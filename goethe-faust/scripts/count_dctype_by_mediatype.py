# Purpose:  Frequency count of dc:type values in the corpus across all mediatypes,
#           broken down by sector. Prerequisite for populating image_type2class.json
#           and video_type2class.json before running gen_dctype_class_mapping.py.
# Usage:    python count_dctype_by_mediatype.py
# Inputs:   data/items/*.json  (individual DDB item JSON files)
# Outputs:  output/dctype_frequency_all.csv
#           Columns: mediatype, sector, dc_type_de, count
# Deps:     stdlib only (json, csv, pathlib, collections)

import csv
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ITEMS_DIR = ROOT / "data" / "items"
OUTPUT = ROOT / "output" / "dctype_frequency_all.csv"


def extract_concepts(edm_rdf):
    """Return (mediatype_iri, sector_iri) from edm.RDF.Concept list."""
    mediatype = None
    sector = None
    concepts = edm_rdf.get("Concept") or []
    if isinstance(concepts, dict):
        concepts = [concepts]
    for c in concepts:
        about = c.get("about", "")
        if "medientyp" in about:
            mediatype = about
        elif "sparte" in about:
            sector = about
    return mediatype, sector


def extract_dctype(provided_cho):
    """Return list of dc:type German literal strings from ProvidedCHO.dcType."""
    dc = provided_cho.get("dcType")
    if dc is None:
        return []
    if isinstance(dc, list):
        return [v.get("$", "").strip() for v in dc if v.get("$", "").strip()]
    if isinstance(dc, dict):
        val = dc.get("$", "").strip()
        return [val] if val else []
    return []


def main():
    counts: Counter = Counter()
    total_files = 0
    skipped = 0

    for path in sorted(ITEMS_DIR.glob("*.json")):
        total_files += 1
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  SKIP {path.name}: {e}")
            skipped += 1
            continue

        edm_rdf = data.get("edm", {}).get("RDF", {})
        provided_cho = edm_rdf.get("ProvidedCHO", {})

        mediatype, sector = extract_concepts(edm_rdf)
        dc_types = extract_dctype(provided_cho)

        if not dc_types:
            counts[(mediatype or "none", sector or "none", "")] += 1
            continue

        for dc_type in dc_types:
            counts[(mediatype or "none", sector or "none", dc_type)] += 1

    # Write CSV sorted by mediatype, sector, then descending count
    rows = sorted(counts.items(), key=lambda x: (x[0][0], x[0][1], -x[1]))

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["mediatype", "sector", "dc_type_de", "count"])
        for (mediatype, sector, dc_type), count in rows:
            writer.writerow([mediatype, sector, dc_type, count])

    # Summary
    print(f"Files read:   {total_files - skipped} / {total_files}")
    print(f"Output rows:  {len(rows)}")
    print(f"Output:       {OUTPUT}")
    print()

    # Per-mediatype breakdown
    mt_totals: dict = {}
    mt_types: dict = {}
    for (mt, sec, dc), cnt in counts.items():
        mt_totals[mt] = mt_totals.get(mt, 0) + cnt
        if dc:
            mt_types.setdefault(mt, set()).add(dc)

    print(f"{'Mediatype':<55} {'records':>8}  {'unique dc:types':>16}")
    print("-" * 82)
    for mt in sorted(mt_totals):
        label = mt.rsplit("/", 1)[-1] if mt != "none" else "none"
        n_types = len(mt_types.get(mt, set()))
        print(f"  {label:<53} {mt_totals[mt]:>8}  {n_types:>16}")


if __name__ == "__main__":
    main()
