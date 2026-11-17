#!/usr/bin/env python3
# Purpose:      Join the outputs of link_gnd_local.py runs on parts of one step 1 file into one
#               links CSV, with the status summary and the term timing summed over the parts.
# Usage:        python scripts/py/merge_link_parts.py \
#                   --parts data/processed/nerl-gnd/werk_links_part0?.csv \
#                   --output data/processed/nerl-gnd/werk_links.csv \
#                   [--input data/processed/nerl-gnd/werk_spans.jsonl] [--force] [--dry-run]
# Inputs:       --parts  finished link_gnd_local.py outputs, in input order; each with its
#                        <part>.parts/run.json (with an output entry), <stem>_status_by_group.csv
#                        and <stem>_term_timing.csv next to it.
#               --input  optional: the step 1 file the parts were split from; its line count must
#                        equal the merged rows.
# Outputs:      --output the links CSV (link_gnd_local.OUTPUT_COLUMNS), the parts in the order given.
#               <stem>_status_by_group.csv and <stem>_term_timing.csv, as link_gnd_local.py writes
#                   them: records and high-confidence links summed per type_group and status; per
#                   term, records, names scored and seconds summed over the parts.
#               <stem>_merge.json: the run ID and earliest start the parts share,
#                   each part's input, run settings and output checksum, and the merged output's
#                   SHA-256, rows and status counts.
#               Exit codes: 0 success, 1 failure, 2 usage error.
# Dependencies: link_gnd_local.py in the same folder (pyarrow, rapidfuzz).
# Assumptions:  Every part ran with the same commit, code, GND tables and settings; the merge stops
#               otherwise, and if the parts carry different run IDs, or only some carry one (parts
#               from before run IDs carry none, and the merge records null). A term's candidates and truncated flag come from the same tables in every
#               part, so they are taken from the first part that has the term; its lookup seconds
#               are summed, since each part looked it up once.

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from link_gnd_local import (
    OUTPUT_COLUMNS,
    SUMMARY_COLUMNS,
    TIMING_COLUMNS,
    git_state,
    sha256,
    side_outputs,
    summary_rows,
    write_csv_atomic,
)
from werk_link_rules import RUN_ID_FILE, die, write_atomic

logger = logging.getLogger("merge_link_parts")

# run.json keys that must be equal in every part; "input" and "output" differ by design.
SHARED_KEYS = ("backend", "script", "gnd", "settings", "versions")


# --- Parts ---


def part_state(part: Path) -> dict[str, Any]:
    """The part's run.json, checked to be finished and to describe this very output file."""
    state_path = part.with_name(part.name + ".parts") / "run.json"
    if not part.is_file() or not state_path.is_file():
        die(f"{part} or its {state_path.name} is missing; run link_gnd_local.py on this part first")
    state: dict[str, Any] = json.loads(state_path.read_text(encoding="utf-8"))
    output = state.get("output")
    if not output:
        die(f"{part} has not finished (no output entry in {state_path}); rerun its link_gnd_local.py command")
    if output["sha256"] != sha256(part):
        die(f"{part} differs from the SHA-256 in {state_path}; rerun that part with --restart")
    return state


def check_shared(states: list[dict[str, Any]], parts: list[Path]) -> dict[str, Any]:
    """The run settings all parts share; stops if any part differs from the first."""
    shared = {key: states[0].get(key) for key in SHARED_KEYS}
    for part, state in zip(parts[1:], states[1:]):
        differ = [key for key in SHARED_KEYS if state.get(key) != shared[key]]
        if differ:
            die(f"{part} ran with other {', '.join(differ)} than {parts[0]}; rerun it at the same commit and settings")
    return shared


