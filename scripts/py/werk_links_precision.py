#!/usr/bin/env python3
# Purpose:      Counts from one labelled sheet of a sample_werk_links.py workbook:
#               for the linked sheet, precision strict and lenient with
#               Wilson 95 % intervals and the label counts per type group; for an error sheet (such as
#               author_conflict), the cause counts per type group and the rows with a note.
# Usage:        .venv/bin/python scripts/py/werk_links_precision.py \
#                   --workbook data/annotation/<labelled>.xlsx [--sheet linked] \
#                   [--out-dir data/processed/nerl-gnd] [--date yyyymmdd] [--force] [--dry-run]
# Inputs:       --workbook  a sample_werk_links.py workbook whose --sheet has a label on every row.
# Outputs:      In --out-dir, for --sheet linked:
#                   werk_links_precision_<date>.csv: definition, type_group, hits, n, excluded, precision,
#                   wilson_low, wilson_high; type_group "all" for the whole sample.
#                   werk_links_precision_labels_<date>.csv: type_group, label, records; "all" totals.
#               for any other --sheet S:
#                   werk_links_causes_<S>_<date>.csv: type_group, label, records; "all" totals.
#                   werk_links_notes_<S>_<date>.csv: obj_id, type_group, label, title, search_terms, note;
#                   the rows with a note, in sheet order.
#                   werk_links_span_ok_<S>_<date>.csv: label, span_ok, records; label "all" totals; only
#                   when the sheet has a span_ok column (y, n or na: is the scored span the title proper).
#               Exit codes: 0 success, 1 failure (missing, unknown or blank labels included), 2 usage error.
# Dependencies: openpyxl; sample_werk_links.py in the same folder, for LABELS.
# Assumptions:  strict counts only correct as a hit, lenient also related (Expressions
#               are linked to their Work later). unsure is left out of n in both. Rows without obj_id
#               are blank rows Excel added and are skipped. The per type group rows rest on few records;
#               the sample was not stratified.

from __future__ import annotations

import argparse
import logging
import math
import os
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

from link_gnd_local import sha256, write_csv_atomic
from openpyxl import load_workbook
from sample_werk_links import GENERIC_LABELS, LABELS
from werk_link_rules import die

logger = logging.getLogger("werk_links_precision")

LINKED = "linked"
DEFINITIONS = {"strict": frozenset({"correct"}), "lenient": frozenset({"correct", "related"})}
EXCLUDED = frozenset({"unsure"})
Z_95 = 1.959963984540054
ALL = "all"
PRECISION_COLUMNS = ["definition", "type_group", "hits", "n", "excluded", "precision", "wilson_low", "wilson_high"]
LABEL_COLUMNS = ["type_group", "label", "records"]
NOTE_COLUMNS = ["obj_id", "type_group", "label", "title", "search_terms", "note"]
SPAN_OK = "span_ok"
SPAN_OK_VALUES = ("y", "n", "na")
SPAN_OK_COLUMNS = ["label", "span_ok", "records"]


# --- Statistics ---


def wilson(hits: int, n: int, z: float = Z_95) -> tuple[float, float]:
    """Wilson score interval of hits out of n."""
    if n == 0:
        return math.nan, math.nan
    p = hits / n
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    # At hits 0 or n one bound is exactly 0 or 1; rounding error would leave it just inside.
    low = 0.0 if hits == 0 else centre - half
    high = 1.0 if hits == n else centre + half
    return low, high


def precision_row(definition: str, group: str, labels: Counter[str]) -> list[Any]:
    excluded = sum(labels[label] for label in EXCLUDED)
    n = sum(labels.values()) - excluded
    hits = sum(labels[label] for label in DEFINITIONS[definition])
    low, high = wilson(hits, n)
    precision = hits / n if n else math.nan
    return [definition, group, hits, n, excluded, f"{precision:.4f}", f"{low:.4f}", f"{high:.4f}"]


# --- Reading ---


def sheet_labels(sheet: str) -> list[str]:
    return [name for name, _ in LABELS.get(sheet, GENERIC_LABELS)]


def cell(row: tuple[Any, ...], header: list[Any], column: str) -> str:
    return str(row[header.index(column)] or "").strip() if column in header else ""


def read_labels(path: Path, sheet: str) -> list[dict[str, str]]:
    """The labelled rows of the sheet, by NOTE_COLUMNS and span_ok ("" when the sheet has none); dies on
    a blank or unknown label, or on a span_ok outside SPAN_OK_VALUES when the column exists."""
    book = load_workbook(path, read_only=True)
    if sheet not in book.sheetnames:
        die(f"{path} has no {sheet} sheet; pass a sample_werk_links.py workbook or another --sheet")
    rows = book[sheet].values
    header = list(next(rows))
    for column in ("obj_id", "type_group", "label"):
        if column not in header:
            die(f"{path} sheet {sheet} has no {column} column")
    allowed = sheet_labels(sheet)
    found: list[dict[str, str]] = []
    problems: list[str] = []
    for number, row in enumerate(rows, start=2):
        record = {column: cell(row, header, column) for column in [*NOTE_COLUMNS, SPAN_OK]}
        if not record["obj_id"]:
            continue
        if record["label"] not in allowed:
            problems.append(f"row {number} ({record['obj_id']}): {record['label'] or 'blank'}")
        if SPAN_OK in header and record[SPAN_OK] not in SPAN_OK_VALUES:
            problems.append(f"row {number} ({record['obj_id']}): span_ok {record[SPAN_OK] or 'blank'}")
        found.append(record)
    book.close()
    if problems:
        die(f"{len(problems)} rows of {path} lack a label of {allowed} or a span_ok of {list(SPAN_OK_VALUES)}: "
            f"{'; '.join(problems[:5])}")  # fmt: skip
    if not found:
        die(f"{path} sheet {sheet} has no labelled rows")
    return found


