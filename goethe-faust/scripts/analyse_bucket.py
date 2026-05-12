#!/usr/bin/env python3
"""
analyse_bucket.py
=================
Report the top N dc:type and dc:subject values for records within a given
timespan_begin year range, read from the items DataFrame parquet.

Useful for characterising anomalous buckets in the year distribution chart
(e.g. the 2000–2024 spike driven by Goethe-Universität institutional records).

Input
-----
  output/items-dataframe.parquet

Output
------
  Printed summary (no files written). Pass --json to emit JSON instead.

Usage
-----
    python scripts/analyse_bucket.py --start 2000 --end 2024
    python scripts/analyse_bucket.py --start 2000 --end 2024 --top 10
    python scripts/analyse_bucket.py --start 2000 --end 2024 --json

Arguments
---------
  --start  INT   First year of the bucket (inclusive). Default: 2000
  --end    INT   Last year of the bucket (inclusive).  Default: 2024
  --top    INT   Number of top entries to show.        Default: 3
  --json         Emit JSON to stdout instead of plain text

Dependencies
------------
  pandas, pyarrow
"""

import argparse
import json
from pathlib import Path

import pandas as pd

PROJECT = Path(__file__).resolve().parent.parent
PARQUET = PROJECT / "output" / "items-dataframe.parquet"


def top_values(series: pd.Series, n: int) -> list[dict]:
    """Return top-n value counts as a list of {value, count} dicts."""
    vc = series.explode().dropna()
    vc = vc[vc != ""]
    return [{"value": v, "count": int(c)} for v, c in vc.value_counts().head(n).items()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyse dc:type / dc:subject for a year bucket")
    parser.add_argument("--start", type=int, default=2000, metavar="YEAR")
    parser.add_argument("--end",   type=int, default=2024, metavar="YEAR")
    parser.add_argument("--top",   type=int, default=3,    metavar="N")
    parser.add_argument("--json",  action="store_true",    help="Emit JSON")
    args = parser.parse_args()

    df = pd.read_parquet(PARQUET)
    bucket = df[(df.timespan_begin >= args.start) & (df.timespan_begin <= args.end)]
    n = len(bucket)

    dc_type    = top_values(bucket["dc_type"],    args.top)
    dc_subject = top_values(bucket["dc_subject"], args.top)

    if args.json:
        print(json.dumps({
            "bucket": f"{args.start}–{args.end}",
            "record_count": n,
            "top_dc_type": dc_type,
            "top_dc_subject": dc_subject,
        }, ensure_ascii=False, indent=2))
        return

    print(f"Bucket {args.start}–{args.end}: {n:,} records")
    print()
    print(f"Top {args.top} dc:type:")
    for i, row in enumerate(dc_type, 1):
        pct = row["count"] / n * 100
        print(f"  {i}. {row['value']:<35}  {row['count']:>6,}  ({pct:.1f}%)")
    print()
    print(f"Top {args.top} dc:subject:")
    for i, row in enumerate(dc_subject, 1):
        pct = row["count"] / n * 100
        print(f"  {i}. {row['value']:<35}  {row['count']:>6,}  ({pct:.1f}%)")


if __name__ == "__main__":
    main()
