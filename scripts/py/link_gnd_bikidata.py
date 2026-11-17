#!/usr/bin/env python3
# Purpose:      Step 2 of Werk linking: link title spans from step 1 to GND Works,
#               with GND queried in-process through bikidata.
# Usage:        cd /data/GND && nice -n 10 uv run --with 'rapidfuzz>=3' python \
#                   <repo>/scripts/py/link_gnd_bikidata.py \
#                   --input data/processed/werk_spans.jsonl \
#                   --output data/processed/werk_links.csv \
#                   --db-path /data/GND/bikidata.duckdb \
#                   [--decision baseline|score] [--limit N] [--chunk-size 1000] [--restart] [--keep-going] [--dry-run]
#               Resumes by default. Rerunning a finished run does nothing.
# Inputs:       --input JSONL, one record per line:
#                   {"obj_id": "...", "title_spans": ["Faust, 2. Theil"], "persons": ["Goethe"],
#                    "ddb_creators": [{"label": "Goethe, Johann Wolfgang von",
#                                      "uri": "http://d-nb.info/gnd/118540238"}],
#                    "ddb_contributors": [], "type_group": "work"}
#               Only obj_id is required. Other fields are ignored. type_group, if present, is
#               one of the linked type groups and limits the GND classes searched.
#               --db-path bikidata DuckDB file with GND and its full-text index
#               (default: env BIKIDATA_DB).
# Outputs:      --output CSV, one row per input record (columns: OUTPUT_COLUMNS).
#               <output>.parts/ keeps run.json, manifest.jsonl, part files, failed.txt and
#               terms.sqlite, the term table: each search term is looked up once per run and
#               kept across chunks and resumed runs.
#               status: linked, ambiguous, below_threshold, author_unconfirmed (baseline only),
#               no_candidate, no_title, author_conflict, generic_title.
#               Exit codes: 0 success, 1 failure, 2 usage error, 130 interrupted.
# Dependencies: rapidfuzz >= 3; bikidata >= 0.5 (not needed for --dry-run); werk_link_rules.py in
#               the same folder, which holds the shared rules.
# Assumptions:  --decision baseline (default): link only on an exact normalised
#               title match with author_match yes and one qualifying Work; --min-score is then
#               unused. --decision score: the earlier rule, title score >= --min-score.
#               --min-score and --name-threshold are starting values, not calibrated.
#               Titles are compared after the historical spelling rules; a Work's
#               creators are the GND creator roles; dc_contributor stands in for a
#               missing dc_creator. Contributor roles are not filtered.
#               Peak memory is logged and warned about, not enforced: bikidata does not
#               expose DuckDB's memory_limit.

from __future__ import annotations

import argparse
import csv
import itertools
import json
import logging
import os
import re
import shutil
import signal
import sqlite3
import sys
import time
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
# The shared rules, moved unchanged from this script; re-exported here, so that
# this script's names and tests stay as they were.
from werk_link_rules import (  # noqa: F401
    AUTHOR_RANK,
    CREATOR_PREDICATES,
    DECISIONS,
    DEFAULT_CHUNK_SIZE,
    DEFAULT_DECISION,
    DEFAULT_MIN_SCORE,
    DEFAULT_NAME_THRESHOLD,
    DEFAULT_SEARCH_SIZE,
    DRY_RUN_RECORDS,
    GENERIC_TITLE_STATUSES,
    GND_ID_RE,
    GNDO,
    GROUP_CLASSES,
    HISTORICAL_RULES,
    MAX_TIED_URIS,
    MIN_FREE_BYTES,
    MIN_TERM_LENGTH,
    NAME_STOPWORDS,
    UMLAUTS,
    Candidate,
    LookupFn,
    TermResult,
    Work,
    any_name_matches,
    check_host,
    ddb_authors,
    decide,
    decide_baseline,
    die,
    gnd_id,
    historical_spelling,
    keep_classes,
    load_done_chunks,
    log_peak_memory,
    names_match,
    normalize_title,
    normalize_tokens,
    part_name,
    read_chunks,
    read_records,
    search_term,
    split_name,
    title_score,
    token_similarity,
    write_atomic,
)

