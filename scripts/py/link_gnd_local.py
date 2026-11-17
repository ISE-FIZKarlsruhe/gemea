#!/usr/bin/env python3
# Purpose:      Step 2 of Werk linking: link title spans from step 1 to GND Works, with GND read
#               from the local Parquet tables of gnd_jsonld_to_parquet.py.
# Usage:        nice -n 10 uv run --no-project --python 3.12 --with pyarrow==21.0.0 --with 'rapidfuzz>=3' \
#                   python scripts/py/link_gnd_local.py \
#                   --input data/processed/nerl-gnd/werk_spans.jsonl \
#                   --output data/processed/nerl-gnd/werk_links.csv \
#                   [--gnd-dir data/gnd/parquet] [--decision baseline|score] [--limit N]
#                   [--chunk-size 1000] [--run-id ID] [--restart] [--keep-going] [--allow-dirty] [--dry-run]
#               Resumes by default. Rerunning a finished run does nothing.
# Inputs:       --input JSONL from extract_werk_spans.py, one record per line: obj_id, type_group,
#                   title_spans, span_sources, persons, ddb_creators, ddb_contributors. Only obj_id is
#                   required; span_sources, if present, must be as long as title_spans.
#               --gnd-dir the four tables and manifest.json; each table's SHA-256 must equal the
#                   manifest's.
# Outputs:      --output CSV, one row per input record (OUTPUT_COLUMNS: the twelve columns of
#                   link_gnd_bikidata.py plus matched_span and matched_span_source).
#               <stem>_status_by_group.csv: records per type_group and status, and how many are
#                   high confidence (linked, score 1.0, author_match yes).
#               <stem>_term_timing.csv: per search term the records using it, its candidate Works,
#                   names scored, truncated, and the seconds spent looking it up and scoring it.
#               <output>.parts/ keeps run.json (commit, checksums, settings; the
#                   output's SHA-256, rows and status counts once finished), manifest.jsonl, part
#                   files, failed.txt, terms.sqlite (the term table and the
#                   per-chunk term statistics) and run-id.json (run ID and UTC start;
#                   the parts of one split input share --run-id, which merge_link_parts.py checks).
#               Exit codes: 0 success, 1 failure, 2 usage error, 130 interrupted.
# Dependencies: pyarrow, rapidfuzz >= 3; werk_link_rules.py in the same folder.
# Assumptions:  The rules are those of werk_link_rules.py and the bikidata linker, except the author rule: a corporate
#               creator matches a name only when their normalize_tokens are equal.
#               A candidate is a Work with one name holding every token of the search term, both
#               through normalize_title; all are kept, truncated above --search-size Works.
#               The tables are held in memory (about 1 GB on the 2026-10-13 dumps).
#               Stops on uncommitted changes to this script or werk_link_rules.py unless
#               --allow-dirty, which run.json records.

from __future__ import annotations

import argparse
import csv
import functools
import hashlib
import itertools
import json
import logging
import os
import platform
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import rapidfuzz

sys.path.insert(0, str(Path(__file__).resolve().parent))
import werk_link_rules as rules
from werk_link_rules import (
    DECISIONS,
    DEFAULT_CHUNK_SIZE,
    DEFAULT_DECISION,
    DEFAULT_MIN_SCORE,
    DEFAULT_NAME_THRESHOLD,
    DEFAULT_SEARCH_SIZE,
    DRY_RUN_RECORDS,
    GENERIC_TITLE_STATUSES,
    GROUP_CLASSES,
    TermResult,
    Work,
    any_name_matches,
    check_host,
    ddb_authors,
    decide,
    decide_baseline,
    die,
    gnd_id,
    keep_classes,
    load_done_chunks,
    log_peak_memory,
    normalize_title,
    normalize_tokens,
    part_name,
    read_records,
    run_id_arg,
    run_identity,
    search_term,
    write_atomic,
)