def shared_identity(parts: list[Path]) -> dict[str, str | None]:
    """The run ID all parts share and their earliest start; null for parts from before run IDs."""
    paths = [part.with_name(part.name + ".parts") / RUN_ID_FILE for part in parts]
    present = [p for p in paths if p.is_file()]
    if not present:
        logger.warning("No part has %s: parts from before run IDs; the merge records run_id null", RUN_ID_FILE)
        return {"run_id": None, "started_at": None}
    if len(present) != len(paths):
        missing = [str(p.parent) for p in paths if not p.is_file()]
        die(f"{', '.join(missing)} have no {RUN_ID_FILE}; rerun those parts with the same --run-id as the others")
    identities = [json.loads(p.read_text(encoding="utf-8")) for p in present]
    run_ids = {identity["run_id"] for identity in identities}
    if len(run_ids) != 1:
        die(f"the parts carry different run IDs ({', '.join(sorted(run_ids))}); run every part with the same --run-id")
    return {"run_id": run_ids.pop(), "started_at": min(identity["started_at"] for identity in identities)}


# --- Merging ---


def merge_links(parts: list[Path], output: Path) -> tuple[int, Counter[str]]:
    """Write the parts into output.tmp in order, checking that each obj_id appears once; returns rows and statuses."""
    temp = output.with_name(output.name + ".tmp")
    seen_path = output.with_name(output.name + ".seen.sqlite")
    seen_path.unlink(missing_ok=True)
    # On disk, because the set of seen IDs grows with the input.
    seen = sqlite3.connect(seen_path)
    seen.execute("CREATE TABLE ids (obj_id TEXT PRIMARY KEY)")
    rows = 0
    statuses: Counter[str] = Counter()
    try:
        with open(temp, "w", encoding="utf-8", newline="") as out:
            writer = csv.writer(out, lineterminator="\n")
            writer.writerow(OUTPUT_COLUMNS)
            for part in parts:
                with open(part, encoding="utf-8", newline="") as f:
                    reader = csv.DictReader(f)
                    if reader.fieldnames != OUTPUT_COLUMNS:
                        die(f"{part} has columns {reader.fieldnames}, not those of link_gnd_local.py")
                    for row in reader:
                        try:
                            seen.execute("INSERT INTO ids VALUES (?)", (row["obj_id"],))
                        except sqlite3.IntegrityError:
                            die(f"obj_id {row['obj_id']} appears in more than one part; split the input again")
                        writer.writerow(row[c] for c in OUTPUT_COLUMNS)
                        rows += 1
                        statuses[row["status"]] += 1
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    finally:
        seen.close()
        seen_path.unlink(missing_ok=True)
    return rows, statuses


def read_rows(path: Path, columns: list[str]) -> list[dict[str, str]]:
    if not path.is_file():
        die(f"{path} is missing; the part's run did not finish its merge")
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != columns:
            die(f"{path} has columns {reader.fieldnames}, expected {columns}")
        return list(reader)


def merged_summary(parts: list[Path]) -> list[list[Any]]:
    """Records and high-confidence links per type group and status, summed; totals recomputed as group "all"."""
    groups: Counter[tuple[str, str]] = Counter()
    high: Counter[tuple[str, str]] = Counter()
    for part in parts:
        for row in read_rows(side_outputs(part)[0], SUMMARY_COLUMNS):
            if row["type_group"] == "all":
                continue
            key = (row["type_group"], row["status"])
            groups[key] += int(row["records"])
            high[key] += int(row["high_confidence"])
    return summary_rows(groups, high)


def merged_timing(parts: list[Path]) -> list[list[Any]]:
    """Per term: records, names scored and seconds summed over the parts; the slowest to score first."""
    terms: dict[str, list[Any]] = {}
    for part in parts:
        for row in read_rows(side_outputs(part)[1], TIMING_COLUMNS):
            term = terms.setdefault(row["term"], [row["term"], 0, row["candidates"], 0, row["truncated"], 0.0, 0.0])
            term[1] += int(row["records"])
            term[3] += int(row["names_scored"])
            term[5] += float(row["lookup_seconds"])
            term[6] += float(row["score_seconds"])
    rows = [[t, r, c, n, tr, round(lk, 4), round(sc, 4)] for t, r, c, n, tr, lk, sc in terms.values()]
    return sorted(rows, key=lambda row: (-row[6], row[0]))


