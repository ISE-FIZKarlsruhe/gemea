#!/usr/bin/env python3
"""
analyse_years.py
================
Extract creation years from the DDB Goethe-Faust item dataset using the
edm:TimeSpan begin/end fields, determine the optimal year bucket size, and
produce a bar chart saved to output/fig_years.png.

Year source
-----------
edm.RDF.TimeSpan.begin  — preferred; represents the start of the date range
edm.RDF.TimeSpan.end    — fallback when begin is absent

Bucketing logic
---------------
The script inspects the full year range and per-bucket record density, then
automatically selects the most readable bucket size:
  - 5 years   : range ≤ 100 years  OR  the 5-year bins each average ≥ 50 records
  - 10 years  : range ≤ 300 years  OR  the decade bins each average ≥ 50 records
  - 25 years  : otherwise (very long range or sparse data)

Output
------
  output/fig_years.png        — bar chart of record counts per year bucket
  output/years-analysis.json  — raw year counts and bucketed counts

Usage
-----
    python scripts/analyse_years.py
"""

import json
import re
import math
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from collections import Counter
from pathlib import Path

PROJECT  = Path(__file__).resolve().parent.parent
IN_PATH  = PROJECT / "data" / "items-all-goethe-faust.json"
OUT_PNG  = PROJECT / "output" / "fig_years.png"
OUT_JSON = PROJECT / "output" / "years-analysis.json"

# Two alternatives:
#  1. year surrounded by non-digits (ISO, free-text)
#  2. year followed by exactly 4 more digits (YYYYMMDD, e.g. "18300213")
YEAR_RE = re.compile(
    r'(?<!\d)(1[0-9]{3}|20[0-2][0-9])(?!\d)'
    r'|(?<!\d)(1[0-9]{3}|20[0-2][0-9])(?=\d{4}(?!\d))'
)


# ── helpers ───────────────────────────────────────────────────────────────────

def extract_year(val):
    """Return the first 4-digit year (1000–2029) found in val, or None."""
    if not val:
        return None
    if isinstance(val, dict):
        val = val.get("$") or ""
    if isinstance(val, list):
        val = " ".join(str(v) for v in val)
    m = YEAR_RE.search(str(val))
    return int(m.group(1) or m.group(2)) if m else None


MAX_BINS = 45  # maximum non-empty bins before trying a wider bucket

