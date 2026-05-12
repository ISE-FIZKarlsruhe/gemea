#!/usr/bin/env python3
# Purpose:  Per sector × mediatype, compute:
#           (1) coverage ratios — share of records carrying htype only /
#               dc:type only / both / neither (dispatch priority signal);
#           (2) discriminating power — unique htype count and unique dc:type
#               count, expressed as density (unique values / total records).
#               The signal with higher density has finer semantic granularity
#               and warrants more detailed mapping work (ADR D1, D2).
# Usage:    python scripts/dispatch_signal_ratio.py
# Inputs:   output/dctype_sparte*.csv  (one file per sector)
# Outputs:  output/dispatch_signal_ratio.csv
#           Columns: sector, sector_label, mediatype, total,
#                    htype_only, htype_only_pct,
#                    dctype_only, dctype_only_pct,
#                    both, both_pct, neither, neither_pct,
#                    unique_htype, unique_dctype,
#                    htype_density, dctype_density,
#                    semantic_leader
# Deps:     stdlib only (csv, pathlib, collections, re)

import csv
import re
from collections import defaultdict
from pathlib import Path

ROOT   = Path(__file__).resolve().parents[1]
OUTDIR = ROOT / "output"
OUTPUT = OUTDIR / "dispatch_signal_ratio.csv"

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

CATS = ("htype_only", "dctype_only", "both", "neither")


def category(htype: str, dc: str) -> str:
    has_h = bool(htype)
    has_d = bool(dc)
    if has_h and has_d:
        return "both"
    if has_h:
        return "htype_only"
    if has_d:
        return "dctype_only"
    return "neither"


def pct(n: int, total: int) -> str:
    return f"{100 * n / total:.1f}" if total else "0.0"


def main() -> None:
    # (sector, mt) → {category → count}
    agg: dict[tuple, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    # (sector, mt) → set of unique values
    htype_sets:  dict[tuple, set] = defaultdict(set)
    dctype_sets: dict[tuple, set] = defaultdict(set)

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
                dc    = row["dc_type_de"].strip()
                count = int(row["count"] or 0)
                key   = (sector, mt)
                agg[key][category(htype, dc)] += count
                if htype:
                    htype_sets[key].add(htype)
                if dc:
                    dctype_sets[key].add(dc)

    out_rows = []
    for (sector, mt) in sorted(agg):
        cats         = agg[(sector, mt)]
        total        = sum(cats.values())
        u_htype      = len(htype_sets[(sector, mt)])
        u_dctype     = len(dctype_sets[(sector, mt)])
        h_density    = u_htype  / total if total else 0.0
        d_density    = u_dctype / total if total else 0.0
        if h_density > d_density:
            leader = "htype"
        elif d_density > h_density:
            leader = "dc:type"
        else:
            leader = "tied"
        out_rows.append({
            "sector":           sector,
            "sector_label":     SECTOR_LABELS.get(sector, sector),
            "mediatype":        MT_LABELS.get(mt, mt),
            "total":            total,
            "htype_only":       cats["htype_only"],
            "htype_only_pct":   pct(cats["htype_only"], total),
            "dctype_only":      cats["dctype_only"],
            "dctype_only_pct":  pct(cats["dctype_only"], total),
            "both":             cats["both"],
            "both_pct":         pct(cats["both"], total),
            "neither":          cats["neither"],
            "neither_pct":      pct(cats["neither"], total),
            "unique_htype":     u_htype,
            "unique_dctype":    u_dctype,
            "htype_density":    f"{h_density:.4f}",
            "dctype_density":   f"{d_density:.4f}",
            "semantic_leader":  leader,
        })

    fields = [
        "sector", "sector_label", "mediatype", "total",
        "htype_only", "htype_only_pct",
        "dctype_only", "dctype_only_pct",
        "both", "both_pct",
        "neither", "neither_pct",
        "unique_htype", "unique_dctype",
        "htype_density", "dctype_density",
        "semantic_leader",
    ]
    with OUTPUT.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(out_rows)

    print(f"Wrote {len(out_rows)} rows → {OUTPUT}\n")

    print(f"{'sector':<12} {'mt':<15} {'total':>7}  {'htype_only':>10}  {'dctype_only':>11}  {'both':>7}  {'neither':>8}  {'u_htype':>7}  {'u_dctype':>8}  {'leader':<8}")
    print("-" * 100)
    for r in out_rows:
        print(
            f"{r['sector']:<12} {r['mediatype']:<15} {r['total']:>7}  "
            f"{r['htype_only']:>5} ({r['htype_only_pct']:>5}%)  "
            f"{r['dctype_only']:>5} ({r['dctype_only_pct']:>5}%)  "
            f"{r['both']:>4} ({r['both_pct']:>5}%)  "
            f"{r['neither']:>4} ({r['neither_pct']:>5}%)  "
            f"{r['unique_htype']:>7}  {r['unique_dctype']:>8}  {r['semantic_leader']:<8}"
        )


if __name__ == "__main__":
    main()