logger = logging.getLogger("link_gnd_local")

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GND_DIR = ROOT / "data" / "gnd" / "parquet"
# This script and the module whose rules it imports; a run stops if either has uncommitted changes.
CODE_FILES = ("scripts/py/link_gnd_local.py", "scripts/py/werk_link_rules.py")
GND_BASE = "https://d-nb.info/gnd/"
# Table -> the columns the linker reads.
TABLES = {
    "gnd_work_names.parquet": ["gnd_id", "name"],
    "gnd_work_classes.parquet": ["gnd_id", "class"],
    "gnd_work_creators.parquet": ["gnd_id", "creator_id"],
    "gnd_creator_names.parquet": ["gnd_id", "entity", "name"],
}
# The twelve columns of link_gnd_bikidata.py, then the matched span and its source.
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
    "matched_span",
    "matched_span_source",
]
SUMMARY_COLUMNS = ["type_group", "status", "records", "high_confidence"]
TIMING_COLUMNS = ["term", "records", "candidates", "names_scored", "truncated", "lookup_seconds", "score_seconds"]
DEFAULT_MAX_MEMORY_MB = 4096
# Recently used terms are kept parsed in memory, so a common term is not read back from disk per record.
TERM_CACHE_SIZE = 256
READ_BATCH_ROWS = 100_000


@dataclass
class LocalWork(Work):
    # Creator GND ID -> "person" or "corporate"; "" for a creator without a record.
    entities: dict[str, str] = field(default_factory=dict)


@dataclass
class Candidate(rules.Candidate):
    # The span that gave the best name, and its source in step 1.
    span: str = ""
    span_source: str = ""


@dataclass
class TermStats:
    records: int = 0
    names_scored: int = 0
    score_seconds: float = 0.0


@dataclass
class ChunkStats:
    lookup_seconds: float = 0.0
    score_seconds: float = 0.0
    terms: dict[str, TermStats] = field(default_factory=dict)

    def term(self, term: str) -> TermStats:
        return self.terms.setdefault(term, TermStats())


LookupFn = rules.LookupFn


# --- GND tables and index ---


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def check_tables(gnd_dir: Path) -> dict[str, Any]:
    """Check each table against manifest.json; return the dump entries and table SHA-256 for run.json."""
    manifest_path = gnd_dir / "manifest.json"
    if not manifest_path.is_file():
        die(f"{manifest_path} not found; pass the folder gnd_jsonld_to_parquet.py wrote with --gnd-dir")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    checksums = {}
    for name, columns in TABLES.items():
        path = gnd_dir / name
        if not path.is_file():
            die(f"{path} not found; copy it from the GPU host or rerun gnd_jsonld_to_parquet.py")
        recorded = manifest.get("tables", {}).get(name, {}).get("sha256")
        actual = sha256(path)
        if actual != recorded:
            die(f"{path} has SHA-256 {actual[:12]}…, the manifest {str(recorded)[:12]}…; copy the table again")
        missing = [c for c in columns if c not in pq.ParquetFile(path).schema_arrow.names]
        if missing:
            die(f"{path} has no column {', '.join(missing)}; rebuild it with the current gnd_jsonld_to_parquet.py")
        checksums[name] = actual
    return {"dumps": manifest.get("dumps", {}), "tables": checksums}


def iter_rows(path: Path, columns: list[str]) -> Iterator[tuple[str, ...]]:
    """Stream the rows of a Parquet table, a batch at a time."""
    for batch in pq.ParquetFile(path).iter_batches(batch_size=READ_BATCH_ROWS, columns=columns):
        yield from zip(*(batch.column(c).to_pylist() for c in columns))


class GndIndex:
    """The GND tables in memory: Work names with an inverted token index, classes and creators."""

    def __init__(self) -> None:
        self.work_names: dict[str, list[str]] = defaultdict(list)
        self.work_classes: dict[str, list[str]] = defaultdict(list)
        self.work_creators: dict[str, list[str]] = defaultdict(list)
        self.creator_names: dict[str, list[str]] = defaultdict(list)
        self.creator_entity: dict[str, str] = {}
        # Name number -> its Work; token -> the numbers of the names holding it.
        self.name_work: list[str] = []
        self.postings: dict[str, list[int]] = defaultdict(list)

    def add_name(self, work_id: str, name: str) -> None:
        self.work_names[work_id].append(name)
        tokens = set(normalize_title(name))
        if not tokens:
            return
        number = len(self.name_work)
        self.name_work.append(work_id)
        for token in tokens:
            self.postings[token].append(number)

    def candidate_ids(self, term: str) -> list[str]:
        """The Works with one name holding every token of the term, in ID order."""
        tokens = set(normalize_title(term))
        if not tokens:
            return []
        # Start from the rarest token, so the set stays small.
        postings = sorted((self.postings.get(t, []) for t in tokens), key=len)
        names = set(postings[0])
        for other in postings[1:]:
            if not names:
                break
            names.intersection_update(other)
        return sorted({self.name_work[n] for n in names})

    def work(self, work_id: str) -> LocalWork:
        creators = sorted(set(self.work_creators.get(work_id, [])))
        return LocalWork(
            uri=GND_BASE + work_id,
            names=sorted(self.work_names.get(work_id, [])),
            authors={c: sorted(self.creator_names.get(c, [])) for c in creators},
            classes=sorted(set(self.work_classes.get(work_id, []))),
            entities={c: self.creator_entity.get(c, "") for c in creators},
        )

    def fetch(self, term: str, search_size: int) -> TermResult:
        """All candidate Works of a term; truncated above search_size Works."""
        ids = self.candidate_ids(term)
        return TermResult(works=[self.work(i) for i in ids], truncated=len(ids) > search_size)


