#!/usr/bin/env python3
# Purpose:      Step 1 of Werk linking: read the DDB records of the linked type groups,
#               find their title spans and names with the ISBD rule, the volume rule and
#               Flair, and write one JSON line per record for step 2 (link_gnd_local.py).
# Usage:        <venv with flair>/bin/python scripts/py/extract_werk_spans.py
#                   [--input PATH] [--output-dir PATH] [--rule-script PATH] [--model NAME]
#                   [--batch-size 16] [--chunk-size 10000] [--shard 0 --num-shards 1]
#                   [--limit N] [--no-flair] [--run-id ID] [--restart] [--allow-dirty] [--dry-run]
#               ... --merge [--output PATH]   after every shard has finished
#               To use two GPUs, run two processes with CUDA_VISIBLE_DEVICES=0 and =1,
#               --num-shards 2 and --shard 0 or 1, on the same --output-dir.
#               Resumes by default. Rerunning a finished shard does nothing.
# Inputs:       --input parquet with obj_id, title, hierarchy_type, dc_type, lang, dc_creator,
#                   dc_contributor (default: data/out/s2/s2_meta_de_content.parquet).
#               --rule-script the bibner-werk ISBD rule, loaded unchanged; its SHA-256 must be
#                   RULE_SHA256 (bibner-werk 050820b; default: scripts/py/t10_rule_baseline.py).
#               --model Flair model (default: flair/ner-english-ontonotes-large).
# Outputs:      --output-dir (default: data/processed/nerl-gnd/werk_spans/): part-NNNNNN.jsonl
#                   per chunk, manifest-shardK.jsonl and run-shardK.json per shard, and run-id.json
#                   (run ID and UTC start, shared by the shards).
#               --merge writes --output (default: data/processed/nerl-gnd/werk_spans.jsonl),
#                   the parts in chunk order. One line per record of a linked type group:
#                   obj_id, title, type_group, htype, dc_type, lang, title_spans, span_sources,
#                   persons, ddb_creators, ddb_contributors, flair.
#               Exit codes: 0 success, 1 failure, 2 usage error, 130 interrupted.
# Dependencies: pyarrow, rapidfuzz (through werk_link_rules.py); flair and torch unless
#               --no-flair or --dry-run.
# Assumptions:  Type groups are those of werk_link_rules.py; contributors
#               stand in for a missing creator. A Flair WORK_OF_ART
#               span is added only when its search term differs from those of the rule's
#               spans. A span of any source whose search term holds only FUNCTION_WORDS (a hand
#               list, no source) is dropped. The volume rule is a first version: tested on the Faust record and a few constructed
#               cases; NOT_VOLUME_TITLES is a hand list without a source.
#               Stops on uncommitted changes to its scripts unless
#               --allow-dirty, which run.json records. run-shardK.json also records the input's
#               SHA-256, and with Flair the model's cache copies (Hugging Face revision, download
#               date) and the Flair and torch versions; the input is hashed at each
#               shard start. The run ID is the UTC start of the first shard unless --run-id.

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any, NoReturn

import pyarrow.parquet as pq  # type: ignore[import-untyped]

