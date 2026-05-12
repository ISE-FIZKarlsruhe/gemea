#!/usr/bin/env python3
"""
summarise_vocab_coverage.py

Purpose:
    Aggregate output/dctype_gnd_coverage.csv by (mediatype, sector) and write
    a human-readable summary CSV showing total dc:type occurrences, the count
    with a controlled-vocabulary URI (GND or Getty AAT), and the coverage %.

Usage:
    python scripts/summarise_vocab_coverage.py

Inputs:
    output/dctype_gnd_coverage.csv — per (mediatype, sector, dc_type_de) row;
        produced by count_dctype_gnd_coverage.py

Outputs:
    output/vocab_coverage_summary.csv
        Columns: mediatype, sector, total, vocab_uri, pct

Dependencies:
    stdlib only: csv, pathlib, collections, logging

Assumptions:
    - has_gnd == 'true' iff the row has a GND or Getty AAT URI
    - mediatype and sector are vocnet IRIs or 'any'
"""

import csv
import logging
from collections import defaultdict
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "output" / "dctype_gnd_coverage.csv"
OUTPUT = ROOT / "output" / "vocab_coverage_summary.csv"

MEDIATYPE_LABEL: dict[str, str] = {
    "http://ddb.vocnet.org/medientyp/mt001": "Audio",
    "http://ddb.vocnet.org/medientyp/mt002": "Photo",
    "http://ddb.vocnet.org/medientyp/mt003": "Text",
    "http://ddb.vocnet.org/medientyp/mt005": "Video",
    "http://ddb.vocnet.org/medientyp/mt007": "Not Digitized",
    "any": "any",
}

SECTOR_LABEL: dict[str, str] = {
    "http://ddb.vocnet.org/sparte/sparte001": "Archive",
    "http://ddb.vocnet.org/sparte/sparte002": "Library",
    "http://ddb.vocnet.org/sparte/sparte003": "Monument",
    "http://ddb.vocnet.org/sparte/sparte004": "Research",
    "http://ddb.vocnet.org/sparte/sparte005": "Media Library",
    "http://ddb.vocnet.org/sparte/sparte006": "Museum",
    "http://ddb.vocnet.org/sparte/sparte007": "Others",
    "any": "any",
}

# Sort order for output rows (mediatype IRI → sort key)
MT_ORDER = {
    "http://ddb.vocnet.org/medientyp/mt001": 1,
    "http://ddb.vocnet.org/medientyp/mt002": 2,
    "http://ddb.vocnet.org/medientyp/mt003": 3,
    "http://ddb.vocnet.org/medientyp/mt005": 4,
    "http://ddb.vocnet.org/medientyp/mt007": 5,
    "any": 6,
}

SEC_ORDER = {
    "http://ddb.vocnet.org/sparte/sparte001": 1,
    "http://ddb.vocnet.org/sparte/sparte002": 2,
    "http://ddb.vocnet.org/sparte/sparte003": 3,
    "http://ddb.vocnet.org/sparte/sparte004": 4,
    "http://ddb.vocnet.org/sparte/sparte005": 5,
    "http://ddb.vocnet.org/sparte/sparte006": 6,
    "http://ddb.vocnet.org/sparte/sparte007": 7,
    "any": 8,
}


def main() -> None:
    if not INPUT.exists():
        log.error("Input not found: %s", INPUT)
        raise SystemExit(1)

    # Aggregate by (mediatype_iri, sector_iri)
    totals: dict[tuple[str, str], int] = defaultdict(int)
    with_vocab: dict[tuple[str, str], int] = defaultdict(int)

    with INPUT.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            mt = row["mediatype"]
            sec = row["sector"]
            count = int(row["count"])
            totals[(mt, sec)] += count
            if row.get("has_gnd", "").strip().lower() == "true":
                with_vocab[(mt, sec)] += count

    # Build output rows
    out_rows = []
    for (mt, sec), total in totals.items():
        vocab = with_vocab.get((mt, sec), 0)
        pct = 100.0 * vocab / total if total else 0.0
        out_rows.append({
            "mediatype": MEDIATYPE_LABEL.get(mt, mt),
            "sector": SECTOR_LABEL.get(sec, sec),
            "total": total,
            "vocab_uri": vocab,
            "pct": f"{pct:.1f}",
        })

    out_rows.sort(key=lambda r: (
        MT_ORDER.get(
            next((k for k, v in MEDIATYPE_LABEL.items() if v == r["mediatype"]), ""),
            99,
        ),
        SEC_ORDER.get(
            next((k for k, v in SECTOR_LABEL.items() if v == r["sector"]), ""),
            99,
        ),
    ))

    with OUTPUT.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["mediatype", "sector", "total", "vocab_uri", "pct"])
        writer.writeheader()
        writer.writerows(out_rows)

    log.info("Wrote %d rows → %s", len(out_rows), OUTPUT)

    # Print table
    print(f"\n{'Mediatype':<16} {'Sector':<16} {'Total':>8} {'Vocab URI':>10} {'%':>7}")
    print("-" * 62)
    for r in out_rows:
        print(f"{r['mediatype']:<16} {r['sector']:<16} {r['total']:>8,} {r['vocab_uri']:>10,} {r['pct']:>6}%")


if __name__ == "__main__":
    main()
