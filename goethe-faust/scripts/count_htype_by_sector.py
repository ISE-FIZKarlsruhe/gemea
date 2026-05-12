#!/usr/bin/env python3
# Purpose:  For each sector × mediatype, aggregate htype counts from all
#           dctype_sparte*.csv files.
# Usage:    python scripts/count_htype_by_sector.py
# Inputs:   output/dctype_sparte*.csv  (one file per sector)
#           data/ddbedm/ddbedm-htype.csv  (htype_code → label_en)
# Outputs:  output/htype_by_sector.csv
#           Columns: sector, sector_label, mediatype, htype, htype_label, count
# Deps:     stdlib only (csv, pathlib, collections, re)

import csv
import re
from collections import defaultdict
from pathlib import Path

ROOT   = Path(__file__).resolve().parents[1]
OUTDIR = ROOT / "output"
OUTPUT = OUTDIR / "htype_by_sector.csv"

MT_LABELS = {
    "mt001": "AUDIO",
    "mt002": "PHOTO",
    "mt003": "TEXT",
    "mt005": "VIDEO",
    "mt007": "NOT DIGITIZED",
    "any":   "ANY",
}

SECTOR_LABELS = {
    "sparte001": "Archive",
    "sparte002": "Library",
    "sparte003": "Monument Preservation",
    "sparte004": "Research",
    "sparte005": "Media Library",
    "sparte006": "Museum",
    "sparte007": "Others",
}

SECTOR_RE = re.compile(r"dctype_(sparte\d+)\.csv$")


def load_htype_labels() -> dict[str, str]:
    path = ROOT / "data" / "ddbedm" / "ddbedm-htype.csv"
    labels: dict[str, str] = {}
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            labels[row["htype_code"].strip()] = row["label_en"].replace("\n", " ").strip()
    return labels


def main() -> None:
    htype_labels = load_htype_labels()

    # (sector, mediatype, htype) → count
    agg: dict[tuple, int] = defaultdict(int)

    sector_files = sorted(OUTDIR.glob("dctype_sparte*.csv"))
    if not sector_files:
        raise FileNotFoundError(f"No dctype_sparte*.csv files found in {OUTDIR}")

    for path in sector_files:
        m = SECTOR_RE.search(path.name)
        if not m:
            continue
        sector = m.group(1)
        with path.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                mt    = row["mediatype"].strip()
                htype = row["htype"].strip()
                count = int(row["count"] or 0)
                agg[(sector, mt, htype)] += count

    out_rows = [
        {
            "sector":       sector,
            "sector_label": SECTOR_LABELS.get(sector, sector),
            "mediatype":    MT_LABELS.get(mt, mt),
            "htype":        htype,
            "htype_label":  htype_labels.get(htype, ""),
            "count":        count,
        }
        for (sector, mt, htype), count in sorted(
            agg.items(), key=lambda x: (x[0][0], x[0][1], -x[1])
        )
    ]

    fields = ["sector", "sector_label", "mediatype", "htype", "htype_label", "count"]
    with OUTPUT.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(out_rows)

    print(f"Wrote {len(out_rows)} rows → {OUTPUT}")


if __name__ == "__main__":
    main()