def load_index(gnd_dir: Path) -> GndIndex:
    started = time.monotonic()
    index = GndIndex()
    for work_id, name in iter_rows(gnd_dir / "gnd_work_names.parquet", TABLES["gnd_work_names.parquet"]):
        if name:
            index.add_name(work_id, name)
    for work_id, gnd_class in iter_rows(gnd_dir / "gnd_work_classes.parquet", TABLES["gnd_work_classes.parquet"]):
        index.work_classes[work_id].append(gnd_class)
    for work_id, creator in iter_rows(gnd_dir / "gnd_work_creators.parquet", TABLES["gnd_work_creators.parquet"]):
        index.work_creators[work_id].append(creator)
    for creator, entity, name in iter_rows(gnd_dir / "gnd_creator_names.parquet", TABLES["gnd_creator_names.parquet"]):
        index.creator_entity[creator] = entity
        if name:
            index.creator_names[creator].append(name)
    logger.info(
        "Indexed %d Work names of %d Works (%d tokens), %d creators in %.0f s",
        len(index.name_work), len(index.work_names), len(index.postings), len(index.creator_entity),
        time.monotonic() - started,
    )  # fmt: skip
    return index


# --- Term table ---


def result_to_json(result: TermResult) -> str:
    works = [
        {"uri": w.uri, "names": w.names, "authors": w.authors, "classes": w.classes,
         "entities": w.entities if isinstance(w, LocalWork) else {}}
        for w in result.works
    ]  # fmt: skip
    return json.dumps({"truncated": result.truncated, "works": works}, ensure_ascii=False)


def result_from_json(text: str) -> TermResult:
    data = json.loads(text)
    works: list[Work] = [
        LocalWork(uri=w["uri"], names=w["names"], authors=w["authors"], classes=w["classes"], entities=w["entities"])
        for w in data["works"]
    ]
    return TermResult(works=works, truncated=data["truncated"])


