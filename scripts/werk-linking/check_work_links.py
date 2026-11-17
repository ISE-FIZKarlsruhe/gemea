#!/usr/bin/env python3
# Purpose:      Check every DDB record that a Werk-linking run linked to one GND Work: extract the
#               linked records with their DDB title, creator, contributor, date, issued, type, publisher,
#               hierarchy, matched span and author source, next to the GND Work's names and
#               creators, and merge in a per-record check label from a labels file.
# Usage:        .venv/bin/python scripts/werk-linking/check_work_links.py --gnd-id 4766057-0 \
#                   --run-dir data/processed/nerl-gnd/rerun-d38 [--labels <labels.csv>] \
#                   [--meta data/out/s2/s2_meta_de_content.parquet] [--gnd-dir data/gnd/parquet] \
#                   [--output <csv>] [--date yyyymmdd] [--force] [--dry-run]
# Inputs:       --run-dir   a run folder with werk_links.csv (read row by row; rows with
#                           status == linked and gnd_uri == https://d-nb.info/gnd/<gnd-id>).
#               --meta      DDB metadata parquet, read in batches, keeping only the linked records.
#               --gnd-dir   gnd_work_names, gnd_work_creators, gnd_creator_names parquet tables.
#               --labels    optional CSV with obj_id, check_label, check_reason (one row per record).
# Outputs:      --output (default <run-dir>/work_links_check_<gnd-id>_<date>.csv): one row per linked
#               record; its first line is a '#' comment saying who made the labels.
#               With --labels also <output stem>_summary.csv: check_label, records.
#               Exit codes: 0 success, 1 failure, 2 usage error.
# Dependencies: pyarrow.
# Assumptions:  The labels in --labels are an AI's judgement from the metadata, not a hand check;
#               the comment line in the output says so. Without --labels the label columns stay
#               empty and no summary is written. Every linked record must have a label when
#               --labels is given, and every label must be one of LABELS.

from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, NoReturn

import pyarrow.parquet as pq  # type: ignore[import-untyped]

logger = logging.getLogger("check_work_links")

GND_PREFIX = "https://d-nb.info/gnd/"
DDB_ITEM = "https://www.deutsche-digitale-bibliothek.de/item/"
META_COLUMNS = ["obj_id", "title", "dc_creator", "dc_contributor", "dc_date", "dc_issued", "dc_type", "dc_publisher",
                "hierarchy_type", "is_part_of"]  # fmt: skip
BATCH_ROWS = 100_000
LABELS = ("same_work", "other_work", "part_or_volume", "edition_or_translation", "unsure")
LINK_COLUMNS = ["matched_name", "score", "author_match", "author_source", "matched_span",
                "matched_span_source", "search_terms", "candidate_count", "type_group"]  # fmt: skip
COLUMNS = (["obj_id", "ddb_url", "title", "dc_creator", "dc_contributor", "dc_date", "dc_issued", "dc_type", "dc_publisher",
            "hierarchy_type", "is_part_of"] + LINK_COLUMNS
           + ["gnd_uri", "gnd_preferred_name", "gnd_variant_names", "gnd_creators", "check_label", "check_reason"])  # fmt: skip
NOTE = ("# check_label and check_reason are an AI's judgement (Claude) from the DDB metadata and GND tables, "
        "not a hand check; verify before citing.")  # fmt: skip


def die(message: str) -> NoReturn:
    logger.error(message)
    sys.exit(1)


# --- Reading ---


def linked_rows(path: Path, gnd_uri: str) -> list[dict[str, str]]:
    """The run's linked rows for one GND Work, in file order."""
    with open(path, encoding="utf-8", newline="") as f:
        return [row for row in csv.DictReader(f) if row["status"] == "linked" and row["gnd_uri"] == gnd_uri]


def agents(values: list[dict[str, Any]] | None) -> str:
    """'label <uri>' per agent, joined by '; '."""
    parts = []
    for agent in values or []:
        label, uri = agent.get("label") or "", agent.get("uri") or ""
        parts.append(f"{label} <{uri}>" if uri else label)
    return "; ".join(parts)


