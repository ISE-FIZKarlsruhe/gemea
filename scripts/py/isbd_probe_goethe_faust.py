#!/usr/bin/env python3
# Purpose:      Run the bibner-werk ISBD rule and the linking script's search_term over
#               the titles of a metadata parquet, to see what step 1 of Werk linking
#               would give step 2. It does not query GND.
# Usage:        .venv/bin/python scripts/py/isbd_probe_goethe_faust.py
#                   [--input PATH] [--rule-script PATH] [--output PATH] [--dry-run] [--force]
# Inputs:       --input parquet with columns obj_id, title, dc_creator (default: the
#                   Goethe-Faust parquet in the sibling goethe-faust repo).
#               --rule-script Python file with detect_flags() and extract_spans() (default:
#                   t10_rule_baseline.py in the sibling bibner-werk repo; loaded, not changed).
# Outputs:      --output CSV with columns metric, value
#                   (default: data/processed/nerl-gnd/isbd_probe_goethe_faust_20261105.csv).
#               The log shows the spans of --probe-obj-id and the most frequent terms.
#               Exit codes: 0 success, 1 failure, 2 usage error.
# Dependencies: pyarrow, rapidfuzz (through werk_link_rules.py).
# Assumptions:  Read-only on its inputs. A probe of the rule, not a producer of spans:
#               only the TITLE span is used for the search term.

from __future__ import annotations

import argparse
import csv
import importlib.util
import logging
import sys
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any, NoReturn

import pyarrow.parquet as pq  # type: ignore[import-untyped]

sys.path.insert(0, str(Path(__file__).resolve().parent))
from werk_link_rules import search_term

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = ROOT.parent / "goethe-faust" / "output" / "items-all-goethe-faust_meta.parquet"
DEFAULT_RULE_SCRIPT = ROOT.parent / "bibner-werk" / "scripts" / "py" / "t10_rule_baseline.py"
DEFAULT_OUTPUT = ROOT / "data" / "processed" / "nerl-gnd" / "isbd_probe_goethe_faust_20261105.csv"
DEFAULT_PROBE_OBJ_ID = "NEL6FUWBOR4IGNBIWEITW63XM7TWO46L"

REQUIRED_COLUMNS = ["obj_id", "title", "dc_creator"]
BATCH_SIZE = 10_000
TOP_TERMS = 10

logger = logging.getLogger("isbd_probe")


def die(message: str) -> NoReturn:
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def load_rule(path: Path) -> ModuleType:
    """Load the rule script from its path, and check that it has the two functions used here."""
    spec = importlib.util.spec_from_file_location("isbd_rule", path)
    if spec is None or spec.loader is None:
        die(f"cannot load {path} as Python; pass a .py file with --rule-script")
    module = importlib.util.module_from_spec(spec)
    sys.modules["isbd_rule"] = module
    spec.loader.exec_module(module)
    for name in ("detect_flags", "extract_spans"):
        if not hasattr(module, name):
            die(f"{path} has no {name}(); pass the ISBD rule script with --rule-script")
    return module


def read_records(path: Path) -> Iterator[tuple[str, str | None, Any]]:
    """Stream (obj_id, title, dc_creator) from the parquet."""
    parquet = pq.ParquetFile(path)
    missing = [c for c in REQUIRED_COLUMNS if c not in parquet.schema_arrow.names]
    if missing:
        die(f"{path} has no column {', '.join(missing)}; pass a parquet from the April generation")
    for batch in parquet.iter_batches(batch_size=BATCH_SIZE, columns=REQUIRED_COLUMNS):
        yield from zip(*(batch.column(name).to_pylist() for name in REQUIRED_COLUMNS))


def summarize(
    records: Iterator[tuple[str, str | None, Any]], rule: ModuleType, probe_obj_id: str
) -> tuple[dict[str, int], Counter[str]]:
    """Count the rule's flags and the search terms of the TITLE spans."""
    counts = dict.fromkeys(
        ["records", "with_dc_creator", "no_title", "has_dot_dash", "has_colon", "has_slash", "no_search_term"], 0
    )
    terms: Counter[str] = Counter()
    for obj_id, title, creator in records:
        counts["records"] += 1
        if creator:
            counts["with_dc_creator"] += 1
        if not title:
            counts["no_title"] += 1
            continue
        flags = rule.detect_flags(title)
        counts["has_dot_dash"] += flags[0]
        counts["has_colon"] += flags[1]
        counts["has_slash"] += flags[2]
        spans = rule.extract_spans(title, *flags)
        span_title = next((title[start:end] for start, end, label in spans if label == "TITLE"), "")
        term = search_term(span_title) if span_title else None
        if term:
            terms[term] += 1
        else:
            counts["no_search_term"] += 1
        if obj_id == probe_obj_id:
            logger.info("%s: %r -> %s, term: %r", obj_id, title, spans, term)
    counts["distinct_terms"] = len(terms)
    counts["records_with_top10_terms"] = sum(count for _, count in terms.most_common(TOP_TERMS))
    counts["terms_seen_once"] = sum(1 for count in terms.values() if count == 1)
    return counts, terms


def write_csv(counts: dict[str, int], output: Path) -> None:
    """Write the metrics atomically, sorted by name."""
    tmp = output.with_name(output.name + ".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(["metric", "value"])
        writer.writerows(sorted(counts.items()))
    tmp.replace(output)


def preflight(args: argparse.Namespace) -> None:
    if not args.input.is_file():
        die(f"{args.input} not found; pass the metadata parquet with --input")
    if not args.rule_script.is_file():
        die(f"{args.rule_script} not found; pass the ISBD rule script with --rule-script")
    if args.output.exists() and not args.force:
        die(f"{args.output} exists; pass --force to overwrite it")
    if not args.dry_run:
        try:
            args.output.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            die(f"cannot create {args.output.parent} ({e}); choose another --output")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe the ISBD rule on the titles of a metadata parquet.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--rule-script", type=Path, default=DEFAULT_RULE_SCRIPT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--probe-obj-id", default=DEFAULT_PROBE_OBJ_ID)
    parser.add_argument("--dry-run", action="store_true", help="count and log, but write nothing")
    parser.add_argument("--force", action="store_true", help="overwrite an existing --output")
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args()
    preflight(args)
    rule = load_rule(args.rule_script)
    counts, terms = summarize(read_records(args.input), rule, args.probe_obj_id)
    for name, value in sorted(counts.items()):
        logger.info("%s: %d", name, value)
    logger.info("Most frequent terms: %s", terms.most_common(TOP_TERMS))
    if args.dry_run:
        logger.info("Dry run: wrote nothing")
        return 0
    write_csv(counts, args.output)
    logger.info("Wrote %s", args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
