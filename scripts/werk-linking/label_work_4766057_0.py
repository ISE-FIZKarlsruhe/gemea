#!/usr/bin/env python3
# Purpose:      Labels for check_work_links.py --labels, for the records linked to GND Work 4766057-0
#               ("Geschichte der Teutschen", Michael Ignaz Schmidt; 5 parts 1778-85 per GND): turns the
#               patterns of the volume designations into check_label and check_reason per record.
# Usage:        .venv/bin/python scripts/werk-linking/label_work_4766057_0.py \
#                   --input <check_work_links.py output without --labels> --output <labels.csv> [--force]
# Inputs:       --input   CSV of check_work_links.py: obj_id, title, dc_issued, dc_type (a '#' first line is skipped).
# Outputs:      --output  CSV: obj_id, check_label, check_reason, designation.
#               Exit codes: 0 success, 1 failure, 2 usage error.
# Dependencies: standard library.
# Assumptions:  The patterns are an AI's judgement (Claude, 2026-11-16), chosen after reading all 235
#               volume designations of the D38 rerun; they hold for this Work only. A volume of the
#               continuation "Neuere Geschichte der Deutschen" (1544-1806) is labelled other_work,
#               following GND's note "Ersch. in 5 Teilen 1778-85"; if the series as published counts as
#               the Work, those rows are volumes instead. Not a hand check.

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from pathlib import Path

NEUERE = "other_work"
# Volume designations of the continuation "Neuere Geschichte der Deutschen" (1544-1806).
NEUERE_PATTERNS = [
    r"neuere geschichte der deutschen", r"schmalkaldischen krieg bis an das ende", r"fortsetzung",
    r"ferdinand (dem )?i\b", r"ferdinand dem ersten", r"ferdinand ii", r"rudolph (dem )?(ii|zweyten)",
    r"matth?ias", r"westph", r"1648 bis 1657", r"kaiser (leopold|joseph|karl|franz)", r"deutschlands zustand",
]
CONTINUATION_START = re.compile(r"kaiser (leopold|josep|karl|franz)|deutschlands zustand")
REGISTER = re.compile(r"register|verbesserungen und zus")


def designation(title: str) -> str:
    low = title.lower()
    at = low.find("geschichte der deutschen")
    return low[at + len("geschichte der deutschen"):].strip(" ,.") if at >= 0 else low


def judge(row: dict[str, str]) -> tuple[str, str]:
    title, issued = row["title"], row["dc_issued"]
    vol = designation(title)
    if row["dc_type"] == "mehrbändiges Werk" and not vol:
        reason = (
            f"multi-volume set record (issued {issued}); the set may also hold the "
            "Neuere Geschichte continuation, since dc_creator lists Milbiller"
        )
        return ("same_work", reason)
    is_neuere = title.lower().startswith("michael ignaz schmidts neuere") or any(re.search(p, vol) for p in NEUERE_PATTERNS)
    # The first part's last volume ends at 1544 with the start of the Schmalkaldic war; it is not Neuere.
    if "anfange des schmalkaldischen" in vol:
        is_neuere = False
    if is_neuere:
        span = ("1658-1806, the later continuation volumes"
                if CONTINUATION_START.search(vol) else "1544-1657")
        extra = "; also a book advertisement record (dc_type)" if row["dc_type"] == "Bücheranzeige" else ""
        reason = (
            f"volume of 'Neuere Geschichte der Deutschen' ({span}), not of the 5-part "
            f"'Geschichte der Teutschen' 1778-85 that GND describes; issued {issued}{extra}"
        )
        return (NEUERE, reason)
    if REGISTER.search(vol):
        return ("part_or_volume", f"index or corrections volume to the first 5 parts (to 1544); issued {issued}")
    if not vol:
        return ("unsure", "no volume designation after the title")
    return ("part_or_volume", f"volume '{vol[:60]}' of the Work (period to 1544); issued {issued}")


def read_rows(path: Path) -> list[dict[str, str]]:
    """The rows of a check_work_links.py CSV, skipping its '#' comment line."""
    with open(path, encoding="utf-8", newline="") as f:
        lines = [line for line in f if not line.startswith("#")]
    return list(csv.DictReader(lines))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Labels for the records linked to GND Work 4766057-0.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--force", action="store_true", help="overwrite the output")
    args = parser.parse_args(argv)
    if not args.input.is_file():
        parser.error(f"--input {args.input} is not a file")
    if args.output.exists() and not args.force:
        print(f"ERROR: {args.output} exists; pass --force to overwrite", file=sys.stderr)
        return 1
    rows = read_rows(args.input)
    temp = args.output.with_name(args.output.name + ".tmp")
    try:
        with open(temp, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["obj_id", "check_label", "check_reason", "designation"])
            for row in rows:
                label, reason = judge(row)
                writer.writerow([row["obj_id"], label, reason, designation(row["title"])[:120]])
        os.replace(temp, args.output)
    finally:
        temp.unlink(missing_ok=True)
    print(f"Wrote {args.output} ({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
