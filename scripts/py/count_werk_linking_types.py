#!/usr/bin/env python3
# Purpose:      Count the htypes and dc:type values of the Werk linking input, group the htypes
#               by the type matching rule, and count the rdf:type lines of the GND Work dump.
# Usage:        .venv/bin/python scripts/py/count_werk_linking_types.py
#                   [--input PATH] [--gnd-nt PATH] [--output-dir PATH] [--dry-run] [--force]
# Inputs:       --input parquet with string columns hierarchy_type, dc_type
#                   (default: data/out/s2/s2_meta_de_content.parquet).
#               --gnd-nt N-Triples of GND Works (default: data/gnd/nt/werk.nt).
# Outputs:      In --output-dir (default: data/processed/nerl-gnd/):
#                   s2_de_content_htype_dctype_20261105.csv: column, value, records
#                   s2_de_content_htype_groups_20261105.csv: group, records
#                   gnd_werk_nt_types_20261105.csv: gnd_type, lines
#               Exit codes: 0 success, 1 failure, 2 usage error.
# Dependencies: pyarrow.
# Assumptions:  Streams both inputs. A multi-valued htype counts in the first group, in
#               GROUP_ORDER, that has one of its codes. The grouping is a proposal
#               not validated on GND.

from __future__ import annotations

import argparse
import csv
import logging
import re
import sys
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import NoReturn

import pyarrow.compute as pc  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = ROOT / "data" / "out" / "s2" / "s2_meta_de_content.parquet"
DEFAULT_GND_NT = ROOT / "data" / "gnd" / "nt" / "werk.nt"
DEFAULT_OUTPUT_DIR = ROOT / "data" / "processed" / "nerl-gnd"
VALUES_FILE = "s2_de_content_htype_dctype_20261105.csv"
GROUPS_FILE = "s2_de_content_htype_groups_20261105.csv"
GND_FILE = "gnd_werk_nt_types_20261105.csv"

HTYPE_GROUPS = {
    "work": {
        "htype_021",
        "htype_007",
        "htype_020",
        "htype_003",
        "htype_026",
        "htype_027",
        "htype_012",
        "htype_011",
        "htype_040",
        "htype_024",
    },
    "manuscript": {"htype_013"},
    "music": {"htype_022"},
    "not_linked": {"htype_014", "htype_006", "htype_008", "htype_015", "htype_019", "htype_023", "htype_038"},
}
GROUP_ORDER = ["music", "manuscript", "work", "not_linked"]
GND_TYPE_RE = re.compile(rb"rdf-syntax-ns#type> <https://d-nb\.info/standards/elementset/gnd#(\w+)>")
BATCH_SIZE = 500_000

logger = logging.getLogger("count_types")


def die(message: str) -> NoReturn:
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def htype_group(htype: str | None) -> str:
    """The group of an htype value, which may hold several space-separated codes."""
    if not htype:
        return "no_htype"
    codes = set(htype.split())
    for group in GROUP_ORDER:
        if codes & HTYPE_GROUPS[group]:
            return group
    return "unassigned"


def count_parquet(path: Path) -> tuple[Counter[str | None], Counter[str | None]]:
    """Count the hierarchy_type and dc_type values, streaming record batches."""
    parquet = pq.ParquetFile(path)
    missing = [c for c in ("hierarchy_type", "dc_type") if c not in parquet.schema_arrow.names]
    if missing:
        die(f"{path} has no column {', '.join(missing)}; pass the April s2_meta_de_content.parquet")
    htypes: Counter[str | None] = Counter()
    dc_types: Counter[str | None] = Counter()
    for batch in parquet.iter_batches(batch_size=BATCH_SIZE, columns=["hierarchy_type", "dc_type"]):
        for counter, column in ((htypes, "hierarchy_type"), (dc_types, "dc_type")):
            for row in pc.value_counts(batch.column(column)).to_pylist():
                counter[row["values"]] += row["counts"]
    return htypes, dc_types


def count_gnd_types(path: Path) -> Counter[str]:
    """Count the rdf:type lines per GND class, reading the N-Triples line by line."""
    types: Counter[str] = Counter()
    with open(path, "rb") as f:
        for line in f:
            match = GND_TYPE_RE.search(line)
            if match:
                types[match.group(1).decode("ascii")] += 1
    return types


def group_counts(htypes: Counter[str | None]) -> Counter[str]:
    groups: Counter[str] = Counter()
    for htype, records in htypes.items():
        groups[htype_group(htype)] += records
    return groups


def write_csv(path: Path, header: list[str], rows: Iterable[Iterable[object]]) -> None:
    """Write one CSV atomically."""
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)
    tmp.replace(path)


def by_count(counter: Counter[str | None]) -> list[tuple[str, int]]:
    """Rows sorted by descending count, then by value, so that output is repeatable."""
    return sorted((("" if key is None else key, value) for key, value in counter.items()), key=lambda r: (-r[1], r[0]))


def preflight(args: argparse.Namespace) -> None:
    for path, flag in ((args.input, "--input"), (args.gnd_nt, "--gnd-nt")):
        if not path.is_file():
            die(f"{path} not found; pass the file with {flag}")
    existing = [args.output_dir / n for n in (VALUES_FILE, GROUPS_FILE, GND_FILE) if (args.output_dir / n).exists()]
    if existing and not args.force and not args.dry_run:
        die(f"{existing[0]} exists; pass --force to overwrite it")
    if not args.dry_run:
        try:
            args.output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            die(f"cannot create {args.output_dir} ({e}); choose another --output-dir")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Count htypes, dc:type values and GND Work types.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--gnd-nt", type=Path, default=DEFAULT_GND_NT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--dry-run", action="store_true", help="count and log, but write nothing")
    parser.add_argument("--force", action="store_true", help="overwrite existing output")
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args()
    preflight(args)
    htypes, dc_types = count_parquet(args.input)
    groups = group_counts(htypes)
    gnd_types = count_gnd_types(args.gnd_nt)
    logger.info("Groups: %s", dict(groups))
    logger.info("GND types: %s", dict(gnd_types))
    if args.dry_run:
        logger.info("Dry run: wrote nothing")
        return 0
    values = [("hierarchy_type", *row) for row in by_count(htypes)] + [("dc_type", *row) for row in by_count(dc_types)]
    write_csv(args.output_dir / VALUES_FILE, ["column", "value", "records"], values)
    write_csv(args.output_dir / GROUPS_FILE, ["group", "records"], by_count(Counter(groups)))
    write_csv(args.output_dir / GND_FILE, ["gnd_type", "lines"], by_count(Counter(gnd_types)))
    logger.info("Wrote 3 files to %s", args.output_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
