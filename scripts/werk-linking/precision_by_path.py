#!/usr/bin/env python3
# Purpose:      Precision of a labelled sample of Werk links per path graph of one run (rule-span,
#               model-span), for provcred:measuredEvaluation:
#               the sample's labels are joined with the run's werk_links.csv, so each sampled link
#               gets the path it has in that run.
# Usage:        .venv/bin/python scripts/werk-linking/precision_by_path.py \
#                   --workbook data/annotation/<labelled>.xlsx --run-dir <run folder> \
#                   [--sheet linked] [--date yyyymmdd] [--force] [--dry-run]
# Inputs:       --workbook  a labelled sample_werk_links.py workbook; its --sheet has obj_id, gnd_url
#                           and label on every row.
#               --run-dir   a run folder with werk_links.csv (read row by row).
# Outputs:      <run-dir>/werk_links_precision_by_path_<date>.csv: path, definition, hits, n, excluded,
#               precision, wilson_low, wilson_high, sample_sha256, workbook_sha256; paths rule-span,
#               model-span and all; definitions strict and lenient.
#               Exit codes: 0 success, 1 failure, 2 usage error.
# Dependencies: openpyxl; linking.py beside this file.
# Assumptions:  strict counts only "correct" as a hit, lenient also "related"; "unsure" is left out
#               of n (as scripts/py/werk_links_precision.py). A sampled record that the run does
#               not link to the same GND Work stops the script: its label would not describe this
#               run's link. sample_sha256 names a path's sample: the SHA-256 of its obj_ids, sorted,
#               one per line. The sample was not drawn per path, so a path's n can be small.

from __future__ import annotations

import argparse
import csv
import hashlib
import logging
import math
import os
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import NoReturn

import linking
from openpyxl import load_workbook

DEFINITIONS = {"strict": frozenset({"correct"}), "lenient": frozenset({"correct", "related"})}
KNOWN_LABELS = frozenset({"correct", "wrong_work", "related", "not_a_work_title", "unsure"})
EXCLUDED = frozenset({"unsure"})
Z_95 = 1.959963984540054
ALL = "all"
COLUMNS = ["path", "definition", "hits", "n", "excluded", "precision", "wilson_low", "wilson_high", "sample_sha256", "workbook_sha256"]

logger = logging.getLogger("precision_by_path")


def die(message: str) -> NoReturn:
    logger.error(message)
    sys.exit(1)


def wilson(hits: int, n: int, z: float = Z_95) -> tuple[float, float]:
    """Wilson score interval of hits out of n."""
    if n == 0:
        return math.nan, math.nan
    p = hits / n
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return (0.0 if hits == 0 else centre - half), (1.0 if hits == n else centre + half)


def gnd_id(uri: str) -> str:
    return uri.rstrip("/").rsplit("/", 1)[-1]


def read_labels(workbook: Path, sheet: str) -> dict[str, tuple[str, str]]:
    """obj_id -> (GND ID, label) of the labelled sheet."""
    book = load_workbook(workbook, read_only=True)
    if sheet not in book.sheetnames:
        die(f"{workbook} has no sheet {sheet!r}; it has {', '.join(book.sheetnames)}")
    rows = book[sheet].iter_rows(values_only=True)
    head = [str(cell) if cell is not None else "" for cell in next(rows)]
    missing = {"obj_id", "gnd_url", "label"} - set(head)
    if missing:
        die(f"sheet {sheet!r} has no column {', '.join(sorted(missing))}; pass a sample_werk_links.py workbook")
    labels: dict[str, tuple[str, str]] = {}
    for row in rows:
        record = dict(zip(head, row))
        if not record["obj_id"]:
            continue  # blank rows Excel added
        label = str(record["label"] or "").strip()
        if label not in KNOWN_LABELS:
            die(f"sheet {sheet!r}, obj_id {record['obj_id']}: label {label!r} is not one of {', '.join(sorted(KNOWN_LABELS))}")
        labels[str(record["obj_id"])] = (gnd_id(str(record["gnd_url"])), label)
    if not labels:
        die(f"sheet {sheet!r} of {workbook} has no labelled rows")
    return labels


