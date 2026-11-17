"""
Purpose:    Concatenate per-sector Parquet files from prescan.py into one combined file.
Usage:      python -m transform merge_parquet --indir <dir> --out <file>
Inputs:     <dir>/*_meta.parquet  — per-sector Parquet files produced by prescan.py
Outputs:    <file>                — combined Parquet (overwrites if present)
Deps:       pyarrow
Assumes:    All input files share the same schema (PARQUET_SCHEMA from prescan.py).
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import pyarrow.parquet as pq


def main() -> None:
    parser = argparse.ArgumentParser(description="Merge per-sector Parquet files")
    parser.add_argument("--indir", type=Path, required=True,
                        help="Directory containing *_meta.parquet files")
    parser.add_argument("--out",   type=Path, required=True,
                        help="Output combined Parquet file")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    log = logging.getLogger(__name__)

    inputs = sorted(args.indir.glob("*_meta.parquet"))
    if not inputs:
        log.error("No *_meta.parquet files found in %s", args.indir)
        raise SystemExit(1)

    log.info("Merging %d files → %s", len(inputs), args.out)
    writer = None
    total  = 0

    for path in inputs:
        tbl = pq.read_table(str(path))
        if writer is None:
            writer = pq.ParquetWriter(str(args.out), tbl.schema)
        writer.write_table(tbl)
        total += len(tbl)
        log.info("  %s: %d rows", path.name, len(tbl))

    if writer:
        writer.close()
        log.info("Done: %d total rows → %s", total, args.out)
    else:
        log.warning("No rows written.")


if __name__ == "__main__":
    main()
