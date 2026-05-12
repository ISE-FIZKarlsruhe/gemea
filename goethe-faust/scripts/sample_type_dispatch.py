#!/usr/bin/env python3
"""
sample_type_dispatch.py

Purpose:
    Validate the dc:type dispatch table by sampling records per (mediatype, sector)
    cell, running the three-level lookup against lookup_dctype_to_class.csv, and
    reporting which class would be assigned to each sampled record.

    Used to verify dispatch results before modifying transform_edm_to_mocho.py.

Usage:
    python scripts/sample_type_dispatch.py [--sample-size N]

    --sample-size N   records to sample per (mediatype, sector) cell (default: 5)

Inputs:
    data/items-all-goethe-faust.json        JSONL corpus
    output/config/lookup_dctype_to_class.csv       dispatch table (Phase B)

Outputs:
    output/dctype_dispatch_sample.csv       sampled records with dispatch results
    output/dctype_dispatch_summary.csv      per (mediatype, sector) match/fallback counts

    Printed: formatted sample table and per-cell summary

Dispatch logic (three-level fallback, per plan §3.3 D.1):
    1. Exact:        lookup (mediatype, sector, dc_type_de)
    2. Any-sector:   lookup (mediatype, 'any', dc_type_de)
    3. Any-mediatype lookup ('any', 'any', dc_type_de)
    4. Fallback:     mocho:Manifestation (D9)

Dependencies:
    stdlib only: argparse, csv, json, logging, pathlib, collections

Assumptions:
    - Concept nodes with vocnet.org/medientyp/ → mediatype; /sparte/ → sector
    - ProvidedCHO.dcType is a dict with '$' key or a list thereof
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
from collections import defaultdict
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
JSONL = ROOT / "data" / "items-all-goethe-faust.json"
LOOKUP = ROOT / "output" / "lookup_dctype_to_class.csv"
OUT_SAMPLE = ROOT / "output" / "dctype_dispatch_sample.csv"
OUT_SUMMARY = ROOT / "output" / "dctype_dispatch_summary.csv"

MEDIATYPE_PREFIX = "http://ddb.vocnet.org/medientyp/"
SECTOR_PREFIX = "http://ddb.vocnet.org/sparte/"
MOCHO_MANIFESTATION = "https://ise-fizkarlsruhe.github.io/ddbkg/mocho#Manifestation"

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


# ── Dispatch index ────────────────────────────────────────────────────────────

DispatchRow = dict[str, str]


def load_lookup(path: Path) -> dict[tuple[str, str, str], DispatchRow]:
    """
    Load lookup_dctype_to_class.csv into a three-key index.
    Key: (mediatype, sector, dc_type_de) — all three may be IRIs or 'any'.
    """
    index: dict[tuple[str, str, str], DispatchRow] = {}
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            key = (row["mediatype"], row["sector"], row["dc_type_de"])
            index[key] = row
    log.info("Lookup index: %d entries", len(index))
    return index


def dispatch(
    index: dict[tuple[str, str, str], DispatchRow],
    mediatype: str,
    sector: str,
    dc_type_de: str,
) -> tuple[DispatchRow | None, str]:
    """
    Three-level fallback lookup.
    Returns (matched_row_or_None, match_level).
    match_level: 'exact' | 'any-sector' | 'any-mediatype' | 'fallback'
    """
    # Level 1 — exact
    row = index.get((mediatype, sector, dc_type_de))
    if row:
        return row, "exact"
    # Level 2 — any sector
    row = index.get((mediatype, "any", dc_type_de))
    if row:
        return row, "any-sector"
    # Level 3 — any mediatype
    row = index.get(("any", "any", dc_type_de))
    if row:
        return row, "any-mediatype"
    return None, "fallback"


def assigned_classes(row: DispatchRow | None) -> tuple[str, str]:
    """
    Return (cho_class, note) representing what would be emitted for the ProvidedCHO.

    W-slot class → replaces mocho:Manifestation for ProvidedCHO.
    M-slot class → accumulated alongside mocho:Manifestation.
    No match      → mocho:Manifestation only (D9 fallback).
    """
    if row is None:
        return MOCHO_MANIFESTATION, ""
    w = row.get("rdf_type_w", "")
    m = row.get("rdf_type_m", "")
    if w:
        return w, f"W-slot (replaces mocho:Manifestation); M-slot={m or '—'}"
    if m and m != MOCHO_MANIFESTATION:
        return MOCHO_MANIFESTATION, f"+ {m} (M-slot accumulated)"
    if m == MOCHO_MANIFESTATION:
        return MOCHO_MANIFESTATION, "mocho:Manifestation explicit"
    return MOCHO_MANIFESTATION, "D9 fallback"


# ── Record parsing ────────────────────────────────────────────────────────────

def get_text(value: object) -> str:
    if isinstance(value, dict):
        return (value.get("$") or "").strip()
    if isinstance(value, str):
        return value.strip()
    return ""


def as_list(value: object) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def extract_mediatype_sector(concepts: list) -> tuple[str, str]:
    mediatype = "any"
    sector = "any"
    for c in concepts:
        about = c.get("about") or ""
        if about.startswith(MEDIATYPE_PREFIX):
            mediatype = about
        elif about.startswith(SECTOR_PREFIX):
            sector = about
    return mediatype, sector


def record_id(rec: dict) -> str:
    cho = rec.get("edm", {}).get("RDF", {}).get("ProvidedCHO", {})
    return cho.get("about") or rec.get("@id") or ""


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sample-size", type=int, default=5, metavar="N",
        help="records to sample per (mediatype, sector) cell (default: 5)",
    )
    args = parser.parse_args()
    sample_size: int = args.sample_size

    if not JSONL.exists():
        log.error("JSONL not found: %s", JSONL)
        raise SystemExit(1)
    if not LOOKUP.exists():
        log.error("Lookup CSV not found: %s", LOOKUP)
        raise SystemExit(1)

    index = load_lookup(LOOKUP)

    # Per (mediatype, sector): list of sampled result dicts
    samples: dict[tuple[str, str], list[dict]] = defaultdict(list)
    # Per (mediatype, sector): {total, matched, fallback}
    counts: dict[tuple[str, str], dict[str, int]] = defaultdict(
        lambda: {"total": 0, "exact": 0, "any-sector": 0, "any-mediatype": 0, "fallback": 0}
    )

    with JSONL.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue

            rdf = rec.get("edm", {}).get("RDF", {})
            concepts = as_list(rdf.get("Concept"))
            cho = rdf.get("ProvidedCHO", {})

            mediatype, sector = extract_mediatype_sector(concepts)
            cell = (mediatype, sector)

            dc_types_raw = as_list(cho.get("dcType"))
            if not dc_types_raw:
                continue

            dc_type_de = get_text(dc_types_raw[0])
            if not dc_type_de:
                continue

            matched_row, level = dispatch(index, mediatype, sector, dc_type_de)
            cho_class, note = assigned_classes(matched_row)

            counts[cell]["total"] += 1
            counts[cell][level] += 1

            if len(samples[cell]) < sample_size:
                samples[cell].append({
                    "mediatype": mediatype,
                    "sector": sector,
                    "mediatype_label": MEDIATYPE_LABEL.get(mediatype, mediatype),
                    "sector_label": SECTOR_LABEL.get(sector, sector),
                    "record_id": record_id(rec),
                    "dc_type_de": dc_type_de,
                    "match_level": level,
                    "cho_class": cho_class,
                    "note": note,
                    "source_vocab": matched_row["source_vocab"] if matched_row else "",
                })

    # ── Write sample CSV ──────────────────────────────────────────────────────

    sample_fields = [
        "mediatype_label", "sector_label", "record_id", "dc_type_de",
        "match_level", "cho_class", "source_vocab", "note",
        "mediatype", "sector",
    ]
    all_samples = [s for cell_samples in samples.values() for s in cell_samples]
    all_samples.sort(key=lambda s: (
        s["mediatype_label"], s["sector_label"], s["dc_type_de"]
    ))
    with OUT_SAMPLE.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=sample_fields)
        writer.writeheader()
        writer.writerows(all_samples)
    log.info("Sample CSV: %d rows → %s", len(all_samples), OUT_SAMPLE)

    # ── Write summary CSV ─────────────────────────────────────────────────────

    summary_fields = [
        "mediatype_label", "sector_label", "total",
        "exact", "any_sector", "any_mediatype", "fallback",
        "pct_matched", "mediatype", "sector",
    ]
    summary_rows = []
    for (mt, sec), c in sorted(counts.items()):
        matched = c["exact"] + c["any-sector"] + c["any-mediatype"]
        pct = f"{100.0 * matched / c['total']:.1f}" if c["total"] else "0.0"
        summary_rows.append({
            "mediatype_label": MEDIATYPE_LABEL.get(mt, mt),
            "sector_label": SECTOR_LABEL.get(sec, sec),
            "total": c["total"],
            "exact": c["exact"],
            "any_sector": c["any-sector"],
            "any_mediatype": c["any-mediatype"],
            "fallback": c["fallback"],
            "pct_matched": pct,
            "mediatype": mt,
            "sector": sec,
        })
    with OUT_SUMMARY.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=summary_fields)
        writer.writeheader()
        writer.writerows(summary_rows)
    log.info("Summary CSV: %d rows → %s", len(summary_rows), OUT_SUMMARY)

    # ── Print sample table ────────────────────────────────────────────────────

    print(f"\n{'='*100}")
    print(f"DC:TYPE DISPATCH SAMPLE  (sample_size={sample_size} per cell)")
    print(f"{'='*100}")
    for (mt, sec), cell_samples in sorted(
        samples.items(), key=lambda kv: (
            MEDIATYPE_LABEL.get(kv[0][0], kv[0][0]),
            SECTOR_LABEL.get(kv[0][1], kv[0][1]),
        )
    ):
        mt_label = MEDIATYPE_LABEL.get(mt, mt)
        sec_label = SECTOR_LABEL.get(sec, sec)
        c = counts[(mt, sec)]
        matched = c["exact"] + c["any-sector"] + c["any-mediatype"]
        pct = 100.0 * matched / c["total"] if c["total"] else 0.0
        print(f"\n── {mt_label} / {sec_label}  "
              f"(total={c['total']:,}  matched={matched:,}  {pct:.0f}%  "
              f"fallback={c['fallback']:,}) ──")
        for s in cell_samples:
            cls_tail = s["cho_class"].split("#")[-1].split("/")[-1]
            print(f"  [{s['match_level']:<14}] {s['dc_type_de']:<35} → {cls_tail}")

    # ── Print summary ─────────────────────────────────────────────────────────

    total_rec = sum(c["total"] for c in counts.values())
    total_matched = sum(
        c["exact"] + c["any-sector"] + c["any-mediatype"] for c in counts.values()
    )
    total_fallback = sum(c["fallback"] for c in counts.values())
    pct_overall = 100.0 * total_matched / total_rec if total_rec else 0.0

    print(f"\n{'='*100}")
    print(f"SUMMARY  total={total_rec:,}  matched={total_matched:,} ({pct_overall:.1f}%)  "
          f"fallback={total_fallback:,}")
    print(f"{'='*100}\n")


if __name__ == "__main__":
    main()
