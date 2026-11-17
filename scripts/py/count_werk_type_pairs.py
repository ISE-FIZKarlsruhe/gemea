#!/usr/bin/env python3
# Purpose:      Cross-count htype and dc:type per record of the Werk linking input, and count
#               the sets of rdf:type classes per GND Work. These counts check whether DDB
#               types map one to one onto GND Work classes.
# Usage:        .venv/bin/python scripts/py/count_werk_type_pairs.py
#                   [--input PATH] [--gnd-nt PATH] [--output-dir PATH] [--dry-run] [--force]
# Inputs:       --input parquet with string columns hierarchy_type, dc_type
#                   (default: data/out/s2/s2_meta_de_content.parquet).
#               --gnd-nt N-Triples of GND Works (default: data/gnd/nt/werk.nt).
# Outputs:      In --output-dir (default: data/processed/nerl-gnd/):
#                   s2_de_content_htype_x_dctype_20261107.csv: hierarchy_type, htype_group,
#                       dc_type, records
#                   gnd_werk_nt_type_sets_20261107.csv: type_set, subjects
#                   s2_de_content_type_groups_20261107.csv: group, gnd_classes, records, by the
#                       revised type matching (type groups by dc:type and htype)
#               Exit codes: 0 success, 1 failure, 2 usage error.
# Dependencies: pyarrow.
# Assumptions:  Streams the parquet. Holds one set of class names per typed GND subject in
#               memory (about 0.6 million subjects for the 2026-04-06 dump). htype groups are
#               those of count_werk_linking_types.py, imported unchanged. dc:type values are
#               matched exactly, except the prefix "Libretto".

from __future__ import annotations

import argparse
import logging
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
from count_werk_linking_types import by_count, die, htype_group, write_csv

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = ROOT / "data" / "out" / "s2" / "s2_meta_de_content.parquet"
DEFAULT_GND_NT = ROOT / "data" / "gnd" / "nt" / "werk.nt"
DEFAULT_OUTPUT_DIR = ROOT / "data" / "processed" / "nerl-gnd"
PAIRS_FILE = "s2_de_content_htype_x_dctype_20261107.csv"
TYPE_SETS_FILE = "gnd_werk_nt_type_sets_20261107.csv"
GROUPS_FILE = "s2_de_content_type_groups_20261107.csv"

MUSIC_DC_TYPES = {"Noten", "notated music", "Musiknoten", "Musikdruck", "Musikhandschrift"}
SONG_DC_TYPES = {"Lied", "Liederbuch", "Liedersammlung", "Kirchenlied", "Musikbuch"}
# The first group whose rule fits a record decides (type_group); the classes are searched in GND.
GND_CLASSES = {
    "music": "MusicalWork VersionOfAMusicalWork",
    "libretto": "MusicalWork Work",
    "song": "MusicalWork Work",
    "manuscript": "Manuscript CollectiveManuscript Work",
    "multivolume": "Work Collection",
    "work": "Work",
    "not_linked": "",
    "ignored": "",
    "unassigned": "",
}

TYPE_LINE_RE = re.compile(rb"^(<[^>]+>) <http://www\.w3\.org/1999/02/22-rdf-syntax-ns#type> <https://d-nb\.info/standards/elementset/gnd#(\w+)>")
BATCH_SIZE = 500_000

logger = logging.getLogger("count_type_pairs")


def count_pairs(path: Path) -> Counter[tuple[str, str]]:
    """Count (hierarchy_type, dc_type) pairs per record, streaming record batches."""
    parquet = pq.ParquetFile(path)
    missing = [c for c in ("hierarchy_type", "dc_type") if c not in parquet.schema_arrow.names]
    if missing:
        die(f"{path} has no column {', '.join(missing)}; pass the April s2_meta_de_content.parquet")
    pairs: Counter[tuple[str, str]] = Counter()
    for batch in parquet.iter_batches(batch_size=BATCH_SIZE, columns=["hierarchy_type", "dc_type"]):
        grouped = pa.Table.from_batches([batch]).group_by(["hierarchy_type", "dc_type"]).aggregate([([], "count_all")])
        for row in grouped.to_pylist():
            pairs[(row["hierarchy_type"] or "", row["dc_type"] or "")] += row["count_all"]
    return pairs


