#!/usr/bin/env python3
# Purpose:      Break down the no_candidate rows of a werk_links.csv before sampling them:
#               how many have no search term at all, how many terms they have, how many tokens the first
#               term holds, and their type group.
# Usage:        .venv/bin/python scripts/py/count_no_candidate.py \
#                   [--input data/processed/nerl-gnd/rerun-d38/werk_links.csv] \
#                   [--out-dir <folder of --input>] [--date yyyymmdd] [--force] [--dry-run]
# Inputs:       --input  link_gnd_local.py output (link_gnd_local.OUTPUT_COLUMNS), read row by row.
# Outputs:      In --out-dir: no_candidate_breakdown_<date>.csv with columns dimension, value, records,
#                   percent (of all no_candidate rows); dimensions: search_terms (empty or present),
#                   term_count, first_term_tokens, type_group; a dimension "all" row first.
#               Exit codes: 0 success, 1 failure, 2 usage error.
# Dependencies: link_gnd_local.py (OUTPUT_COLUMNS, write_csv_atomic) and werk_link_rules.py (die) in the
#               same folder.
# Assumptions:  Search terms are joined by " | " (link_gnd_local.py). The span source of a search term is
#               not in werk_links.csv, so it is not counted here. Memory holds only the counters; the run
#               takes seconds, so it does not resume.

from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
from collections import Counter
from datetime import date
from pathlib import Path

from link_gnd_local import OUTPUT_COLUMNS, write_csv_atomic
from werk_link_rules import die

logger = logging.getLogger("count_no_candidate")

DEFAULT_INPUT = Path("data/processed/nerl-gnd/rerun-d38/werk_links.csv")
STATUS = "no_candidate"
COLUMNS = ["dimension", "value", "records", "percent"]
DIMENSIONS = ("search_terms", "term_count", "first_term_tokens", "type_group")
TOP_BIN = 4  # counts of TOP_BIN or more go into one "4+" bin


# --- Counting ---


def bin_count(n: int) -> str:
    return f"{TOP_BIN}+" if n >= TOP_BIN else str(n)


def describe(row: dict[str, str]) -> dict[str, str]:
    """The value of each dimension for one row."""
    terms = [t.strip() for t in row["search_terms"].split(" | ") if t.strip()]
    first_tokens = len(terms[0].split()) if terms else 0
    return {
        "search_terms": "present" if terms else "empty",
        "term_count": bin_count(len(terms)),
        "first_term_tokens": bin_count(first_tokens),
        "type_group": row["type_group"] or "(none)",
    }


def count(path: Path) -> tuple[dict[str, Counter[str]], int]:
    """Per dimension the no_candidate records per value, and the number of no_candidate records."""
    counts: dict[str, Counter[str]] = {dimension: Counter() for dimension in DIMENSIONS}
    total = 0
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != OUTPUT_COLUMNS:
            die(f"{path} has columns {reader.fieldnames}, not those of link_gnd_local.py")
        for row in reader:
            if row["status"] != STATUS:
                continue
            total += 1
            for dimension, value in describe(row).items():
                counts[dimension][value] += 1
    return counts, total


def rows(counts: dict[str, Counter[str]], total: int) -> list[list[str | int | float]]:
    """The 'all' row, then each dimension's values in sorted order."""
    out: list[list[str | int | float]] = [["all", STATUS, total, 100.0 if total else 0.0]]
    for dimension in DIMENSIONS:
        for value, records in sorted(counts[dimension].items()):
            out.append([dimension, value, records, round(100 * records / total, 2)])
    return out


# --- Pre-flight and main ---


def output_path(args: argparse.Namespace) -> Path:
    return Path(args.out_dir) / f"no_candidate_breakdown_{args.date}.csv"


def preflight(args: argparse.Namespace) -> None:
    if not args.input.is_file():
        die(f"--input {args.input} not found; copy the run here first or pass its path")
    if args.dry_run:
        return
    if not args.out_dir.is_dir() or not os.access(args.out_dir, os.W_OK):
        die(f"--out-dir {args.out_dir} is missing or not writable; create it or choose another")
    if output_path(args).exists() and not args.force:
        die(f"{output_path(args)} already exists; pass --force to overwrite it")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Break down the no_candidate rows of werk_links.csv.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--out-dir", type=Path, help="default: the folder of --input")
    parser.add_argument("--date", default=f"{date.today():%Y%m%d}", help="date stamp of the output name")
    parser.add_argument("--force", action="store_true", help="overwrite an existing output")
    parser.add_argument("--dry-run", action="store_true", help="check the inputs and print the plan; write nothing")
    args = parser.parse_args(argv)
    if not (args.date.isdigit() and len(args.date) == 8):
        parser.error(f"--date {args.date}: expected yyyymmdd")
    if args.out_dir is None:
        args.out_dir = args.input.parent
    return args


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args(argv)
    preflight(args)
    if args.dry_run:
        print(f"would count\t{STATUS} rows of {args.input}")
        print(f"would write\t{output_path(args)}")
        return 0

    counts, total = count(args.input)
    if total == 0:
        logger.warning("%s has no %s rows; the output holds only the 'all' row", args.input, STATUS)
    write_csv_atomic(output_path(args), COLUMNS, rows(counts, total))
    logger.info("Counted %d %s rows into %s", total, STATUS, output_path(args))
    return 0


if __name__ == "__main__":
    sys.exit(main())