# --- Tables ---


def by_group(labelled: list[dict[str, str]]) -> dict[str, Counter[str]]:
    """Label counts per type group, with ALL first."""
    groups: dict[str, Counter[str]] = {ALL: Counter(r["label"] for r in labelled)}
    for record in sorted(labelled, key=lambda r: (r["type_group"], r["label"])):
        groups.setdefault(record["type_group"], Counter())[record["label"]] += 1
    return groups


def precision_rows(groups: dict[str, Counter[str]]) -> list[list[Any]]:
    return [precision_row(d, g, labels) for d in DEFINITIONS for g, labels in groups.items()]


def label_rows(groups: dict[str, Counter[str]], sheet: str) -> list[list[Any]]:
    order = sheet_labels(sheet)
    return [[g, label, labels[label]] for g, labels in groups.items() for label in order if labels[label]]


def note_rows(labelled: list[dict[str, str]]) -> list[list[str]]:
    return [[r[column] for column in NOTE_COLUMNS] for r in labelled if r["note"]]


def span_ok_rows(labelled: list[dict[str, str]], sheet: str) -> list[list[Any]]:
    """Records per label and span_ok, then label ALL; empty when no row has a span_ok."""
    counts = Counter((r["label"], r[SPAN_OK]) for r in labelled if r[SPAN_OK])
    totals: Counter[str] = Counter()
    for (_, value), n in counts.items():
        totals[value] += n
    rows = [[label, value, counts[(label, value)]]
            for label in sheet_labels(sheet) for value in SPAN_OK_VALUES if counts[(label, value)]]  # fmt: skip
    return rows + [[ALL, value, totals[value]] for value in SPAN_OK_VALUES if totals[value]]


# --- Pre-flight and main ---


def output_paths(out_dir: Path, stamp: str, sheet: str) -> list[Path]:
    if sheet == LINKED:
        return [out_dir / f"werk_links_precision_{stamp}.csv", out_dir / f"werk_links_precision_labels_{stamp}.csv"]
    return [out_dir / f"werk_links_{part}_{sheet}_{stamp}.csv" for part in ("causes", "notes", "span_ok")]


def preflight(args: argparse.Namespace) -> list[Path]:
    if not args.workbook.is_file():
        die(f"--workbook {args.workbook} not found; pass the labelled workbook's path")
    outputs = output_paths(args.out_dir, args.date, args.sheet)
    if args.dry_run:
        return outputs
    if not args.out_dir.is_dir() or not os.access(args.out_dir, os.W_OK):
        die(f"{args.out_dir} is missing or not writable; create it or choose another --out-dir")
    existing = [p.name for p in outputs if p.exists()]
    if existing and not args.force:
        die(f"{', '.join(existing)} already exist; move them, or pass --force")
    return outputs


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Precision of the Werk links from a labelled sample workbook.")
    parser.add_argument("--workbook", type=Path, required=True)
    parser.add_argument("--sheet", default=LINKED, help="the labelled sheet; precision only for linked")
    parser.add_argument("--out-dir", type=Path, default=Path("data/processed/nerl-gnd"))
    parser.add_argument("--date", default=date.today().strftime("%Y%m%d"), help="yyyymmdd in the output names")
    parser.add_argument("--force", action="store_true", help="overwrite the outputs")
    parser.add_argument("--dry-run", action="store_true", help="check the labels and print the outputs; write nothing")
    args = parser.parse_args(argv)
    if not (len(args.date) == 8 and args.date.isdigit()):
        parser.error(f"--date {args.date} is not yyyymmdd")
    return args


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args(argv)
    outputs = preflight(args)
    labelled = read_labels(args.workbook, args.sheet)
    groups = by_group(labelled)
    if args.dry_run:
        print(f"would read\t{sum(groups[ALL].values())} labelled rows from {args.workbook}")
        for path in outputs:
            print(f"would write\t{path}")
        return 0

    logger.info("Read %s sheet %s (sha256 %s)", args.workbook, args.sheet, sha256(args.workbook))
    if args.sheet != LINKED:
        write_csv_atomic(outputs[0], LABEL_COLUMNS, label_rows(groups, args.sheet))
        write_csv_atomic(outputs[1], NOTE_COLUMNS, note_rows(labelled))
        written = outputs[:2]
        spans = span_ok_rows(labelled, args.sheet)
        if spans:
            write_csv_atomic(outputs[2], SPAN_OK_COLUMNS, spans)
            written.append(outputs[2])
        logger.info("Labels: %s", ", ".join(f"{label} {n}" for label, n in groups[ALL].most_common()))
        logger.info("Wrote %s", ", ".join(str(p) for p in written))
        return 0

    precision = precision_rows(groups)
    write_csv_atomic(outputs[0], PRECISION_COLUMNS, precision)
    write_csv_atomic(outputs[1], LABEL_COLUMNS, label_rows(groups, args.sheet))
    for row in precision:
        if row[1] == ALL:
            logger.info("%s precision %s (95 %% CI %s to %s), %d of %d, %d excluded",
                        row[0], row[5], row[6], row[7], row[2], row[3], row[4])  # fmt: skip
    logger.info("Wrote %s", ", ".join(str(p) for p in outputs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