sys.path.insert(0, str(Path(__file__).resolve().parent))
from collect_run_provenance import model_caches, package_version
from count_werk_type_pairs import type_group
from flair_work_of_art_probe import Prediction, Tagger, load_tagger
from isbd_probe_goethe_faust import load_rule
from werk_link_rules import (
    GROUP_CLASSES,
    RUN_ID_FILE,
    run_id_arg,
    run_identity,
    search_term,
)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = ROOT / "data" / "out" / "s2" / "s2_meta_de_content.parquet"
DEFAULT_OUTPUT_DIR = ROOT / "data" / "processed" / "nerl-gnd" / "werk_spans"
DEFAULT_MERGED = ROOT / "data" / "processed" / "nerl-gnd" / "werk_spans.jsonl"
DEFAULT_RULE_SCRIPT = ROOT / "scripts" / "py" / "t10_rule_baseline.py"
DEFAULT_MODEL = "flair/ner-english-ontonotes-large"
RULE_SHA256 = "682cef4e092c94f954470f3b0e3a9a56e1792cf5ca525dcfe4a7653e88eab90f"
# This script and the modules it imports; a run stops if any has uncommitted changes.
CODE_FILES = [
    "scripts/py/extract_werk_spans.py",
    "scripts/py/count_werk_type_pairs.py",
    "scripts/py/count_werk_linking_types.py",
    "scripts/py/flair_work_of_art_probe.py",
    "scripts/py/isbd_probe_goethe_faust.py",
    "scripts/py/werk_link_rules.py",
    "scripts/py/collect_run_provenance.py",
]
COLUMNS = ["obj_id", "title", "hierarchy_type", "dc_type", "lang", "dc_creator", "dc_contributor"]
FLAIR_LABELS = ("WORK_OF_ART", "PERSON", "GPE", "DATE", "ORG")
LINKED_GROUPS = set(GROUP_CLASSES)
# A volume number such as "12. " before a capitalised word: the volume's own title follows.
VOLUME_RE = re.compile(r"(?<![\w.])\d+\.\s+(?=[A-ZÄÖÜ])")
# Words that follow a number but start an edition or part statement, not a title.
NOT_VOLUME_TITLES = {
    "aufl", "auflage", "ausg", "ausgabe", "bd", "band", "baende", "bände", "theil", "teil", "abth",
    "abt", "abtheilung", "abteilung", "heft", "lfg", "lieferung", "jg", "jahrgang", "buch", "stück",
}  # fmt: skip
# A span whose search term has only these words is no Work title.
FUNCTION_WORDS = {
    "der", "die", "das", "des", "dem", "den", "ein", "eine", "einer", "eines", "einem", "einen",
    "und", "oder", "von", "vom", "zu", "zum", "zur", "im", "in", "an", "am", "auf", "aus", "mit", "bey",
    "the", "a", "of", "and", "or", "to",
}  # fmt: skip
DEFAULT_BATCH_SIZE = 16
DEFAULT_CHUNK_SIZE = 10_000
DRY_RUN_RECORDS = 3
MIN_FREE_BYTES = 1024**3

logger = logging.getLogger("extract_werk_spans")


def die(message: str) -> NoReturn:
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


# --- Spans of one record ---


def volume_span(text: str) -> str | None:
    """The text after the first volume number, such as 'Faust, 2. Theil' in '…, 12. Faust, 2. Theil'."""
    match = VOLUME_RE.search(text)
    if not match:
        return None
    span = text[match.end() :].strip(" ,;.")
    first = re.match(r"\w+", span)
    if not first or first.group().casefold() in NOT_VOLUME_TITLES:
        return None
    return span


def rule_spans(title: str, rule: ModuleType) -> tuple[list[tuple[str, str]], list[str]]:
    """Title spans with their source, and PERSON spans, from the ISBD rule and the volume rule."""
    spans: list[tuple[str, str]] = []
    persons: list[str] = []
    for start, end, label in rule.extract_spans(title, *rule.detect_flags(title)):
        text = title[start:end]
        if label == "TITLE":
            spans.append((text, "isbd_title"))
        elif label == "OTHER_TITLE":
            spans.append((text, "isbd_other_title"))
            volume = volume_span(text)
            if volume:
                spans.append((volume, "volume_rule"))
        elif label == "PERSON":
            persons.append(text)
    return spans, persons


def add_flair_spans(spans: list[tuple[str, str]], flair: dict[str, list[str]]) -> list[tuple[str, str]]:
    """Add WORK_OF_ART spans whose search term no rule span has."""
    terms = {search_term(span) for span, _ in spans} - {None}
    result = list(spans)
    for span in flair.get("WORK_OF_ART", []):
        term = search_term(span)
        if term and term not in terms:
            result.append((span, "flair"))
            terms.add(term)
    return result


def function_words_only(span: str) -> bool:
    """True when the span's search term holds only FUNCTION_WORDS."""
    term = search_term(span)
    if not term:
        return False
    return set(term.split()) <= FUNCTION_WORDS