def count_type_sets(path: Path) -> Counter[str]:
    """Count the subjects per set of GND classes, such as 'MusicalWork+Work'."""
    classes: defaultdict[bytes, set[str]] = defaultdict(set)
    with open(path, "rb") as f:
        for line in f:
            match = TYPE_LINE_RE.match(line)
            if match:
                classes[match.group(1)].add(match.group(2).decode("ascii"))
    return Counter("+".join(sorted(names)) for names in classes.values())


def type_group(htype: str, dc_type: str) -> str:
    """The type group of one record: dc:type first, then the htype."""
    group = htype_group(htype or None)
    codes = set(htype.split())
    if group == "not_linked":
        return "not_linked"
    if dc_type in MUSIC_DC_TYPES or group == "music":
        return "music"
    if dc_type.startswith("Libretto"):
        return "libretto"
    if dc_type in SONG_DC_TYPES:
        return "song"
    if group == "manuscript":
        return "manuscript"
    if "htype_020" in codes:
        return "multivolume"
    if group == "work":
        return "work"
    if group == "no_htype":
        return "ignored"
    return "unassigned"


def group_rows(pairs: Counter[tuple[str, str]]) -> list[tuple[str, str, int]]:
    """Records per type group, with the GND classes it searches, in descending order."""
    groups: Counter[str] = Counter()
    for (htype, dc_type), n in pairs.items():
        groups[type_group(htype, dc_type)] += n
    return [(group, GND_CLASSES[group], n) for group, n in by_count(Counter(groups))]


def pair_rows(pairs: Counter[tuple[str, str]]) -> list[tuple[str, str, str, int]]:
    """Rows sorted by descending count, then by htype and dc:type, so that output is repeatable."""
    rows = [(htype, htype_group(htype or None), dc_type, n) for (htype, dc_type), n in pairs.items()]
    return sorted(rows, key=lambda r: (-r[3], r[0], r[2]))


def preflight(args: argparse.Namespace) -> None:
    for path, flag in ((args.input, "--input"), (args.gnd_nt, "--gnd-nt")):
        if not path.is_file():
            die(f"{path} not found; pass the file with {flag}")
    existing = [args.output_dir / n for n in (PAIRS_FILE, TYPE_SETS_FILE, GROUPS_FILE) if (args.output_dir / n).exists()]
    if existing and not args.force and not args.dry_run:
        die(f"{existing[0]} exists; pass --force to overwrite it")
    if not args.dry_run:
        try:
            args.output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            die(f"cannot create {args.output_dir} ({e}); choose another --output-dir")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Cross-count htype and dc:type, and GND class sets per Work.")
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
    pairs = count_pairs(args.input)
    type_sets = count_type_sets(args.gnd_nt)
    logger.info("%d htype and dc:type pairs over %d records", len(pairs), sum(pairs.values()))
    logger.info("GND class sets: %s", dict(type_sets))
    groups = group_rows(pairs)
    logger.info("Type groups: %s", {group: n for group, _, n in groups})
    if args.dry_run:
        logger.info("Dry run: wrote nothing")
        return 0
    write_csv(args.output_dir / PAIRS_FILE, ["hierarchy_type", "htype_group", "dc_type", "records"], pair_rows(pairs))
    write_csv(args.output_dir / TYPE_SETS_FILE, ["type_set", "subjects"], by_count(Counter(type_sets)))
    write_csv(args.output_dir / GROUPS_FILE, ["group", "gnd_classes", "records"], groups)
    logger.info("Wrote 3 files to %s", args.output_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
