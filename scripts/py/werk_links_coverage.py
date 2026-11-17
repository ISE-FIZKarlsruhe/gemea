#!/usr/bin/env python3
# Purpose:      Count the statuses of a link_gnd_local.py links CSV by type group, author source,
#               matched span source, truncated flag and candidate count, and list the matched spans
#               of the linked rows.
# Usage:        .venv/bin/python scripts/py/werk_links_coverage.py \
#                   [--input data/processed/nerl-gnd/werk_links.csv] [--out-dir data/processed/nerl-gnd] \
#                   [--date yyyymmdd] [--force] [--dry-run]
# Inputs:       --input  links CSV with link_gnd_local.OUTPUT_COLUMNS (default werk_links.csv); read row
#                        by row. If <stem>_status_by_group.csv lies next to it, the type group counts
#                        are checked against it.
# Outputs:      In --out-dir, for each breakdown B in type_group, author_source, matched_span_source,
#               truncated, candidate_bin:
#                   werk_links_coverage_by_<B>_<date>.csv: value, status, records, percent (of the
#                   value's records); per value a status "all" row, and a value "all" for the whole file.
#               werk_links_coverage_linked_spans_<date>.csv: matched_span, matched_span_source, records,
#                   function_words_only; linked rows only, most frequent first.
#               Exit codes: 0 success, 1 failure, 2 usage error.
# Dependencies: link_gnd_local.py in the same folder (pyarrow, rapidfuzz), for its columns and writer.
# Assumptions:  An empty author_source or matched_span_source is counted as "(none)". FUNCTION_WORDS is
#               a prototype list: the function words among the slowest terms of the first full run
#               (werk_links_term_timing.csv) and of the Goethe-Faust smoke run, not a sourced stop list.

from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

from link_gnd_local import OUTPUT_COLUMNS, side_outputs, write_csv_atomic
from werk_link_rules import die

logger = logging.getLogger("werk_links_coverage")

DEFAULT_INPUT = Path("data/processed/nerl-gnd/werk_links.csv")
BREAKDOWNS = ("type_group", "author_source", "matched_span_source", "truncated", "candidate_bin")
BREAKDOWN_COLUMNS = ["value", "status", "records", "percent"]
SPAN_COLUMNS = ["matched_span", "matched_span_source", "records", "function_words_only"]
NONE_VALUE = "(none)"
# Upper bounds of the candidate count bins; the last bin is everything above 500 (--search-size).
CANDIDATE_BINS = ((0, "0"), (1, "1"), (10, "2-10"), (100, "11-100"), (500, "101-500"))
FUNCTION_WORDS = frozenset(
    {"der", "die", "das", "des", "dem", "den", "ein", "eine", "und", "oder", "von", "für", "bis", "bey", "the"}
)


# --- Counting ---


def candidate_bin(count: str) -> str:
    """The bin label of a candidate_count value."""
    n = int(count)
    for upper, label in CANDIDATE_BINS:
        if n <= upper:
            return label
    return ">500"


def function_words_only(span: str) -> bool:
    """True when every word of the span is in FUNCTION_WORDS."""
    words = span.casefold().split()
    return bool(words) and all(word in FUNCTION_WORDS for word in words)


def breakdown_value(row: dict[str, str], breakdown: str) -> str:
    if breakdown == "candidate_bin":
        return candidate_bin(row["candidate_count"])
    return row[breakdown] or NONE_VALUE


def count_rows(path: Path) -> tuple[dict[str, Counter[tuple[str, str]]], Counter[tuple[str, str]], int]:
    """Per breakdown, records per (value, status); the matched spans of linked rows; and the row count."""
    counts: dict[str, Counter[tuple[str, str]]] = {b: Counter() for b in BREAKDOWNS}
    spans: Counter[tuple[str, str]] = Counter()
    rows = 0
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != OUTPUT_COLUMNS:
            die(f"{path} has columns {reader.fieldnames}, not those of link_gnd_local.py")
        for row in reader:
            rows += 1
            for breakdown in BREAKDOWNS:
                counts[breakdown][(breakdown_value(row, breakdown), row["status"])] += 1
            if row["status"] == "linked":
                spans[(row["matched_span"], row["matched_span_source"] or NONE_VALUE)] += 1
    return counts, spans, rows