logger = logging.getLogger("link_gnd_bikidata")

WORK_NAME_PREDICATES = (f"<{GNDO}preferredNameForTheWork>", f"<{GNDO}variantNameForTheWork>")
REQUIRED_PREDICATES = (*WORK_NAME_PREDICATES, f"<{GNDO}firstAuthor>")
PERSON_NAME_PATH = "gnd:preferredNameForThePerson|gnd:variantNameForThePerson"
CREATOR_PATH = "|".join(f"gnd:{name}" for name in CREATOR_PREDICATES)
RDF_TYPE = "<http://www.w3.org/1999/02/22-rdf-syntax-ns#type>"

OUTPUT_COLUMNS = [
    "obj_id",
    "status",
    "gnd_uri",
    "matched_name",
    "score",
    "author_match",
    "search_terms",
    "candidate_count",
    "truncated",
    "tied_uris",
    "author_source",
    "type_group",
]

DEFAULT_MAX_MEMORY_MB = 1024
VALUES_BATCH = 200

SparqlFn = Callable[[str], "dict[str, Any]"]
FtsFn = Callable[[str, str, int], "list[tuple[str, float]]"]


@dataclass
class Backend:
    fts: FtsFn
    sparql: SparqlFn


# --- Author rule ---


def author_check(work: Work, record: dict[str, Any], threshold: float) -> str:
    """Apply the author rule: yes, no (removes the candidate), pass1_yes, pass1_no or unknown."""
    creators, _ = ddb_authors(record)
    creator_ids = {gnd_id(c.get("uri") or "") for c in creators} - {None}
    # A role such as "(Fotograf)" after a contributor's name is not part of the name.
    literals = [
        re.sub(r"\s*\([^)]*\)\s*$", "", c["label"])
        for c in creators
        if c.get("label") and not gnd_id(c.get("uri") or "")
    ]
    persons = [p for p in record.get("persons") or [] if p and p.strip()]
    author_names = [name for names in work.authors.values() for name in names]

    if creator_ids & set(work.authors):
        return "yes"
    if any_name_matches(literals, author_names, threshold):
        return "yes"
    if (creator_ids and work.authors) or (literals and author_names):
        return "no"
    if not persons or not author_names:
        return "unknown"
    return "pass1_yes" if any_name_matches(persons, author_names, threshold) else "pass1_no"


# --- Candidate retrieval ---


def fts_query(term: str) -> str:
    """The Tantivy query: a token with a historical spelling also searches its modern form."""
    parts = [f"({t} OR {historical_spelling(t)})" if historical_spelling(t) != t else t for t in term.split()]
    return " ".join(parts)


def iri_values(ids: Iterable[str]) -> str:
    return " ".join(f"<https://d-nb.info/gnd/{value}>" for value in ids)


