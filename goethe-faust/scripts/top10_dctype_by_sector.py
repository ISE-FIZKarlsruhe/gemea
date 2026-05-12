#!/usr/bin/env python3
# Purpose:  For each sector × mediatype, aggregate dc:type rows and emit the
#           top-10 dc:types by summed count.
# Usage:    python scripts/top10_dctype_by_sector.py
# Inputs:   output/dctype_sparte*.csv  (one file per sector)
# Outputs:  output/top10_dctype_by_sector.csv
#           Columns: sector, mediatype, htypes, dc_types, count,
#                    rdf_type_w, rdf_type_m, notes, mapped
# Deps:     stdlib only (csv, pathlib, collections, glob)

import csv
import re
from collections import defaultdict
from pathlib import Path

ROOT   = Path(__file__).resolve().parents[1]
OUTDIR = ROOT / "output"
OUTPUT = OUTDIR / "top10_dctype_by_sector.csv"

MT_LABELS = {
    "mt001": "AUDIO",
    "mt002": "PHOTO",
    "mt003": "TEXT",
    "mt005": "VIDEO",
    "mt007": "NOT DIGITIZED",
    "any":   "ANY",
}

SECTOR_RE = re.compile(r"dctype_(sparte\d+)\.csv$")


def coalesce(*values: str) -> str:
    """Return first non-empty value."""
    for v in values:
        if v:
            return v
    return ""


def main() -> None:
    # (sector, mediatype, dc_type_de) → aggregated data
    agg: dict[tuple, dict] = defaultdict(lambda: {
        "count": 0,
        "htypes": set(),
        "rdf_type_w": "",
        "rdf_type_m": "",
        "notes": set(),
        "mapped": "",
    })

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
                mt  = row["mediatype"].strip()
                dc  = row["dc_type_de"].strip()
                key = (sector, mt, dc)
                rec = agg[key]
                rec["count"] += int(row["count"] or 0)
                if row["htype"].strip():
                    rec["htypes"].add(row["htype"].strip())
                rec["rdf_type_w"] = coalesce(rec["rdf_type_w"], row["rdf_type_w"].strip())
                rec["rdf_type_m"] = coalesce(rec["rdf_type_m"], row["rdf_type_m"].strip())
                if row["notes"].strip():
                    rec["notes"].add(row["notes"].strip())
                rec["mapped"] = coalesce(rec["mapped"], row["mapped"].strip())

    # group by (sector, mediatype), rank by count, keep top 10
    groups: dict[tuple, list] = defaultdict(list)
    for (sector, mt, dc), rec in agg.items():
        groups[(sector, mt)].append((dc, rec))

    out_rows = []
    for (sector, mt) in sorted(groups):
        ranked = sorted(groups[(sector, mt)], key=lambda x: x[1]["count"], reverse=True)[:10]
        for dc, rec in ranked:
            out_rows.append({
                "sector":    sector,
                "mediatype": MT_LABELS.get(mt, mt),
                "htypes":    " | ".join(sorted(rec["htypes"])),
                "dc_types":  dc,
                "count":     rec["count"],
                "rdf_type_w": rec["rdf_type_w"],
                "rdf_type_m": rec["rdf_type_m"],
                "notes":     "; ".join(sorted(rec["notes"])),
                "mapped":    rec["mapped"],
            })

    fields = ["sector", "mediatype", "htypes", "dc_types", "count",
              "rdf_type_w", "rdf_type_m", "notes", "mapped"]
    with OUTPUT.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(out_rows)

    print(f"Wrote {len(out_rows)} rows → {OUTPUT}")


if __name__ == "__main__":
    main()