def bucket_counts(year_counts, size, year_min, year_max):
    """Aggregate year_counts into buckets of width `size`."""
    start = (year_min // size) * size
    end   = ((year_max // size) + 1) * size
    bins  = {}
    for b in range(start, end, size):
        label = f"{b}–{b + size - 1}"
        bins[label] = sum(year_counts.get(y, 0) for y in range(b, b + size))
    return bins


MAX_BINS = 45  # maximum non-empty bins before trying a wider bucket

def choose_bucket(year_min, year_max, year_counts):
    """Return the smallest bucket size that produces ≤ MAX_BINS non-empty bins."""
    for size in (5, 10, 25, 50, 100):
        bins = bucket_counts(year_counts, size, year_min, year_max)
        non_empty = sum(1 for v in bins.values() if v > 0)
        if non_empty <= MAX_BINS:
            return size
    return 100


# ── scan dataset ──────────────────────────────────────────────────────────────

print("Scanning dataset ...")
year_counts  = Counter()   # year (int) → record count
missing      = 0
total        = 0

with open(IN_PATH) as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        total += 1

        ts = rec.get("edm", {}).get("RDF", {}).get("TimeSpan")
        if not isinstance(ts, dict):
            missing += 1
            continue

        year = extract_year(ts.get("begin")) or extract_year(ts.get("end"))
        if year:
            year_counts[year] += 1
        else:
            missing += 1

has_year = sum(year_counts.values())
year_min = min(year_counts)
year_max = max(year_counts)

print(f"Total records    : {total:,}")
print(f"Has year         : {has_year:,}  ({100*has_year/total:.1f}%)")
print(f"Missing year     : {missing:,}")
print(f"Year range       : {year_min} – {year_max}  (span {year_max - year_min} years)")

# ── choose bucket size ────────────────────────────────────────────────────────

size   = choose_bucket(year_min, year_max, year_counts)
bins   = bucket_counts(year_counts, size, year_min, year_max)
# Drop empty bins; restrict to 1600+ for a compact chart (pre-1600 is very sparse)
non_empty = [(k, v) for k, v in bins.items()
             if v > 0 and int(k.split("–")[0]) >= 1600]
labels    = [k for k, _ in non_empty]
counts    = [v for _, v in non_empty]

n_pre1600 = sum(v for k, v in bins.items()
                if v > 0 and int(k.split("–")[0]) < 1600)
print(f"\nSelected bucket size: {size} years  ({len(labels)} non-empty bins from 1600+)")
print(f"  Pre-1600 records omitted from chart: {n_pre1600:,}")

# ── save JSON ─────────────────────────────────────────────────────────────────

output = {
    "total_records":  total,
    "records_with_year": has_year,
    "records_missing_year": missing,
    "year_range": [year_min, year_max],
    "bucket_size": size,
    "raw_year_counts": {str(y): c for y, c in sorted(year_counts.items())},
    "bucketed_counts": dict(non_empty),
}
with open(OUT_JSON, "w", encoding="utf-8") as f:
    json.dump(output, f, indent=2, ensure_ascii=False)
print(f"Saved JSON  : {OUT_JSON}")

# ── plot ──────────────────────────────────────────────────────────────────────

plt.rcParams.update({
    "font.family": "sans-serif",
    "axes.spines.top":   False,
    "axes.spines.right": False,
    "figure.dpi": 150,
})

fig_w = max(10, len(labels) * 0.62)
fig, ax = plt.subplots(figsize=(fig_w, 6))

# Colour bars: highlight the 19th-century Goethe-era buckets
colors = []
for lbl in labels:
    start_yr = int(lbl.split("–")[0])
    if 1749 <= start_yr < 1850:
        colors.append("#C44E52")   # Goethe era (1749–1832): red
    else:
        colors.append("#4C72B0")   # default blue

bars = ax.bar(range(len(labels)), counts, color=colors, width=0.78, linewidth=0)

# X-axis: show only the start year of each bucket to avoid crowding
ax.set_xticks(range(len(labels)))
ax.set_xticklabels([lbl.split("–")[0] for lbl in labels],
                   rotation=60, ha="right", fontsize=8.5)
ax.set_ylabel("Number of records", fontsize=10)
ax.set_title(
    f"DDB Goethe-Faust Records by Year of Creation"
    f"  ·  {size}-year buckets  ·  N={sum(counts):,}  (1600–present)",
    fontsize=12, fontweight="bold", pad=10,
)
ax.grid(axis="y", alpha=0.35)
ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{int(x):,}"))

# Annotate bars: counts inside tall bars (white), above short bars (dark).
vmax = max(counts)
for bar, val in zip(bars, counts):
    if val == 0:
        continue
    if val >= 0.07 * vmax:
        # Tall enough: centre text inside the bar in white
        ax.text(bar.get_x() + bar.get_width() / 2, val / 2,
                f"{val:,}", ha="center", va="center", fontsize=6.5,
                rotation=90, color="white", fontweight="bold")
    else:
        # Short bar: place count just above, in the bar's own colour
        ax.text(bar.get_x() + bar.get_width() / 2, val + vmax * 0.012,
                f"{val:,}", ha="center", va="bottom", fontsize=6,
                rotation=90, color=bar.get_facecolor())

# Legend patch for highlighted era
from matplotlib.patches import Patch
legend_elements = [
    Patch(facecolor="#C44E52", label="Goethe era (1749–1832)"),
    Patch(facecolor="#4C72B0", label="Other"),
]
ax.legend(handles=legend_elements, frameon=False, fontsize=9, loc="upper left")

fig.tight_layout()
fig.savefig(OUT_PNG, bbox_inches="tight")
plt.close(fig)
print(f"Saved chart : {OUT_PNG}")