def batches(items: Sequence[str], size: int) -> Iterator[Sequence[str]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def fetch_pairs(backend: Backend, ids: Sequence[str], pattern: str) -> Iterator[tuple[str, str]]:
    """Run pattern over ?key for each ID, yielding (key ID, ?value) in a repeatable order."""
    for batch in batches(ids, VALUES_BATCH):
        query = (
            f"PREFIX gnd: <{GNDO}>\nSELECT ?key ?value WHERE {{ VALUES ?key {{ {iri_values(batch)} }} {pattern} }} "
            "ORDER BY ?key ?value"
        )
        for binding in backend.sparql(query)["results"]["bindings"]:
            key = gnd_id(binding["key"]["value"])
            if key:
                yield key, binding["value"]["value"]


def fetch_works(backend: Backend, term: str, search_size: int) -> TermResult:
    """Candidate Works for one search term, with their names and authors."""
    query = fts_query(term)
    work_ids: set[str] = set()
    truncated = False
    for predicate in WORK_NAME_PREDICATES:
        hits = backend.fts(query, predicate, search_size)
        truncated = truncated or len(hits) >= search_size
        work_ids.update(i for i in (gnd_id(uri) for uri, _ in hits) if i)
    ids = sorted(work_ids)
    works = {i: Work(uri=f"https://d-nb.info/gnd/{i}") for i in ids}

    names_pattern = "{ ?key gnd:preferredNameForTheWork ?value } UNION { ?key gnd:variantNameForTheWork ?value }"
    for key, name in fetch_pairs(backend, ids, names_pattern):
        works[key].names.append(name)

    for key, value in fetch_pairs(backend, ids, f"?key {RDF_TYPE} ?value"):
        if value.startswith(GNDO):
            works[key].classes.append(value[len(GNDO) :])

    author_ids_by_work = list(fetch_pairs(backend, ids, f"?key {CREATOR_PATH} ?value"))
    author_ids = sorted({i for i in (gnd_id(value) for _, value in author_ids_by_work) if i})
    names_by_author: dict[str, list[str]] = {}
    for key, name in fetch_pairs(backend, author_ids, f"?key {PERSON_NAME_PATH} ?value"):
        names_by_author.setdefault(key, []).append(name)
    for key, value in author_ids_by_work:
        author = gnd_id(value)
        if author:
            works[key].authors[author] = names_by_author.get(author, [])
    return TermResult(works=[works[i] for i in ids], truncated=truncated)


def result_to_json(result: TermResult) -> str:
    works = [{"uri": w.uri, "names": w.names, "authors": w.authors, "classes": w.classes} for w in result.works]
    return json.dumps({"truncated": result.truncated, "works": works}, ensure_ascii=False)


def result_from_json(text: str) -> TermResult:
    data = json.loads(text)
    works = [Work(uri=w["uri"], names=w["names"], authors=w["authors"], classes=w["classes"]) for w in data["works"]]
    return TermResult(works=works, truncated=data["truncated"])


def make_lookup(backend: Backend, search_size: int, table_path: Path | None = None) -> LookupFn:
    """Lookup through the term table: each term is searched once, then read from disk.

    Each term is committed as soon as it is fetched, so an interrupted run keeps its lookups.
    Without table_path the table is kept in memory, for tests.
    """
    table = sqlite3.connect(":memory:" if table_path is None else str(table_path))
    table.execute("CREATE TABLE IF NOT EXISTS terms (term TEXT PRIMARY KEY, result TEXT NOT NULL)")
    table.commit()

    def lookup(term: str) -> TermResult:
        row = table.execute("SELECT result FROM terms WHERE term = ?", (term,)).fetchone()
        if row:
            return result_from_json(row[0])
        result = fetch_works(backend, term, search_size)
        table.execute("INSERT INTO terms VALUES (?, ?)", (term, result_to_json(result)))
        table.commit()
        return result

    return lookup


# --- Linking one record ---


def score_candidates(
    span_results: list[tuple[str, TermResult]], record: dict[str, Any], threshold: float
) -> tuple[list[Candidate], int]:
    """One Candidate per Work kept by the author rule, and the number of Works it removed."""
    best: dict[str, tuple[float, str, Work]] = {}
    exact: set[str] = set()
    for span, result in span_results:
        span_key = normalize_title(span)
        for work in result.works:
            for name in work.names:
                score = title_score(span, name)
                if span_key and normalize_title(name) == span_key:
                    exact.add(work.uri)
                if work.uri not in best or score > best[work.uri][0]:
                    best[work.uri] = (score, name, work)

    candidates = []
    removed = 0
    for uri, (score, name, work) in best.items():
        match = author_check(work, record, threshold)
        if match == "no":
            removed += 1
        else:
            candidates.append(Candidate(uri, name, score, match, uri in exact))
    return candidates, removed


def link_record(
    record: dict[str, Any], lookup: LookupFn, min_score: float, name_threshold: float, decision: str = DEFAULT_DECISION
) -> dict[str, Any]:
    """Link one step 1 record and return its output row."""
    row: dict[str, Any] = dict.fromkeys(OUTPUT_COLUMNS, "")
    row.update(obj_id=record["obj_id"], candidate_count=0, truncated=False, type_group=record.get("type_group") or "")
    allowed = GROUP_CLASSES.get(record.get("type_group") or "")
    spans = [s for s in record.get("title_spans") or [] if s and s.strip()]
    if not spans:
        row["status"] = "no_title"
        return row

    span_results = []
    terms = []
    for span in spans:
        term = search_term(span)
        if term:
            terms.append(term)
            span_results.append((span, keep_classes(lookup(term), allowed)))

    candidates, removed = score_candidates(span_results, record, name_threshold)
    if decision == "baseline":
        status, top, tied = decide_baseline(candidates, removed)
    else:
        status, top, tied = decide(candidates, removed, min_score)
    truncated = any(result.truncated for _, result in span_results)
    if truncated and status in GENERIC_TITLE_STATUSES:
        status = "generic_title"
    row.update(
        status=status,
        search_terms=" | ".join(terms),
        candidate_count=len(candidates) + removed,
        truncated=truncated,
        tied_uris=" ".join(tied),
    )
    if top is not None:
        row.update(matched_name=top.name, score=round(top.score, 3), author_match=top.author_match)
        if top.author_match == "yes":
            row["author_source"] = ddb_authors(record)[1]
        elif top.author_match.startswith("pass1"):
            row["author_source"] = "step1"
    if status == "linked" and top is not None:
        row["gnd_uri"] = top.uri
    return row


# --- Pre-flight ---


def load_backend(db_path: str | None) -> Backend:
    """Open bikidata on the database."""
    if not db_path:
        die("no database given; pass --db-path or set BIKIDATA_DB")
    if not Path(db_path).is_file():
        die(f"database {db_path} not found; run this on the host that holds it")
    os.environ["BIKIDATA_DB"] = db_path
    try:
        import bikidata
    except ImportError:
        die("bikidata is not installed; run from /data/GND with: uv run python ...")

    def fts(query: str, prop: str, size: int) -> list[tuple[str, float]]:
        return list(bikidata.fts_search(query, prop=prop, size=size, db_path=db_path))

    def sparql(query: str) -> dict[str, Any]:
        result: dict[str, Any] = bikidata.sparql(query, db_path=db_path)
        return result

    return Backend(fts=fts, sparql=sparql)


def check_backend(backend: Backend) -> None:
    """Check that the GND predicates exist, and report whether person names are loaded."""
    for predicate in REQUIRED_PREDICATES:
        probe = backend.sparql(f"SELECT ?s WHERE {{ ?s {predicate} ?o }} LIMIT 1")
        if not probe["results"]["bindings"]:
            die(f"database has no {predicate}; is GND loaded in it?")
    probe = backend.sparql(f"SELECT ?s WHERE {{ ?s <{GNDO}preferredNameForThePerson> ?o }} LIMIT 1")
    if probe["results"]["bindings"]:
        logger.info("Person names are loaded")
    else:
        logger.warning("No person names loaded: literal author names will stay unknown")


# --- Resumable run state ---


def run_settings(args: argparse.Namespace, total: int) -> dict[str, Any]:
    stat = args.input.stat()
    return {
        "input_size": stat.st_size,
        "input_mtime": stat.st_mtime,
        "records": total,
        "limit": args.limit,
        "chunk_size": args.chunk_size,
        "decision": args.decision,
        "min_score": args.min_score,
        "name_threshold": args.name_threshold,
        "search_size": args.search_size,
    }


def prepare_parts(parts_dir: Path, settings: dict[str, Any]) -> None:
    """Create the parts folder, or check that it belongs to this same run."""
    state_path = parts_dir / "run.json"
    if not state_path.exists():
        parts_dir.mkdir(parents=True, exist_ok=True)
        write_atomic(state_path, json.dumps(settings, indent=2))
        return
    if json.loads(state_path.read_text(encoding="utf-8")) != settings:
        die(f"input or settings changed since the run in {parts_dir}; pass --restart to start over")


def process_chunk(
    chunk: int, records: list[dict[str, Any]], parts_dir: Path, lookup: LookupFn, args: argparse.Namespace
) -> Counter[str]:
    """Write one chunk to .tmp, rename it when finished, then record it in the manifest."""
    final = parts_dir / part_name(chunk)
    temp = final.with_name(final.name + ".tmp")
    statuses: Counter[str] = Counter()
    try:
        with open(temp, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS, lineterminator="\n")
            for record in records:
                row = link_record(record, lookup, args.min_score, args.name_threshold, args.decision)
                writer.writerow(row)
                statuses[row["status"]] += 1
        os.replace(temp, final)
    finally:
        temp.unlink(missing_ok=True)
    entry = {"chunk": chunk, "rows": len(records), "finished_at": datetime.now(timezone.utc).isoformat()}  # noqa: UP017 (3.9-compatible)
    with open(parts_dir / "manifest.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    return statuses


def merge_parts(parts_dir: Path, chunk_count: int, total: int, output: Path) -> None:
    """Merge the parts atomically, checking row count and that each obj_id appears once."""
    temp = output.with_name(output.name + ".tmp")
    seen_path = parts_dir / "seen.sqlite"
    seen_path.unlink(missing_ok=True)
    # On disk, because the set of seen IDs grows with the input.
    seen = sqlite3.connect(seen_path)
    seen.execute("CREATE TABLE ids (obj_id TEXT PRIMARY KEY)")
    rows = 0
    try:
        with open(temp, "w", encoding="utf-8", newline="") as out:
            writer = csv.writer(out, lineterminator="\n")
            writer.writerow(OUTPUT_COLUMNS)
            for chunk in range(chunk_count):
                with open(parts_dir / part_name(chunk), encoding="utf-8", newline="") as part:
                    for row in csv.reader(part):
                        try:
                            seen.execute("INSERT INTO ids VALUES (?)", (row[0],))
                        except sqlite3.IntegrityError:
                            die(f"obj_id {row[0]} appears twice in the input; fix the step 1 output, then --restart")
                        writer.writerow(row)
                        rows += 1
        if rows != total:
            die(f"merged {rows} rows but expected {total}; check {parts_dir}, or pass --restart")
        os.replace(temp, output)
    finally:
        seen.close()
        seen_path.unlink(missing_ok=True)
        temp.unlink(missing_ok=True)


def run(args: argparse.Namespace, backend: Backend, total: int, parts_dir: Path) -> int:
    """Process every chunk not yet done, then merge. Returns the exit code."""
    done = load_done_chunks(parts_dir)
    chunk_count = (total + args.chunk_size - 1) // args.chunk_size
    failed_path = parts_dir / "failed.txt"
    failed_path.unlink(missing_ok=True)
    lookup = make_lookup(backend, args.search_size, parts_dir / "terms.sqlite")
    statuses: Counter[str] = Counter()
    failed = []
    last_done = max(done, default=None)
    processed = 0
    started = time.monotonic()
    logger.info("%d records in %d chunks, %d already done", total, chunk_count, len(done))

    try:
        for chunk, records in enumerate(read_chunks(args.input, args.chunk_size, args.limit)):
            if chunk in done:
                continue
            try:
                statuses += process_chunk(chunk, records, parts_dir, lookup, args)
            except Exception as e:
                if not args.keep_going:
                    raise
                logger.error("Chunk %d failed: %s", chunk, e)
                failed.append(chunk)
                with open(failed_path, "a", encoding="utf-8") as f:
                    f.write(f"{chunk}\n")
                continue
            last_done = chunk
            processed += len(records)
            elapsed = time.monotonic() - started
            remaining = (chunk_count - len(done) - len(failed)) * args.chunk_size - processed
            logger.info(
                "Chunk %d done, %d records in %.0f s, about %.0f s left: %s",
                chunk,
                processed,
                elapsed,
                max(remaining, 0) * elapsed / processed,
                dict(statuses),
            )
    except KeyboardInterrupt:
        logger.warning("Interrupted; last finished chunk: %s. Rerun the same command to resume.", last_done)
        return 130
    finally:
        log_peak_memory(args.max_memory_mb)

    if failed:
        logger.error("%d chunks failed, listed in %s; rerun to retry them", len(failed), failed_path)
        return 1
    merge_parts(parts_dir, chunk_count, total, args.output)
    logger.info("Wrote %d rows -> %s", total, args.output)
    return 0


def dry_run(args: argparse.Namespace) -> None:
    """Print the searches for the first records, without touching a database."""
    for record in itertools.islice(read_records(args.input), args.limit or DRY_RUN_RECORDS):
        for span in record.get("title_spans") or []:
            term = search_term(span) if span else None
            print(f"{record['obj_id']}\t{span!r}\t{fts_query(term) if term else '(no usable search term)'}")


def handle_sigterm(_signum: int, _frame: object) -> None:
    raise KeyboardInterrupt


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Link title spans from step 1 to GND Works through bikidata.")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--db-path", default=os.environ.get("BIKIDATA_DB"))
    parser.add_argument("--limit", type=int, help="process only the first N records")
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    parser.add_argument("--decision", choices=DECISIONS, default=DEFAULT_DECISION,
                        help="baseline: exact title and agreeing author (default); score: title score >= --min-score")
    parser.add_argument("--min-score", type=float, default=DEFAULT_MIN_SCORE)
    parser.add_argument("--name-threshold", type=float, default=DEFAULT_NAME_THRESHOLD)
    parser.add_argument("--search-size", type=int, default=DEFAULT_SEARCH_SIZE)
    parser.add_argument("--max-memory-mb", type=int, default=DEFAULT_MAX_MEMORY_MB)
    parser.add_argument("--keep-going", action="store_true", help="log failed chunks and continue")
    parser.add_argument("--restart", action="store_true", help="delete this run's parts and output, then start over")
    parser.add_argument("--dry-run", action="store_true", help="print searches; no database, no output")
    args = parser.parse_args()
    for name in ("limit", "chunk_size", "search_size", "max_memory_mb"):
        value = getattr(args, name)
        if value is not None and value < 1:
            parser.error(f"--{name.replace('_', '-')} must be at least 1")
    for name in ("min_score", "name_threshold"):
        if not 0 <= getattr(args, name) <= 1:
            parser.error(f"--{name.replace('_', '-')} must be between 0 and 1")
    return args


def main() -> int:
    args = parse_args()
    if not args.input.is_file():
        die(f"input {args.input} not found; run step 1 first or check the path")
    if args.dry_run:
        dry_run(args)
        return 0

    parts_dir = args.output.with_name(args.output.name + ".parts")
    output_dir = args.output.resolve().parent
    if not output_dir.is_dir() or not os.access(output_dir, os.W_OK):
        die(f"output folder {output_dir} is missing or not writable; create it or choose another --output")
    if args.restart:
        shutil.rmtree(parts_dir, ignore_errors=True)
        args.output.unlink(missing_ok=True)
    elif args.output.exists() and not parts_dir.exists():
        die(f"output {args.output} exists but is not from this script's run; move it, or pass --restart to replace it")

    total = sum(1 for _ in itertools.islice(read_records(args.input), args.limit))
    settings = run_settings(args, total)
    prepare_parts(parts_dir, settings)
    if args.output.exists():
        logger.info("Run already finished: %s", args.output)
        return 0

    check_host(output_dir, args.max_memory_mb)
    backend = load_backend(args.db_path)
    check_backend(backend)
    signal.signal(signal.SIGTERM, handle_sigterm)
    return run(args, backend, total, parts_dir)


if __name__ == "__main__":
    sys.exit(main())