class TermTable:
    """terms.sqlite: each term is looked up once per run; plus per-chunk term statistics."""

    def __init__(self, path: Path | str, index: GndIndex, search_size: int) -> None:
        self.db = sqlite3.connect(str(path))
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS terms (term TEXT PRIMARY KEY, result TEXT NOT NULL, "
            "works INTEGER NOT NULL, truncated INTEGER NOT NULL, lookup_seconds REAL NOT NULL)"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS stats (chunk INTEGER NOT NULL, term TEXT NOT NULL, records INTEGER NOT NULL, "
            "names_scored INTEGER NOT NULL, score_seconds REAL NOT NULL)"
        )
        self.db.commit()
        self.index = index
        self.search_size = search_size
        self.lookup: LookupFn = functools.lru_cache(maxsize=TERM_CACHE_SIZE)(self._lookup)

    def _lookup(self, term: str) -> TermResult:
        row = self.db.execute("SELECT result FROM terms WHERE term = ?", (term,)).fetchone()
        if row:
            return result_from_json(row[0])
        started = time.perf_counter()
        result = self.index.fetch(term, self.search_size)
        seconds = time.perf_counter() - started
        self.db.execute(
            "INSERT INTO terms VALUES (?, ?, ?, ?, ?)",
            (term, result_to_json(result), len(result.works), result.truncated, seconds),
        )
        self.db.commit()
        return result

    def save_stats(self, chunk: int, stats: ChunkStats) -> None:
        """Replace this chunk's statistics, so a chunk that is redone is not counted twice."""
        self.db.execute("DELETE FROM stats WHERE chunk = ?", (chunk,))
        self.db.executemany(
            "INSERT INTO stats VALUES (?, ?, ?, ?, ?)",
            [(chunk, term, s.records, s.names_scored, s.score_seconds) for term, s in stats.terms.items()],
        )
        self.db.commit()

    def timing_rows(self, chunk_count: int) -> Iterator[tuple[Any, ...]]:
        """One row per term of the run's chunks, the slowest to score first."""
        return self.db.execute(
            "SELECT s.term, SUM(s.records), t.works, SUM(s.names_scored), t.truncated, "
            "ROUND(t.lookup_seconds, 4), ROUND(SUM(s.score_seconds), 4) "
            "FROM stats s JOIN terms t USING (term) WHERE s.chunk < ? "
            "GROUP BY s.term ORDER BY SUM(s.score_seconds) DESC, s.term",
            (chunk_count,),
        )

    def close(self) -> None:
        self.db.close()


# --- Linking one record ---


def span_pairs(record: dict[str, Any]) -> list[tuple[str, str]]:
    """The record's title spans with their span_sources entry; ValueError if the lengths differ."""
    spans = record.get("title_spans") or []
    sources = record.get("span_sources")
    if sources is None:
        sources = [""] * len(spans)
    if len(sources) != len(spans):
        raise ValueError(f"{len(spans)} title_spans but {len(sources)} span_sources")
    return [(span, source or "") for span, source in zip(spans, sources)]


def names_agree(literals: list[str], person_names: list[str], corporate_names: list[str], threshold: float) -> bool:
    """Person names by any_name_matches with the threshold; corporate names only when their tokens are equal."""
    if any_name_matches(literals, person_names, threshold):
        return True
    corporate = {tuple(normalize_tokens(name)) for name in corporate_names} - {()}
    return any(tuple(normalize_tokens(literal)) in corporate for literal in literals)


def author_check(work: Work, record: dict[str, Any], threshold: float) -> str:
    """The author rule of link_gnd_bikidata.author_check, with corporate creators matched exactly."""
    creators, _ = ddb_authors(record)
    creator_ids = {gnd_id(c.get("uri") or "") for c in creators} - {None}
    # A role such as "(Fotograf)" after a contributor's name is not part of the name.
    literals = [
        re.sub(r"\s*\([^)]*\)\s*$", "", c["label"])
        for c in creators
        if c.get("label") and not gnd_id(c.get("uri") or "")
    ]
    persons = [p for p in record.get("persons") or [] if p and p.strip()]
    entities = work.entities if isinstance(work, LocalWork) else {}
    person_names = [n for c, names in work.authors.items() if entities.get(c) != "corporate" for n in names]
    corporate_names = [n for c, names in work.authors.items() if entities.get(c) == "corporate" for n in names]
    any_names = bool(person_names or corporate_names)

    if creator_ids & set(work.authors):
        return "yes"
    if names_agree(literals, person_names, corporate_names, threshold):
        return "yes"
    if (creator_ids and work.authors) or (literals and any_names):
        return "no"
    if not persons or not any_names:
        return "unknown"
    return "pass1_yes" if names_agree(persons, person_names, corporate_names, threshold) else "pass1_no"


def dice(span_tokens: set[str], name_tokens: list[str]) -> float:
    """title_score of werk_link_rules.py, on tokens already normalised."""
    name_set = set(name_tokens)
    if not span_tokens or not name_set:
        return 0.0
    return 2 * len(span_tokens & name_set) / (len(span_tokens) + len(name_set))


def score_candidates(
    span_results: list[tuple[str, str, str, TermResult]],
    record: dict[str, Any],
    threshold: float,
    stats: ChunkStats | None = None,
) -> tuple[list[Candidate], int]:
    """One Candidate per Work kept by the author rule, and the number of Works it removed.

    span_results holds (span, span source, search term, Works). As link_gnd_bikidata.score_candidates,
    plus the span that gave each Work's best name.
    """
    best: dict[str, tuple[float, str, Work, int]] = {}
    exact: set[str] = set()
    for position, (span, _source, term, result) in enumerate(span_results):
        started = time.perf_counter()
        span_key = normalize_title(span)
        span_tokens = set(span_key)
        names = 0
        for work in result.works:
            for name in work.names:
                names += 1
                name_key = normalize_title(name)
                score = dice(span_tokens, name_key)
                if span_key and name_key == span_key:
                    exact.add(work.uri)
                if work.uri not in best or score > best[work.uri][0]:
                    best[work.uri] = (score, name, work, position)
        if stats is not None:
            stats.term(term).names_scored += names
            stats.term(term).score_seconds += time.perf_counter() - started

    candidates = []
    removed = 0
    for uri, (score, name, work, position) in best.items():
        started = time.perf_counter()
        match = author_check(work, record, threshold)
        span, source, term, _ = span_results[position]
        if stats is not None:
            stats.term(term).score_seconds += time.perf_counter() - started
        if match == "no":
            removed += 1
        else:
            candidates.append(Candidate(uri, name, score, match, uri in exact, span, source))
    return candidates, removed


def link_record(
    record: dict[str, Any],
    lookup: LookupFn,
    min_score: float,
    name_threshold: float,
    decision: str = DEFAULT_DECISION,
    stats: ChunkStats | None = None,
) -> dict[str, Any]:
    """Link one step 1 record and return its output row."""
    row: dict[str, Any] = dict.fromkeys(OUTPUT_COLUMNS, "")
    row.update(obj_id=record["obj_id"], candidate_count=0, truncated=False, type_group=record.get("type_group") or "")
    allowed = GROUP_CLASSES.get(record.get("type_group") or "")
    spans = [(span, source) for span, source in span_pairs(record) if span and span.strip()]
    if not spans:
        row["status"] = "no_title"
        return row

    span_results = []
    terms = []
    for span, source in spans:
        term = search_term(span)
        if term:
            terms.append(term)
            started = time.perf_counter()
            result = lookup(term)
            if stats is not None:
                stats.lookup_seconds += time.perf_counter() - started
            span_results.append((span, source, term, keep_classes(result, allowed)))

    started = time.perf_counter()
    candidates, removed = score_candidates(span_results, record, name_threshold, stats)
    if decision == "baseline":
        status, top, tied = decide_baseline(list(candidates), removed)
    else:
        status, top, tied = decide(list(candidates), removed, min_score)
    if stats is not None:
        stats.score_seconds += time.perf_counter() - started
        for term in set(terms):
            stats.term(term).records += 1
    truncated = any(result.truncated for _, _, _, result in span_results)
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
        assert isinstance(top, Candidate)
        row.update(matched_name=top.name, score=round(top.score, 3), author_match=top.author_match)
        row.update(matched_span=top.span, matched_span_source=top.span_source)
        if top.author_match == "yes":
            row["author_source"] = ddb_authors(record)[1]
        elif top.author_match.startswith("pass1"):
            row["author_source"] = "step1"
    if status == "linked" and top is not None:
        row["gnd_uri"] = top.uri
    return row


# --- Input ---


def read_checked_records(input_path: Path) -> Iterator[dict[str, Any]]:
    """read_records of werk_link_rules.py, also stopping on span_sources of the wrong length."""
    for record in read_records(input_path):
        try:
            span_pairs(record)
        except ValueError as e:
            die(f"{input_path}: record {record['obj_id']} has {e}; fix or regenerate the step 1 output")
        yield record


def read_chunks(input_path: Path, chunk_size: int, limit: int | None) -> Iterator[list[dict[str, Any]]]:
    """Stream the records as lists of chunk_size; only one chunk is held in memory."""
    records = itertools.islice(read_checked_records(input_path), limit)
    while True:
        chunk = list(itertools.islice(records, chunk_size))
        if not chunk:
            return
        yield chunk


# --- Run state and provenance ---


def git_state() -> tuple[str, bool]:
    """The gemea commit, and whether this script or werk_link_rules.py has uncommitted changes."""
    try:
        commit = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, check=True)
        status = subprocess.run(
            ["git", "-C", str(ROOT), "status", "--porcelain", "--", *CODE_FILES], capture_output=True, text=True, check=True
        )
    except (OSError, subprocess.CalledProcessError) as e:
        die(f"cannot read the git state of {ROOT} ({e}); run from a gemea checkout")
    return commit.stdout.strip(), bool(status.stdout.strip())


