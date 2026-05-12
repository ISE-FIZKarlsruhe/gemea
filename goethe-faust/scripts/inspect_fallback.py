#!/usr/bin/env python3
# Purpose:  Extract records that fell through to fallback D9 in validate_dispatch.py.
#           Groups by (sector, mediatype, htype, dc_type) and lists item UIDs per group.
# Usage:    python scripts/inspect_fallback.py
# Inputs:   output/dispatch_validation.csv
# Outputs:  output/dispatch_fallback.csv
#           Columns: sector, mediatype, htype, dc_type, count, item_ids
# Deps:     stdlib only (csv, pathlib, collections)

import csv
from collections import defaultdict
from pathlib import Path

ROOT   = Path(__file__).resolve().parents[1]
INPUT  = ROOT / "output" / "dispatch_validation.csv"
OUTPUT = ROOT / "output" / "dispatch_fallback.csv"

groups: dict = defaultdict(list)

with INPUT.open(newline="", encoding="utf-8") as fh:
    for row in csv.DictReader(fh):
        if row["dispatch_rule"] == "fallback D9":
            key = (row["sector"], row["mediatype"], row["htype"], row["dc_type"])
            groups[key].append(row["item_id"])

fields = ["sector", "mediatype", "htype", "dc_type", "count", "item_ids"]
with OUTPUT.open("w", newline="", encoding="utf-8") as fh:
    writer = csv.DictWriter(fh, fieldnames=fields)
    writer.writeheader()
    for (sector, mt, htype, dc_type), ids in sorted(groups.items()):
        writer.writerow({
            "sector":    sector,
            "mediatype": mt,
            "htype":     htype,
            "dc_type":   dc_type,
            "count":     len(ids),
            "item_ids":  " | ".join(ids),
        })

print(f"Wrote {len(groups)} groups ({sum(len(v) for v in groups.values())} records) → {OUTPUT}")
