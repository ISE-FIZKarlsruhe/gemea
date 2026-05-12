#!/usr/bin/env python3
"""
Purpose:    Generate output/config/image_type2class.json from the dc:type frequency
            table for mt002 (Photo/Image). Implements the dispatch model from
            notes/image-type-class-mapping.md:
              Group A (ARTWORK_2D)                        → vra:Work        [W slot]
              Group B (OBJECTS_3D)                        → vra:Work        [W slot]
              Group C (PHOTO_TYPES)                       → mocho:ImageWork [W slot]
              Group D (ARCHITECTURE)                      → mocho:ImmovableWork [W slot]
              sparte001 + PHOTO_TYPES                     → skip (rico:Record via htype)
              Sector defaults: sparte006/sparte003 → vra:Work; sparte005 → mocho:ImageWork
              Group F (all remaining)                     → mocho:Manifestation [M slot]
            The class array uses WEMI positional slots [W, E, M, I].
            W-slot classes replace mocho:Manifestation in the transform; M-slot
            classes are emitted alongside it (or as the primary Manifestation type).
Usage:      python gen_image_type2class.py [--summary]
            --summary  print sector totals and top-30 dc:types then exit
Inputs:     output/dctype_frequency_all.csv
Outputs:    output/config/image_type2class.json
Deps:       stdlib only (csv, json, pathlib, collections, argparse)
Assumes:    CSV columns: mediatype, sector, dc_type_de, count
            Sector IRIs end with sparte<NNN>; mediatype IRIs end with mt<NNN>
"""

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

ROOT     = Path(__file__).resolve().parent.parent
FREQ_CSV = ROOT / "output" / "dctype_frequency_all.csv"
OUTPUT   = ROOT / "output" / "config" / "image_type2class.json"

SPARTE_BASE = "http://ddb.vocnet.org/sparte/"

VRA_WORK         = "vra:Work"
MOCHO_IMAGE_WORK = "mocho:ImageWork"
MOCHO_IMMOVABLE  = "mocho:ImmovableWork"
MOCHO_MANIFEST   = "mocho:Manifestation"

# ── Dispatch groups ────────────────────────────────────────────────────────────

# Group A — 2D Visual Artworks → vra:Work (sparte005 Media Library, sparte006 Museum)
ARTWORK_2D: frozenset = frozenset({
    "Zeichnung", "Album Zeichnung", "Handzeichnung", "Zeichnung/Druckgrafik",
    "Druckgraphik", "Druckgrafik", "Druckschrift",
    "Grafik", "Graphik",
    "Studie", "Skizze", "Silhouettenbild", "Zyklus",
    "Gemälde", "Malerei", "Wandbild", "Buchmalerei",
    "Plakat", "Mappenwerk", "Kostümentwurf",
    "Flugblatt", "Einzelblattsammlung", "Akzidenzdruck (Verlagsanzeige)",
    "Rollenporträt", "Porträt",
})

# Group B — 3D Objects → vra:Work (any sector)
OBJECTS_3D: frozenset = frozenset({
    "Skulptur", "Büste", "Relief",
    "Medaille", "Münze", "Geldschein", "Notgeldschein",
})

# Group C — Photographic Works → mocho:ImageWork (sparte005, sparte006)
PHOTO_TYPES: frozenset = frozenset({
    "Fotografie", "Foto",
    "Standfoto", "Kontaktbogen", "Kleinbilddia",
    "Negativ", "Negativ s/w", "Positiv",
    "Reprofotografie", "Fotoalbum",
    "Fotomechanische Reproduktion",
    "Bilddokument",
    "Ansichtskarte", "Ansichtskarte / Motivkarte",
    "Ansichtskarte / Motivkarte;Weltpostkarte",
    "Postkarte",
})

# Group C extension — sparte005 (Media Library) only; too generic in other sectors
PHOTO_TYPES_MEDIA_LIBRARY_ONLY: frozenset = frozenset({
    "Bild",   # too generic outside media library
    "Druck",  # photographic print in media library; printed artwork in museum (→ ARTWORK_2D)
})

# Group D — Built Heritage → mocho:ImmovableWork (any sector)
# "Denkmal" here only — not in OBJECTS_3D (fixed monument, not moveable sculpture)
ARCHITECTURE: frozenset = frozenset({
    "Baudenkmal", "Wohnhaus", "Museum", "Schule", "Universitätsinstitut",
    "Denkmal",
})

MUSEUM_SECTORS = frozenset({"sparte005", "sparte006"})
ARCHIVE_SECTOR = "sparte001"


# ── Dispatch ──────────────────────────────────────────────────────────────────