def run_settings(args: argparse.Namespace, total: int, gnd: dict[str, Any], commit: str, dirty: bool) -> dict[str, Any]:
    """What run.json records; a resumed run must match it, the output entry aside."""
    stat = args.input.stat()
    return {
        "backend": "local",
        "script": {
            "commit": commit,
            "uncommitted_changes": dirty,
            "sha256": {name: sha256(ROOT / name) for name in CODE_FILES},
        },
        "input": {"path": str(args.input.resolve()), "sha256": sha256(args.input), "size": stat.st_size, "records": total},
        "gnd": gnd,
        "settings": {
            "limit": args.limit,
            "chunk_size": args.chunk_size,
            "decision": args.decision,
            "min_score": args.min_score,
            "name_threshold": args.name_threshold,
            "search_size": args.search_size,
        },
        "versions": {"python": platform.python_version(), "pyarrow": pa.__version__, "rapidfuzz": rapidfuzz.__version__},
    }


def prepare_parts(parts_dir: Path, settings: dict[str, Any]) -> None:
    """Create the parts folder, or check that it belongs to this same run (another backend or tables stop it)."""
    state_path = parts_dir / "run.json"
    if not state_path.exists():
        parts_dir.mkdir(parents=True, exist_ok=True)
        write_atomic(state_path, json.dumps(settings, indent=2, ensure_ascii=False))
        return
    recorded = json.loads(state_path.read_text(encoding="utf-8"))
    recorded.pop("output", None)
    if recorded != settings:
        die(f"input, code, GND tables or settings differ from {state_path}; pass --restart to start over")