def flat(value: Any) -> str:
    """A metadata value as text: lists joined by '; ', agents as 'label <uri>'."""
    if value is None:
        return ""
    if isinstance(value, list):
        if value and isinstance(value[0], dict):
            return agents(value)
        return "; ".join(flat(v) for v in value)
    if isinstance(value, dict):
        return "; ".join(f"{k}={flat(v)}" for k, v in value.items() if v)
    return str(value)


def load_meta(path: Path, wanted: set[str]) -> dict[str, dict[str, str]]:
    """Metadata of the wanted records, read batch by batch."""
    found: dict[str, dict[str, str]] = {}
    for batch in pq.ParquetFile(path).iter_batches(batch_size=BATCH_ROWS, columns=META_COLUMNS):
        for record in batch.to_pylist():
            if record["obj_id"] in wanted:
                found[record["obj_id"]] = {c: flat(record[c]) for c in META_COLUMNS if c != "obj_id"}
    return found


def load_gnd(gnd_dir: Path, work_id: str) -> dict[str, str]:
    """The Work's preferred name, variant names and creators ('role: name (id)')."""
    names = pq.read_table(gnd_dir / "gnd_work_names.parquet", filters=[("gnd_id", "=", work_id)]).to_pylist()
    creators = pq.read_table(gnd_dir / "gnd_work_creators.parquet", filters=[("gnd_id", "=", work_id)]).to_pylist()
    creator_ids = sorted({r["creator_id"] for r in creators})
    creator_names: dict[str, str] = {}
    if creator_ids:
        table = pq.read_table(gnd_dir / "gnd_creator_names.parquet", filters=[("gnd_id", "in", creator_ids)])
        creator_names = {r["gnd_id"]: r["name"] for r in table.to_pylist() if r["kind"] == "preferred"}
    return {
        "gnd_preferred_name": "; ".join(r["name"] for r in names if r["kind"] == "preferred"),
        "gnd_variant_names": "; ".join(r["name"] for r in names if r["kind"] != "preferred"),
        "gnd_creators": "; ".join(f"{r['role']}: {creator_names.get(r['creator_id'], '')} ({r['creator_id']})"
                                  for r in sorted(creators, key=lambda r: (r["role"], r["creator_id"]))),
    }  # fmt: skip