def classify(dc_type: str, sector: str) -> tuple[str, str, str]:
    """Return (class_curie, wemi_slot, notes) for a (dc_type, sector) pair.

    Args:
        dc_type: dc:type literal string as it appears in the corpus
        sector:  sparte suffix, e.g. 'sparte006'
    Returns:
        class_curie: prefixed class name, or '' for skip
        wemi_slot:   'W', 'M', or 'skip'
        notes:       dispatch rationale for documentation
    """
    # Group D — Architecture: any sector; check before OBJECTS_3D (Denkmal overlap)
    if dc_type in ARCHITECTURE:
        return MOCHO_IMMOVABLE, "W", f"Built heritage — any sector"

    # Group E — Archive photos: rico:Record comes from htype dispatch; no extra class here
    if sector == ARCHIVE_SECTOR and dc_type in (PHOTO_TYPES | PHOTO_TYPES_MEDIA_LIBRARY_ONLY):
        return "", "skip", "Archive photo — rico:Record via htype dispatch; no dc:type class"

    # Group A — 2D Artworks (museum / media library sectors only)
    if dc_type in ARTWORK_2D and sector in MUSEUM_SECTORS:
        return VRA_WORK, "W", f"2D artwork — {sector}"

    # Group B — 3D Objects (any sector)
    if dc_type in OBJECTS_3D:
        return VRA_WORK, "W", f"3D object — any sector"

    # Group C — Photographic Works (museum / media library sectors)
    if dc_type in PHOTO_TYPES and sector in MUSEUM_SECTORS:
        return MOCHO_IMAGE_WORK, "W", f"Photo Work — {sector}"
    if dc_type in PHOTO_TYPES_MEDIA_LIBRARY_ONLY and sector == "sparte005":
        return MOCHO_IMAGE_WORK, "W", "Photo Work (media library only) — sparte005"

    # Sector defaults for unmatched dc:types
    if sector in {"sparte006", "sparte003"}:
        return VRA_WORK, "W", f"Sector default — {sector}"
    if sector == "sparte005":
        return MOCHO_IMAGE_WORK, "W", "Sector default — sparte005"

    # Group F — Default (D9): mocho:Manifestation
    return MOCHO_MANIFEST, "M", "Default (D9)"


def _make_class_array(curie: str, wemi_slot: str) -> list:
    """Return [W, E, M, I] array with curie at wemi_slot; others empty."""
    arr: list = ["", "", "", ""]
    if wemi_slot == "W":
        arr[0] = curie
    elif wemi_slot == "M":
        arr[2] = curie
    return arr


# ── I/O helpers ───────────────────────────────────────────────────────────────

def load_mt002_rows() -> list:
    """Stream dctype_frequency_all.csv and return rows where mediatype = mt002."""
    rows = []
    with FREQ_CSV.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if "mt002" in r["mediatype"]:
                rows.append(r)
    return rows


def _sector_suffix(sector_iri: str) -> str:
    """Extract sparte<NNN> from full IRI or return as-is."""
    return sector_iri.rsplit("/", 1)[-1] if "/" in sector_iri else sector_iri


def print_summary(rows: list) -> None:
    by_sec: dict = defaultdict(int)
    totals: dict = defaultdict(int)
    for r in rows:
        sec = _sector_suffix(r["sector"])
        by_sec[sec] += int(r["count"])
        totals[r["dc_type_de"]] += int(r["count"])

    print("=== mt002 sector totals ===")
    for s, n in sorted(by_sec.items(), key=lambda x: -x[1]):
        print(f"  {s:<14} {n:>7}")

    print(f"\n=== Top 30 dc:types (all sectors), {len(totals)} unique total ===")
    for dc, n in sorted(totals.items(), key=lambda x: -x[1])[:30]:
        print(f"  {dc:<50} {n:>7}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate image_type2class.json for mt002 dc:type dispatch."
    )
    parser.add_argument("--summary", action="store_true",
                        help="Print sector totals and top-30 dc:types then exit")
    args = parser.parse_args()

    rows = load_mt002_rows()

    if args.summary:
        print_summary(rows)
        return

    # Aggregate counts by (dc_type_de, sector_suffix)
    groups: dict = defaultdict(int)
    for r in rows:
        dc  = r["dc_type_de"].strip()
        sec = _sector_suffix(r["sector"])
        groups[(dc, sec)] += int(r["count"])

    result: dict = {}
    dist:   dict = defaultdict(int)

    for (dc, sec), count in sorted(groups.items(), key=lambda x: (-x[1], x[0][0])):
        curie, wemi_slot, notes = classify(dc, sec)
        entry_key  = f"{dc}__{sec}" if sec not in ("none", "") else dc
        sector_iri = (SPARTE_BASE + sec) if sec not in ("none", "") else "any"

        result[entry_key] = {
            "dc_type_de": dc,
            "sector":     sector_iri,
            "count":      count,
            "class":      _make_class_array(curie, wemi_slot),
            "notes":      notes,
        }
        dist[f"{curie or '(skip)'} [{wemi_slot}]"] += 1

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=4), encoding="utf-8")

    print(f"Wrote {len(result)} entries → {OUTPUT}")
    print()
    print("Class distribution (W/M slot):")
    for cls, n in sorted(dist.items(), key=lambda x: -x[1]):
        print(f"  {cls:<50} {n:>5} entries")


if __name__ == "__main__":
    main()