def drop_function_word_spans(spans: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Drop spans of any source whose search term holds only function words."""
    return [(span, source) for span, source in spans if not function_words_only(span)]


def authors(entries: Any) -> list[dict[str, str]]:
    """The {label, uri} entries of dc_creator or dc_contributor that carry anything."""
    result = []
    for entry in entries or []:
        label, uri = (entry or {}).get("label") or "", (entry or {}).get("uri") or ""
        if label or uri:
            result.append({"label": label, "uri": uri})
    return result


def flair_labels(predictions: list[Prediction]) -> dict[str, list[str]]:
    """Flair spans by label, for the labels step 2 may use."""
    labels: dict[str, list[str]] = {label: [] for label in FLAIR_LABELS}
    for label, text, _score in predictions:
        if label in labels and text not in labels[label]:
            labels[label].append(text)
    return labels


def record_line(row: dict[str, Any], group: str, rule: ModuleType, predictions: list[Prediction] | None) -> dict[str, Any]:
    """The step 1 line for one record."""
    title = row.get("title") or ""
    spans, persons = rule_spans(title, rule) if title.strip() else ([], [])
    flair = flair_labels(predictions) if predictions is not None else {}
    spans = drop_function_word_spans(add_flair_spans(spans, flair))
    for person in flair.get("PERSON", []):
        if person not in persons:
            persons.append(person)
    creators = authors(row.get("dc_creator"))
    return {
        "obj_id": row["obj_id"],
        "title": title,
        "type_group": group,
        "htype": row.get("hierarchy_type") or "",
        "dc_type": row.get("dc_type") or "",
        "lang": row.get("lang") or "",
        "title_spans": [span for span, _ in spans],
        "span_sources": [source for _, source in spans],
        "persons": persons,
        "ddb_creators": creators,
        "ddb_contributors": [] if creators else authors(row.get("dc_contributor")),
        "flair": flair,
    }


def chunk_lines(rows: list[dict[str, Any]], rule: ModuleType, tag: Tagger | None, batch_size: int) -> list[dict[str, Any]]:
    """Lines for the records of a chunk that belong to a linked type group, in input order."""
    kept = []
    for row in rows:
        group = type_group(row.get("hierarchy_type") or "", row.get("dc_type") or "")
        if group in LINKED_GROUPS:
            kept.append((row, group))
    predictions: list[list[Prediction] | None] = [None] * len(kept)
    if tag is not None:
        titled = [i for i, (row, _) in enumerate(kept) if (row.get("title") or "").strip()]
        for start in range(0, len(titled), batch_size):
            batch = titled[start : start + batch_size]
            for i, result in zip(batch, tag([kept[i][0]["title"] for i in batch])):
                predictions[i] = result
    return [record_line(row, group, rule, predictions[i]) for i, (row, group) in enumerate(kept)]


# --- Input ---


def read_chunks(path: Path, chunk_size: int, limit: int | None) -> Iterator[list[dict[str, Any]]]:
    """Stream the records as lists of exactly chunk_size (the last may be shorter)."""
    parquet = pq.ParquetFile(path)
    all_rows = (row for batch in parquet.iter_batches(batch_size=chunk_size, columns=COLUMNS) for row in batch.to_pylist())
    rows = itertools.islice(all_rows, limit)
    while True:
        chunk = list(itertools.islice(rows, chunk_size))
        if not chunk:
            return
        yield chunk


# --- Pre-flight and run state ---


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def git_state() -> tuple[str, bool]:
    """The gemea commit, and whether this script or its modules have uncommitted changes."""
    try:
        commit = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, check=True)
        status = subprocess.run(
            ["git", "-C", str(ROOT), "status", "--porcelain", "--", *CODE_FILES], capture_output=True, text=True, check=True
        )
    except (OSError, subprocess.CalledProcessError) as e:
        die(f"cannot read the git state of {ROOT} ({e}); run from a gemea checkout")
    return commit.stdout.strip(), bool(status.stdout.strip())


def preflight(args: argparse.Namespace) -> None:
    if not args.input.is_file():
        die(f"{args.input} not found; pass the s2 parquet with --input")
    names = pq.ParquetFile(args.input).schema_arrow.names
    missing = [c for c in COLUMNS if c not in names]
    if missing:
        die(f"{args.input} has no column {', '.join(missing)}; pass the April s2_meta_de_content.parquet")
    if not args.rule_script.is_file():
        die(f"{args.rule_script} not found; pass the bibner-werk ISBD rule with --rule-script")
    if sha256(args.rule_script) != RULE_SHA256:
        die(f"{args.rule_script} differs from the committed rule (bibner-werk 050820b); copy that version")
    if args.dry_run:
        return
    try:
        args.output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        die(f"cannot create {args.output_dir} ({e}); choose another --output-dir")
    free = shutil.disk_usage(args.output_dir).free
    if free < MIN_FREE_BYTES:
        die(f"only {free // 1024**2} MB free in {args.output_dir}; free some disk or choose another --output-dir")


def run_settings(args: argparse.Namespace, commit: str, dirty: bool) -> dict[str, Any]:
    stat = args.input.stat()
    rows = pq.ParquetFile(args.input).metadata.num_rows
    records = min(rows, args.limit) if args.limit else rows
    return {
        "input": str(args.input.resolve()),
        "input_size": stat.st_size,
        "input_mtime": stat.st_mtime,
        "input_sha256": sha256(args.input),
        "records": records,
        "limit": args.limit,
        "chunk_size": args.chunk_size,
        "chunks": (records + args.chunk_size - 1) // args.chunk_size,
        "num_shards": args.num_shards,
        "flair": not args.no_flair,
        "model": None if args.no_flair else args.model,
        "model_caches": [] if args.no_flair else model_caches(),
        "versions": {} if args.no_flair else {"flair": package_version("flair"), "torch": package_version("torch")},
        "batch_size": args.batch_size,
        "rule_sha256": RULE_SHA256,
        "commit": commit,
        "uncommitted_changes": dirty,
    }


def write_atomic(path: Path, text: str) -> None:
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(text, encoding="utf-8")
    os.replace(temp, path)


def prepare_shard(args: argparse.Namespace, settings: dict[str, Any]) -> None:
    """Record this shard's settings, or check that they match an earlier run's."""
    for state in args.output_dir.glob("run-shard*.json"):
        if json.loads(state.read_text(encoding="utf-8")) != settings:
            die(f"input, code or settings differ from {state}; pass --restart to start over")
    path = args.output_dir / f"run-shard{args.shard}.json"
    if not path.exists():
        write_atomic(path, json.dumps(settings, indent=2))


def done_chunks(output_dir: Path, shard: int) -> set[int]:
    """Chunks in this shard's manifest whose part file exists; a line cut off by a crash is ignored."""
    manifest = output_dir / f"manifest-shard{shard}.jsonl"
    done: set[int] = set()
    if not manifest.exists():
        return done
    for line in manifest.read_text(encoding="utf-8").splitlines():
        try:
            chunk = json.loads(line)["chunk"]
        except (json.JSONDecodeError, KeyError):
            continue
        if (output_dir / part_name(chunk)).exists():
            done.add(chunk)
    return done


def part_name(chunk: int) -> str:
    return f"part-{chunk:06d}.jsonl"


def write_part(output_dir: Path, shard: int, chunk: int, lines: list[dict[str, Any]], records: int) -> None:
    """Write one chunk to .tmp, rename it when finished, then record it in the manifest."""
    final = output_dir / part_name(chunk)
    temp = final.with_name(final.name + ".tmp")
    try:
        with open(temp, "w", encoding="utf-8") as f:
            f.writelines(json.dumps(line, ensure_ascii=False) + "\n" for line in lines)
        os.replace(temp, final)
    finally:
        temp.unlink(missing_ok=True)
    entry = {"chunk": chunk, "records": records, "lines": len(lines), "finished_at": datetime.now(timezone.utc).isoformat()}  # noqa: UP017 (3.9-compatible)
    with open(output_dir / f"manifest-shard{shard}.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


# --- Run, merge, dry run ---


def run_shard(args: argparse.Namespace, rule: ModuleType, tag: Tagger | None, chunks: int) -> int:
    """Process this shard's chunks that are not done. Returns the exit code."""
    done = done_chunks(args.output_dir, args.shard)
    mine = [c for c in range(chunks) if c % args.num_shards == args.shard]
    logger.info("Shard %d of %d: %d chunks, %d already done", args.shard, args.num_shards, len(mine), len(done))
    groups: Counter[str] = Counter()
    started = time.monotonic()
    processed = 0
    try:
        for chunk, rows in enumerate(read_chunks(args.input, args.chunk_size, args.limit)):
            if chunk % args.num_shards != args.shard or chunk in done:
                continue
            lines = chunk_lines(rows, rule, tag, args.batch_size)
            write_part(args.output_dir, args.shard, chunk, lines, len(rows))
            groups.update(line["type_group"] for line in lines)
            processed += 1
            remaining = len(mine) - len(done) - processed
            elapsed = time.monotonic() - started
            logger.info("Chunk %d done (%d lines); about %.0f s left: %s", chunk, len(lines), remaining * elapsed / processed, dict(groups))
    except KeyboardInterrupt:
        logger.warning("Interrupted; rerun the same command to resume.")
        return 130
    logger.info("Shard %d finished", args.shard)
    return 0


def merge(args: argparse.Namespace) -> int:
    """Join the parts of all shards into one file, after checking that every chunk is there."""
    states = sorted(args.output_dir.glob("run-shard*.json"))
    if not states:
        die(f"no run-shard*.json in {args.output_dir}; run the shards first")
    settings = [json.loads(s.read_text(encoding="utf-8")) for s in states]
    if any(s != settings[0] for s in settings):
        die(f"the shards in {args.output_dir} ran with different settings; rerun them with --restart")
    if len(states) != settings[0]["num_shards"]:
        die(f"{len(states)} of {settings[0]['num_shards']} shards have run; run the others first")
    chunks = settings[0]["chunks"]
    identity_path = args.output_dir / RUN_ID_FILE
    if identity_path.exists():
        logger.info("Run %s", json.loads(identity_path.read_text(encoding="utf-8"))["run_id"])
    else:
        logger.warning("No %s in %s: a run from before run IDs", RUN_ID_FILE, args.output_dir)
    missing = [c for c in range(chunks) if not (args.output_dir / part_name(c)).exists()]
    if missing:
        die(f"{len(missing)} chunks are missing, first {missing[0]}; rerun the shard {missing[0] % settings[0]['num_shards']}")
    temp = args.output.with_name(args.output.name + ".tmp")
    lines = 0
    try:
        with open(temp, "w", encoding="utf-8") as out:
            for chunk in range(chunks):
                with open(args.output_dir / part_name(chunk), encoding="utf-8") as part:
                    for line in part:
                        out.write(line)
                        lines += 1
        os.replace(temp, args.output)
    finally:
        temp.unlink(missing_ok=True)
    logger.info("Merged %d chunks, %d lines -> %s", chunks, lines, args.output)
    return 0


def dry_run(args: argparse.Namespace, rule: ModuleType) -> int:
    """Print the first lines, rule only, and count the type groups of the --limit records."""
    groups: Counter[str] = Counter()
    shown = 0
    for rows in read_chunks(args.input, args.chunk_size, args.limit or DRY_RUN_RECORDS):
        for row in rows:
            groups[type_group(row.get("hierarchy_type") or "", row.get("dc_type") or "")] += 1
        for line in chunk_lines(rows, rule, None, args.batch_size):
            if shown < DRY_RUN_RECORDS:
                print(json.dumps(line, ensure_ascii=False))
                shown += 1
    logger.info("Dry run, no model, nothing written. Type groups: %s", dict(groups))
    return 0


def handle_sigterm(_signum: int, _frame: object) -> None:
    raise KeyboardInterrupt


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Step 1 of Werk linking: title spans and names per DDB record.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_MERGED, help="merged file, written by --merge")
    parser.add_argument("--rule-script", type=Path, default=DEFAULT_RULE_SCRIPT)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help="titles per Flair mini-batch")
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE, help="input records per part file")
    parser.add_argument("--shard", type=int, default=0)
    parser.add_argument("--num-shards", type=int, default=1)
    parser.add_argument("--limit", type=int, help="read only the first N input records")
    parser.add_argument("--no-flair", action="store_true", help="rule and volume rule only, no model")
    parser.add_argument("--run-id", type=run_id_arg, help="run ID (UTC start, 20261110T090205Z); default: the first shard's start")
    parser.add_argument("--restart", action="store_true", help="delete --output-dir and start over")
    parser.add_argument("--allow-dirty", action="store_true", help="run despite uncommitted changes; recorded")
    parser.add_argument("--merge", action="store_true", help="join the finished shards into --output")
    parser.add_argument("--dry-run", action="store_true", help="print the first lines, rule only; write nothing")
    args = parser.parse_args()
    for name in ("batch_size", "chunk_size", "num_shards", "limit"):
        value = getattr(args, name)
        if value is not None and value < 1:
            parser.error(f"--{name.replace('_', '-')} must be at least 1")
    if not 0 <= args.shard < args.num_shards:
        parser.error(f"--shard must be between 0 and {args.num_shards - 1}")
    return args


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args()
    if args.merge:
        return merge(args)
    preflight(args)
    rule = load_rule(args.rule_script)
    if args.dry_run:
        return dry_run(args, rule)
    commit, dirty = git_state()
    if dirty and not args.allow_dirty:
        die("this script or its modules have uncommitted changes; commit them, or pass --allow-dirty")
    if args.restart:
        shutil.rmtree(args.output_dir, ignore_errors=True)
        args.output_dir.mkdir(parents=True)
    settings = run_settings(args, commit, dirty)
    prepare_shard(args, settings)
    identity = run_identity(args.output_dir, args.run_id)
    logger.info("Run %s, started %s", identity["run_id"], identity["started_at"])
    mine = [c for c in range(settings["chunks"]) if c % args.num_shards == args.shard]
    if set(mine) <= done_chunks(args.output_dir, args.shard):
        logger.info("Shard %d already finished in %s", args.shard, args.output_dir)
        return 0
    tag = None if args.no_flair else load_tagger(args.model, args.batch_size)
    signal.signal(signal.SIGTERM, handle_sigterm)
    return run_shard(args, rule, tag, settings["chunks"])


if __name__ == "__main__":
    sys.exit(main())