def load_labels(path: Path) -> dict[str, tuple[str, str]]:
    """obj_id -> (check_label, check_reason); dies on an unknown label or a duplicate obj_id."""
    labels: dict[str, tuple[str, str]] = {}
    with open(path, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row["check_label"] not in LABELS:
                die(f"{path}: {row['obj_id']} has label {row['check_label']!r}; use one of {', '.join(LABELS)}")
            if row["obj_id"] in labels:
                die(f"{path}: {row['obj_id']} appears twice; keep one row per record")
            labels[row["obj_id"]] = (row["check_label"], row["check_reason"])
    return labels


# --- Building and writing ---


def build(rows: list[dict[str, str]], meta: dict[str, dict[str, str]], gnd: dict[str, str],
          labels: dict[str, tuple[str, str]] | None) -> list[dict[str, str]]:  # fmt: skip
    """One output row per linked record, sorted by title, creator, date."""
    out = []
    for row in rows:
        record = {c: row.get(c, "") for c in COLUMNS}
        record.update(meta.get(row["obj_id"], {}))
        record.update(gnd)
        record["ddb_url"] = DDB_ITEM + row["obj_id"]
        label, reason = (labels or {}).get(row["obj_id"], ("", ""))
        record["check_label"], record["check_reason"] = label, reason
        out.append(record)
    return sorted(out, key=lambda r: (r["title"], r["dc_creator"], r["dc_date"], r["obj_id"]))


def write_atomic(path: Path, header: str | None, columns: list[str], rows: list[dict[str, Any]]) -> None:
    temp = path.with_name(path.name + ".tmp")
    try:
        with open(temp, "w", encoding="utf-8", newline="") as f:
            if header:
                f.write(header + "\n")
            writer = csv.DictWriter(f, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def summary_path(output: Path) -> Path:
    return output.with_name(output.stem + "_summary.csv")


# --- Pre-flight and main ---


def preflight(args: argparse.Namespace) -> None:
    for flag, path in (("--run-dir", args.links), ("--meta", args.meta)):
        if not path.is_file():
            die(f"{flag}: {path} not found; pass its path")
    tables = [args.gnd_dir / f"{t}.parquet" for t in ("gnd_work_names", "gnd_work_creators", "gnd_creator_names")]
    missing = [t.name for t in tables if not t.is_file()]
    if missing:
        die(f"{', '.join(missing)} not in {args.gnd_dir}; pass --gnd-dir")
    if args.labels and not args.labels.is_file():
        die(f"--labels {args.labels} not found")
    if args.dry_run:
        return
    folder = args.output.resolve().parent
    if not folder.is_dir() or not os.access(folder, os.W_OK):
        die(f"{folder} is missing or not writable; choose another --output")
    targets = [args.output] + ([summary_path(args.output)] if args.labels else [])
    existing = [str(t) for t in targets if t.exists()]
    if existing and not args.force:
        die(f"{', '.join(existing)} already exists; move it, or pass --force")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check the records a Werk-linking run linked to one GND Work.")
    parser.add_argument("--gnd-id", required=True, help="GND id of the Work, e.g. 4766057-0")
    parser.add_argument("--run-dir", type=Path, default=Path("data/processed/nerl-gnd/rerun-d38"))
    parser.add_argument("--meta", type=Path, default=Path("data/out/s2/s2_meta_de_content.parquet"))
    parser.add_argument("--gnd-dir", type=Path, default=Path("data/gnd/parquet"))
    parser.add_argument("--labels", type=Path, help="CSV with obj_id, check_label, check_reason")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--date", default=f"{date.today():%Y%m%d}", help="date in the default output name")
    parser.add_argument("--force", action="store_true", help="overwrite existing outputs")
    parser.add_argument("--dry-run", action="store_true", help="check the inputs and print the plan; write nothing")
    args = parser.parse_args(argv)
    if args.gnd_id.startswith(GND_PREFIX):
        args.gnd_id = args.gnd_id.removeprefix(GND_PREFIX)
    if not args.gnd_id or "/" in args.gnd_id:
        parser.error(f"--gnd-id {args.gnd_id!r}: expected a GND id such as 4766057-0")
    args.links = args.run_dir / "werk_links.csv"
    if args.output is None:
        args.output = args.run_dir / f"work_links_check_{args.gnd_id}_{args.date}.csv"
    return args


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args(argv)
    preflight(args)
    if args.dry_run:
        print(f"would read\t{args.links} (linked to {GND_PREFIX}{args.gnd_id})")
        print(f"would write\t{args.output}" + (f" and {summary_path(args.output)}" if args.labels else ""))
        return 0

    rows = linked_rows(args.links, GND_PREFIX + args.gnd_id)
    if not rows:
        die(f"no linked rows for {GND_PREFIX}{args.gnd_id} in {args.links}")
    logger.info("%d linked records", len(rows))
    meta = load_meta(args.meta, {row["obj_id"] for row in rows})
    if len(meta) < len(rows):
        logger.warning("%d linked records not in %s; their DDB columns stay empty", len(rows) - len(meta), args.meta)
    gnd = load_gnd(args.gnd_dir, args.gnd_id)
    labels = load_labels(args.labels) if args.labels else None
    if labels is not None:
        unlabelled = sorted({row["obj_id"] for row in rows} - set(labels))
        if unlabelled:
            die(f"{len(unlabelled)} linked records have no label in {args.labels}, e.g. {unlabelled[0]}")
        extra = set(labels) - {row["obj_id"] for row in rows}
        if extra:
            logger.warning("%d labels for records not linked to this Work; ignored", len(extra))

    out = build(rows, meta, gnd, labels)
    write_atomic(args.output, NOTE if labels else None, COLUMNS, out)
    logger.info("Wrote %s (%d rows)", args.output, len(out))
    if labels is not None:
        counts = Counter(r["check_label"] for r in out)
        summary = [{"check_label": label, "records": counts[label]} for label in LABELS]
        write_atomic(summary_path(args.output), NOTE, ["check_label", "records"], summary)
        logger.info("Wrote %s: %s", summary_path(args.output), dict(counts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