def count_lines(path: Path) -> int:
    with open(path, "rb") as f:
        return sum(1 for line in f if line.strip())


# --- Pre-flight and main ---


def outputs(output: Path) -> list[Path]:
    summary, timing = side_outputs(output)
    return [output, summary, timing, output.with_name(f"{output.stem}_merge.json")]


def preflight(args: argparse.Namespace) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if len(set(args.parts)) != len(args.parts):
        die("a part is given twice; list each part once")
    if args.output in args.parts:
        die(f"--output {args.output} is one of the parts; choose another name")
    if args.input is not None and not args.input.is_file():
        die(f"--input {args.input} not found; pass the step 1 file the parts were split from")
    states = [part_state(part) for part in args.parts]
    shared = check_shared(states, args.parts)
    shared_identity(args.parts)
    if args.dry_run:
        return states, shared
    folder = args.output.resolve().parent
    if not folder.is_dir() or not os.access(folder, os.W_OK):
        die(f"{folder} is missing or not writable; create it or choose another --output")
    existing = [p.name for p in outputs(args.output) if p.exists()]
    if existing and not args.force:
        die(f"{', '.join(existing)} already exist; move them, or pass --force")
    return states, shared


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Join link_gnd_local.py outputs of parts of one step 1 file.")
    parser.add_argument("--parts", required=True, nargs="+", type=Path, help="part outputs, in input order")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--input", type=Path, help="the step 1 file the parts were split from, to check the row count")
    parser.add_argument("--force", action="store_true", help="overwrite the merged outputs")
    parser.add_argument("--dry-run", action="store_true", help="check the parts and print their rows; write nothing")
    args = parser.parse_args(argv)
    if len(args.parts) < 2:
        parser.error("--parts needs at least two part outputs")
    return args


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args(argv)
    states, shared = preflight(args)
    part_rows = [state["output"]["rows"] for state in states]
    if args.dry_run:
        for part, rows in zip(args.parts, part_rows):
            print(f"{part}\t{rows} rows")
        print(f"total\t{sum(part_rows)} rows")
        return 0

    rows, statuses = merge_links(args.parts, args.output)
    temp = args.output.with_name(args.output.name + ".tmp")
    try:
        if rows != sum(part_rows):
            die(f"merged {rows} rows but the parts' run.json give {sum(part_rows)}; check the parts")
        if args.input is not None and count_lines(args.input) != rows:
            die(f"merged {rows} rows but {args.input} has {count_lines(args.input)} records; a part is missing")
        summary_path, timing_path = side_outputs(args.output)
        write_csv_atomic(summary_path, SUMMARY_COLUMNS, merged_summary(args.parts))
        write_csv_atomic(timing_path, TIMING_COLUMNS, merged_timing(args.parts))
        commit, dirty = git_state()
        record = {
            "merged_by": {"script": "scripts/py/merge_link_parts.py", "commit": commit, "uncommitted_changes": dirty},
            **shared_identity(args.parts),
            **shared,
            "parts": [
                {"path": str(part.resolve()), "input": state["input"], "output": state["output"]}
                for part, state in zip(args.parts, states)
            ],
            "input": None if args.input is None else {"path": str(args.input.resolve()), "sha256": sha256(args.input)},
            "output": {
                "sha256": sha256(temp),
                "rows": rows,
                "statuses": dict(sorted(statuses.items())),
                "finished_at": datetime.now(timezone.utc).isoformat(),  # noqa: UP017 (3.9-compatible)
            },
        }
        write_atomic(outputs(args.output)[3], json.dumps(record, indent=2, ensure_ascii=False))
        os.replace(temp, args.output)
    finally:
        temp.unlink(missing_ok=True)
    logger.info("Merged %d parts, %d rows -> %s; statuses %s", len(args.parts), rows, args.output, dict(statuses))
    return 0


if __name__ == "__main__":
    sys.exit(main())