def side_outputs(output: Path) -> tuple[Path, Path]:
    """The status summary and the per-term timing CSV, next to the output."""
    return (
        output.with_name(f"{output.stem}_status_by_group.csv"),
        output.with_name(f"{output.stem}_term_timing.csv"),
    )


# --- Chunks and merge ---


def process_chunk(
    chunk: int, records: list[dict[str, Any]], parts_dir: Path, terms: TermTable, args: argparse.Namespace
) -> tuple[Counter[str], ChunkStats]:
    """Write one chunk to .tmp, rename it when finished, save its term statistics, then record it in the manifest."""
    final = parts_dir / part_name(chunk)
    temp = final.with_name(final.name + ".tmp")
    statuses: Counter[str] = Counter()
    stats = ChunkStats()
    try:
        with open(temp, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS, lineterminator="\n")
            for record in records:
                row = link_record(record, terms.lookup, args.min_score, args.name_threshold, args.decision, stats)
                writer.writerow(row)
                statuses[row["status"]] += 1
        os.replace(temp, final)
    finally:
        temp.unlink(missing_ok=True)
    terms.save_stats(chunk, stats)
    entry = {"chunk": chunk, "rows": len(records), "finished_at": datetime.now(timezone.utc).isoformat()}  # noqa: UP017 (3.9-compatible)
    with open(parts_dir / "manifest.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    return statuses, stats


def write_csv_atomic(path: Path, header: list[str], rows: Iterator[Any] | list[Any]) -> None:
    temp = path.with_name(path.name + ".tmp")
    try:
        with open(temp, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f, lineterminator="\n")
            writer.writerow(header)
            writer.writerows(rows)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def merge_parts(parts_dir: Path, chunk_count: int, total: int, output: Path, terms: TermTable) -> None:
    """Merge the parts, write the summary and timing CSVs, record the output in run.json, then rename the output.

    As link_gnd_bikidata.merge_parts: checks the row count and that each obj_id appears once.
    """
    temp = output.with_name(output.name + ".tmp")
    seen_path = parts_dir / "seen.sqlite"
    seen_path.unlink(missing_ok=True)
    # On disk, because the set of seen IDs grows with the input.
    seen = sqlite3.connect(seen_path)
    seen.execute("CREATE TABLE ids (obj_id TEXT PRIMARY KEY)")
    rows = 0
    groups: Counter[tuple[str, str]] = Counter()
    high: Counter[tuple[str, str]] = Counter()
    try:
        with open(temp, "w", encoding="utf-8", newline="") as out:
            writer = csv.writer(out, lineterminator="\n")
            writer.writerow(OUTPUT_COLUMNS)
            for chunk in range(chunk_count):
                with open(parts_dir / part_name(chunk), encoding="utf-8", newline="") as part:
                    for row in csv.DictReader(part, fieldnames=OUTPUT_COLUMNS):
                        try:
                            seen.execute("INSERT INTO ids VALUES (?)", (row["obj_id"],))
                        except sqlite3.IntegrityError:
                            die(f"obj_id {row['obj_id']} appears twice in the input; fix the step 1 output, then --restart")
                        writer.writerow(row[c] for c in OUTPUT_COLUMNS)
                        rows += 1
                        key = (row["type_group"], row["status"])
                        groups[key] += 1
                        # High confidence: linked, score 1.0 and author_match yes.
                        if row["status"] == "linked" and row["score"] == "1.0" and row["author_match"] == "yes":
                            high[key] += 1
        if rows != total:
            die(f"merged {rows} rows but expected {total}; check {parts_dir}, or pass --restart")
        summary_path, timing_path = side_outputs(output)
        write_csv_atomic(summary_path, SUMMARY_COLUMNS, summary_rows(groups, high))
        write_csv_atomic(timing_path, TIMING_COLUMNS, terms.timing_rows(chunk_count))
        record_output(parts_dir, temp, rows, groups)
        os.replace(temp, output)
    finally:
        seen.close()
        seen_path.unlink(missing_ok=True)
        temp.unlink(missing_ok=True)


def summary_rows(groups: Counter[tuple[str, str]], high: Counter[tuple[str, str]]) -> list[list[Any]]:
    """Records per type group and status, then the totals per status as group "all"."""
    rows: list[list[Any]] = [[g, s, n, high[(g, s)]] for (g, s), n in sorted(groups.items())]
    totals: Counter[str] = Counter()
    high_totals: Counter[str] = Counter()
    for (group, status), count in groups.items():
        totals[status] += count
        high_totals[status] += high[(group, status)]
    rows += [["all", s, n, high_totals[s]] for s, n in sorted(totals.items())]
    return rows


def record_output(parts_dir: Path, output: Path, rows: int, groups: Counter[tuple[str, str]]) -> None:
    """Add the output's SHA-256, row count and status counts to run.json."""
    state_path = parts_dir / "run.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    statuses: Counter[str] = Counter()
    for (_, status), count in groups.items():
        statuses[status] += count
    state["output"] = {
        "sha256": sha256(output),
        "rows": rows,
        "statuses": dict(sorted(statuses.items())),
        "finished_at": datetime.now(timezone.utc).isoformat(),  # noqa: UP017 (3.9-compatible)
    }
    write_atomic(state_path, json.dumps(state, indent=2, ensure_ascii=False))


def run(args: argparse.Namespace, index: GndIndex, total: int, parts_dir: Path) -> int:
    """Process every chunk not yet done, then merge. Returns the exit code."""
    done = load_done_chunks(parts_dir)
    chunk_count = (total + args.chunk_size - 1) // args.chunk_size
    failed_path = parts_dir / "failed.txt"
    failed_path.unlink(missing_ok=True)
    terms = TermTable(parts_dir / "terms.sqlite", index, args.search_size)
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
                chunk_statuses, stats = process_chunk(chunk, records, parts_dir, terms, args)
            except Exception as e:
                if not args.keep_going:
                    raise
                logger.error("Chunk %d failed: %s", chunk, e)
                failed.append(chunk)
                with open(failed_path, "a", encoding="utf-8") as f:
                    f.write(f"{chunk}\n")
                continue
            statuses += chunk_statuses
            last_done = chunk
            processed += len(records)
            elapsed = time.monotonic() - started
            remaining = (chunk_count - len(done) - len(failed)) * args.chunk_size - processed
            logger.info(
                "Chunk %d done, %d records in %.0f s (this chunk: lookups %.1f s, scoring %.1f s), about %.0f s left: %s",
                chunk, processed, elapsed, stats.lookup_seconds, stats.score_seconds,
                max(remaining, 0) * elapsed / processed, dict(statuses),
            )  # fmt: skip
        if failed:
            logger.error("%d chunks failed, listed in %s; rerun to retry them", len(failed), failed_path)
            return 1
        merge_parts(parts_dir, chunk_count, total, args.output, terms)
    except KeyboardInterrupt:
        logger.warning("Interrupted; last finished chunk: %s. Rerun the same command to resume.", last_done)
        return 130
    finally:
        terms.close()
        log_peak_memory(args.max_memory_mb)

    logger.info("Wrote %d rows -> %s, with %s and %s", total, args.output, *(p.name for p in side_outputs(args.output)))
    return 0


def dry_run(args: argparse.Namespace, index: GndIndex) -> None:
    """Print, for the first records, each span's search term, tokens and candidate counts; write nothing."""
    print("obj_id\tspan\tsource\tterm\ttokens\tcandidates\tafter_class_filter\ttruncated")
    for record in itertools.islice(read_checked_records(args.input), args.limit or DRY_RUN_RECORDS):
        allowed = GROUP_CLASSES.get(record.get("type_group") or "")
        for span, source in span_pairs(record):
            term = search_term(span) if span else None
            if not term:
                print(f"{record['obj_id']}\t{span!r}\t{source}\t(no usable search term)")
                continue
            result = index.fetch(term, args.search_size)
            kept = len(keep_classes(result, allowed).works)
            tokens = " ".join(normalize_title(term))
            print(f"{record['obj_id']}\t{span!r}\t{source}\t{term}\t{tokens}\t{len(result.works)}\t{kept}\t{result.truncated}")


def handle_sigterm(_signum: int, _frame: object) -> None:
    raise KeyboardInterrupt


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Link title spans from step 1 to GND Works, from local Parquet tables.")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--gnd-dir", type=Path, default=DEFAULT_GND_DIR, help="folder of the GND tables and manifest.json")
    parser.add_argument("--limit", type=int, help="process only the first N records")
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    parser.add_argument("--decision", choices=DECISIONS, default=DEFAULT_DECISION,
                        help="baseline: exact title and agreeing author (default); score: title score >= --min-score")
    parser.add_argument("--min-score", type=float, default=DEFAULT_MIN_SCORE)
    parser.add_argument("--name-threshold", type=float, default=DEFAULT_NAME_THRESHOLD)
    parser.add_argument("--search-size", type=int, default=DEFAULT_SEARCH_SIZE)
    parser.add_argument("--max-memory-mb", type=int, default=DEFAULT_MAX_MEMORY_MB)
    parser.add_argument("--keep-going", action="store_true", help="log failed chunks and continue")
    parser.add_argument("--run-id", type=run_id_arg, help="run ID (UTC start, 20261110T090205Z), the same for every part; default: this run's start")
    parser.add_argument("--restart", action="store_true", help="delete this run's parts and outputs, then start over")
    parser.add_argument("--allow-dirty", action="store_true", help="run despite uncommitted changes; recorded")
    parser.add_argument("--dry-run", action="store_true", help="print search terms and candidate counts; write nothing")
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
    gnd = check_tables(args.gnd_dir)
    if args.dry_run:
        dry_run(args, load_index(args.gnd_dir))
        return 0
    commit, dirty = git_state()
    if dirty and not args.allow_dirty:
        die("link_gnd_local.py or werk_link_rules.py has uncommitted changes; commit them, or pass --allow-dirty")

    parts_dir = args.output.with_name(args.output.name + ".parts")
    output_dir = args.output.resolve().parent
    if not output_dir.is_dir() or not os.access(output_dir, os.W_OK):
        die(f"output folder {output_dir} is missing or not writable; create it or choose another --output")
    if args.restart:
        shutil.rmtree(parts_dir, ignore_errors=True)
        for path in (args.output, *side_outputs(args.output)):
            path.unlink(missing_ok=True)
    elif args.output.exists() and not parts_dir.exists():
        die(f"output {args.output} exists but is not from this script's run; move it, or pass --restart to replace it")

    total = sum(1 for _ in itertools.islice(read_checked_records(args.input), args.limit))
    prepare_parts(parts_dir, run_settings(args, total, gnd, commit, dirty))
    if args.output.exists():
        logger.info("Run already finished: %s", args.output)
        return 0
    identity = run_identity(parts_dir, args.run_id)
    logger.info("Run %s, started %s", identity["run_id"], identity["started_at"])

    check_host(output_dir, args.max_memory_mb)
    index = load_index(args.gnd_dir)
    signal.signal(signal.SIGTERM, handle_sigterm)
    return run(args, index, total, parts_dir)


if __name__ == "__main__":
    sys.exit(main())
