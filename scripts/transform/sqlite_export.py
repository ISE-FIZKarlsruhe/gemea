"""Sequential SQLite → JSONL export for a single-sector corpus file (Option C)."""

from __future__ import annotations

import argparse
import gzip
import json
import logging
import sqlite3
from pathlib import Path

log = logging.getLogger(__name__)


def export(
    db_path: Path,
    out_path: Path,
    *,
    log_interval: int = 100_000,
    limit: int | None = None,
) -> int:
    """Export all records from a single-sector SQLite to a JSONL file.

    Sequential full-table scan of objs — no per-UID random access.
    Returns total records written.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    written = errors = 0

    try:
        with open(out_path, "w", encoding="utf-8") as out:
            for uid, buf in conn.execute("SELECT uid, bufgz FROM objs"):
                try:
                    out.write(json.dumps(json.loads(gzip.decompress(buf)), ensure_ascii=False) + "\n")
                    written += 1
                except Exception as exc:
                    errors += 1
                    log.warning("%s uid=%s error: %s", db_path.name, uid, exc)

                if written % log_interval == 0:
                    log.info("Exported %d records", written)
                if limit and written >= limit:
                    break
    finally:
        conn.close()

    if errors:
        log.warning("Skipped %d records due to decompress/parse errors", errors)
    log.info("Export complete: %d records → %s", written, out_path)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export a single-sector s{N}.sqlite to JSONL (Option C prep)"
    )
    parser.add_argument("--db",  type=Path, required=True,
                        help="Path to sector SQLite file (e.g. s2.sqlite)")
    parser.add_argument("--out", type=Path, required=True,
                        help="Output JSONL file path")
    parser.add_argument("--limit", type=int, default=None,
                        help="Stop after N records (for dryruns)")
    parser.add_argument("--log-level", default="INFO", dest="log_level",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(message)s",
    )

    n = export(args.db, args.out, limit=args.limit)
    print(f"{n:,} records → {args.out}")


if __name__ == "__main__":
    main()