def paths_in_run(links: Path, labels: dict[str, tuple[str, str]]) -> dict[str, str]:
    """obj_id -> path of each sampled link in this run; stops if a sampled link is not the run's link."""
    paths: dict[str, str] = {}
    with open(links, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            sampled = labels.get(row["obj_id"])
            if sampled is None:
                continue
            if row["status"] != "linked" or gnd_id(row["gnd_uri"]) != sampled[0]:
                die(f"{row['obj_id']} is labelled for GND {sampled[0]}, but the run has status {row['status']}, {row['gnd_uri'] or 'no Work'}")
            paths[row["obj_id"]] = linking.path_of(row["matched_span_source"])
    absent = sorted(set(labels) - set(paths))
    if absent:
        die(f"{len(absent)} sampled records are not in {links} (first: {absent[0]}); pass the run the sample describes")
    return paths


def sample_sha256(obj_ids: list[str]) -> str:
    return hashlib.sha256("".join(f"{obj_id}\n" for obj_id in sorted(obj_ids)).encode("utf-8")).hexdigest()


def precision_rows(labels: dict[str, tuple[str, str]], paths: dict[str, str], workbook_sha256: str) -> list[list[str | int]]:
    """One row per path (and all) and definition."""
    table: list[list[str | int]] = []
    for path in [*sorted(set(paths.values())), ALL]:
        obj_ids = [obj_id for obj_id in labels if path in (ALL, paths[obj_id])]
        counts = Counter(labels[obj_id][1] for obj_id in obj_ids)
        excluded = sum(counts[label] for label in EXCLUDED)
        n = len(obj_ids) - excluded
        for definition, hit_labels in DEFINITIONS.items():
            hits = sum(counts[label] for label in hit_labels)
            low, high = wilson(hits, n)
            precision = f"{hits / n:.4f}" if n else ""
            table.append([path, definition, hits, n, excluded, precision, f"{low:.4f}", f"{high:.4f}", sample_sha256(obj_ids), workbook_sha256])
    return table


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Precision of a labelled sample of Werk links per path graph of one run.")
    parser.add_argument("--workbook", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--sheet", default="linked")
    parser.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    parser.add_argument("--force", action="store_true", help="overwrite the output")
    parser.add_argument("--dry-run", action="store_true", help="print the table; write nothing")
    args = parser.parse_args(argv)
    if not args.workbook.is_file():
        parser.error(f"--workbook {args.workbook} is not a file")
    if not (args.run_dir / "werk_links.csv").is_file():
        parser.error(f"--run-dir {args.run_dir} has no werk_links.csv")
    if not re.fullmatch(r"\d{8}", args.date):
        parser.error("--date must be yyyymmdd")
    return args


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args(argv)
    output = args.run_dir / f"werk_links_precision_by_path_{args.date}.csv"
    if not args.dry_run:
        if output.exists() and not args.force:
            die(f"{output} exists; pass --force to overwrite")
        if not os.access(args.run_dir, os.W_OK):
            die(f"{args.run_dir} is not writable")
    labels = read_labels(args.workbook, args.sheet)
    try:
        paths = paths_in_run(args.run_dir / "werk_links.csv", labels)
    except linking.RowError as e:
        die(f"{args.run_dir / 'werk_links.csv'}: {e}")
    table = precision_rows(labels, paths, hashlib.sha256(args.workbook.read_bytes()).hexdigest())
    if args.dry_run:
        print(",".join(COLUMNS))
        for row in table:
            print(",".join(str(value) for value in row))
        return 0
    temp = output.with_name(output.name + ".tmp")
    try:
        with open(temp, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(COLUMNS)
            writer.writerows(table)
        os.replace(temp, output)
    finally:
        temp.unlink(missing_ok=True)
    logger.info("Wrote %s (%d sampled links)", output, len(labels))
    return 0


if __name__ == "__main__":
    sys.exit(main())