def percent(part: int, whole: int) -> float:
    return round(100 * part / whole, 2) if whole else 0.0


def breakdown_rows(counts: Counter[tuple[str, str]]) -> list[list[Any]]:
    """Rows per value and status, a status "all" row per value, then value "all" over the whole file."""
    per_value: Counter[str] = Counter()
    per_status: Counter[str] = Counter()
    for (value, status), n in counts.items():
        per_value[value] += n
        per_status[status] += n
    total = sum(per_value.values())
    rows: list[list[Any]] = []
    for value in sorted(per_value):
        statuses = sorted(s for v, s in counts if v == value)
        rows += [[value, s, counts[(value, s)], percent(counts[(value, s)], per_value[value])] for s in statuses]
        rows.append([value, "all", per_value[value], 100.0])
    rows += [["all", s, n, percent(n, total)] for s, n in sorted(per_status.items())]
    rows.append(["all", "all", total, 100.0])
    return rows


def span_rows(spans: Counter[tuple[str, str]]) -> list[list[Any]]:
    ordered = sorted(spans.items(), key=lambda item: (-item[1], item[0]))
    return [[span, source, n, function_words_only(span)] for (span, source), n in ordered]


def check_summary(summary: Path, counts: Counter[tuple[str, str]]) -> None:
    """Stop if the linker's status summary disagrees with the counts by type group."""
    with open(summary, encoding="utf-8", newline="") as f:
        # The linker writes an empty type group as such; this script counts it as "(none)".
        expected = {
            (r["type_group"] or NONE_VALUE, r["status"]): int(r["records"])
            for r in csv.DictReader(f)
            if r["type_group"] != "all"
        }
    if dict(counts) != expected:
        differ = sorted(set(counts.items()) ^ set(expected.items()))
        die(f"counts by type group differ from {summary}: {differ[:5]}; is it the summary of this CSV?")
    logger.info("Type group counts agree with %s", summary)


# --- Pre-flight and main ---


def output_paths(out_dir: Path, stamp: str) -> list[Path]:
    paths = [out_dir / f"werk_links_coverage_by_{b}_{stamp}.csv" for b in BREAKDOWNS]
    return [*paths, out_dir / f"werk_links_coverage_linked_spans_{stamp}.csv"]


def preflight(args: argparse.Namespace) -> list[Path]:
    if not args.input.is_file():
        die(f"--input {args.input} not found; copy it from the GPU host or pass its path")
    outputs = output_paths(args.out_dir, args.date)
    if args.dry_run:
        return outputs
    if not args.out_dir.is_dir() or not os.access(args.out_dir, os.W_OK):
        die(f"{args.out_dir} is missing or not writable; create it or choose another --out-dir")
    existing = [p.name for p in outputs if p.exists()]
    if existing and not args.force:
        die(f"{', '.join(existing)} already exist; move them, or pass --force")
    return outputs


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Coverage tables of a link_gnd_local.py links CSV.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--out-dir", type=Path, help="default: the folder of --input")
    parser.add_argument("--date", default=date.today().strftime("%Y%m%d"), help="yyyymmdd in the output names")
    parser.add_argument("--force", action="store_true", help="overwrite the outputs")
    parser.add_argument("--dry-run", action="store_true", help="check the input and print the outputs; write nothing")
    args = parser.parse_args(argv)
    if not (len(args.date) == 8 and args.date.isdigit()):
        parser.error(f"--date {args.date} is not yyyymmdd")
    if args.out_dir is None:
        args.out_dir = args.input.parent
    return args


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args(argv)
    outputs = preflight(args)
    if args.dry_run:
        for path in outputs:
            print(f"would write\t{path}")
        return 0

    counts, spans, rows = count_rows(args.input)
    summary = side_outputs(args.input)[0]
    if summary.is_file():
        check_summary(summary, counts["type_group"])
    for breakdown, path in zip(BREAKDOWNS, outputs):
        write_csv_atomic(path, BREAKDOWN_COLUMNS, breakdown_rows(counts[breakdown]))
    linked_spans = span_rows(spans)
    write_csv_atomic(outputs[-1], SPAN_COLUMNS, linked_spans)
    function_rows = sum(row[2] for row in linked_spans if row[3])
    logger.info("Counted %d rows into %d files in %s; %d linked rows have a function-word span",
                rows, len(outputs), args.out_dir, function_rows)  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(main())
