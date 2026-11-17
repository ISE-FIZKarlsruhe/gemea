#!/usr/bin/env python3
# Purpose:      Count the title spans of a step 1 file (extract_werk_spans.py) whose search term holds
#               only function words, by span source, to check whether dropping such Flair
#               spans at the merge also needs to cover rule spans.
# Usage:        .venv/bin/python scripts/py/count_function_word_spans.py \
#                   [--input data/processed/nerl-gnd/werk_spans.jsonl] [--out-dir data/processed/nerl-gnd] \
#                   [--date yyyymmdd] [--force] [--dry-run]
# Inputs:       --input  step 1 JSONL, one record per line with title_spans and span_sources of equal
#                        length; read line by line.
# Outputs:      In --out-dir:
#                   function_word_spans_by_source_<date>.csv: span_source, spans, records,
#                   function_word_spans, function_word_records; a source "all" row last.
#                   function_word_spans_by_term_<date>.csv: span_source, term, spans, records; function-
#                   word-only terms only, most frequent first.
#               "records" counts the records with at least one such span. Exit codes: 0 success,
#               1 failure, 2 usage error.
# Dependencies: extract_werk_spans.py (FUNCTION_WORDS) and werk_link_rules.py (search_term) in the same
#               folder, so the count uses the filter's own test.
# Assumptions:  Spans whose search term is None (too short) are counted in "spans" but never as function-
#               word spans. Memory is bounded by the number of distinct function-word terms, not by the
#               input; the run takes minutes, so it does not resume.

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

from extract_werk_spans import FUNCTION_WORDS
from link_gnd_local import write_csv_atomic
from werk_link_rules import die, search_term

logger = logging.getLogger("count_function_word_spans")

DEFAULT_INPUT = Path("data/processed/nerl-gnd/werk_spans.jsonl")
SOURCE_COLUMNS = ["span_source", "spans", "records", "function_word_spans", "function_word_records"]
TERM_COLUMNS = ["span_source", "term", "spans", "records"]
ALL = "all"


# --- Counting ---


def function_word_term(span: str) -> str | None:
    """The span's search term if it holds only function words, else None."""
    term = search_term(span)
    if term and set(term.split()) <= FUNCTION_WORDS:
        return term
    return None


def count_lines(path: Path) -> tuple[dict[str, Counter[str]], Counter[tuple[str, str]], Counter[tuple[str, str]], int]:
    """Per source the span and record counts; per (source, term) the spans and the records; and the line count."""
    by_source: dict[str, Counter[str]] = {}
    term_spans: Counter[tuple[str, str]] = Counter()
    term_records: Counter[tuple[str, str]] = Counter()
    lines = 0
    with open(path, encoding="utf-8") as f:
        for number, line in enumerate(f, start=1):
            record = json.loads(line)
            spans, sources = record.get("title_spans") or [], record.get("span_sources") or []
            if len(spans) != len(sources):
                die(f"{path} line {number}: {len(spans)} title_spans but {len(sources)} span_sources")
            lines += 1
            add_record(by_source, term_spans, term_records, spans, sources)
    return by_source, term_spans, term_records, lines


def add_record(
    by_source: dict[str, Counter[str]],
    term_spans: Counter[tuple[str, str]],
    term_records: Counter[tuple[str, str]],
    spans: list[str],
    sources: list[str],
) -> None:
    """Add one record's spans to the counters; a record counts once per source and once per term."""
    seen_sources: set[str] = set()
    hit_sources: set[str] = set()
    seen_terms: set[tuple[str, str]] = set()
    for span, source in zip(spans, sources):
        for key in (source, ALL):
            by_source.setdefault(key, Counter())["spans"] += 1
            seen_sources.add(key)
        term = function_word_term(span)
        if term is None:
            continue
        for key in (source, ALL):
            by_source[key]["function_word_spans"] += 1
            hit_sources.add(key)
        term_spans[(source, term)] += 1
        seen_terms.add((source, term))
    for key in seen_sources:
        by_source[key]["records"] += 1
    for key in hit_sources:
        by_source[key]["function_word_records"] += 1
    for pair in seen_terms:
        term_records[pair] += 1


def source_rows(by_source: dict[str, Counter[str]]) -> list[list[Any]]:
    keys = sorted(k for k in by_source if k != ALL) + ([ALL] if ALL in by_source else [])
    return [[k, *(by_source[k][c] for c in SOURCE_COLUMNS[1:])] for k in keys]


def term_rows(term_spans: Counter[tuple[str, str]], term_records: Counter[tuple[str, str]]) -> list[list[Any]]:
    ordered = sorted(term_spans.items(), key=lambda item: (-item[1], item[0]))
    return [[source, term, n, term_records[(source, term)]] for (source, term), n in ordered]


# --- Pre-flight and main ---


def output_paths(out_dir: Path, stamp: str) -> list[Path]:
    return [
        out_dir / f"function_word_spans_by_source_{stamp}.csv",
        out_dir / f"function_word_spans_by_term_{stamp}.csv",
    ]


def preflight(args: argparse.Namespace) -> list[Path]:
    if not args.input.is_file():
        die(f"--input {args.input} not found; copy werk_spans.jsonl from the GPU host or pass its path")
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
    parser = argparse.ArgumentParser(description="Count function-word-only title spans by span source.")
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

    by_source, term_spans, term_records, lines = count_lines(args.input)
    write_csv_atomic(outputs[0], SOURCE_COLUMNS, source_rows(by_source))
    write_csv_atomic(outputs[1], TERM_COLUMNS, term_rows(term_spans, term_records))
    hits = {k: c["function_word_spans"] for k, c in by_source.items() if k != ALL and c["function_word_spans"]}
    logger.info("Read %d lines; function-word spans by source: %s", lines, hits or "none")
    return 0


if __name__ == "__main__":
    sys.exit(main())
