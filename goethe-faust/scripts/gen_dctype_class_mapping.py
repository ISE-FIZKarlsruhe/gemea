#!/usr/bin/env python3
"""
gen_dctype_class_mapping.py

Purpose:
    Read the four dc:type config JSONs (audio, image, video, general) and the
    GND URI mapping CSV (Phase B0), and write output/config/lookup_dctype_to_class.csv —
    the dispatch table that maps (mediatype, sector, dc_type_de) to mocho/RDA
    rdf:type class IRIs.

Usage:
    python scripts/gen_dctype_class_mapping.py

Inputs:
    scripts/old-config/audio_type2class.json  — mt001 dc:type → MO/ACO classes
    scripts/old-config/type2class.json        — general dc:type → legacy fabio classes
    output/config/image_type2class.json       — mt002 dc:type × sector → VRA/mocho classes
    output/config/video_type2class.json       — mt005 dc:type → EBUCore Plus classes
    output/dctype_to_gnd_uri.csv              — dc_type_de → GND URI (Phase B0)

Outputs:
    output/config/lookup_dctype_to_class.csv         — dispatch table

Schema (lookup_dctype_to_class.csv):
    mediatype    — vocnet mediatype IRI or 'any'
    sector       — vocnet sector IRI or 'any'
    dc_type_de   — German dc:type literal (current lookup key)
    dc_type_en   — English translation
    dnb_uri      — GND concept URI (future lookup key — Option 1 path)
    rdf_type_w   — Work-level class IRI
    rdf_type_e   — Expression-level class IRI
    rdf_type_m   — Manifestation-level class IRI
    rdf_type_i   — Item-level class IRI
    source_vocab — mo, aco, vra, mocho, ebucoreplus, fabio (legacy)
    notes        — remarks from source config

Dependencies:
    stdlib only: json, csv, pathlib, logging

Assumptions:
    - Audio old-config class[0] == 'mo:MusicalWork' ↔ Group A (musical carrier)
    - Audio sector field is an integer (2, 4, 6) or absent; maps to vocnet sparte IRI
    - Image sector field is already a full vocnet IRI (never empty)
    - Video sectors field is a list of short codes (e.g. 'sparte005')
    - General/text entries retain prefixed class strings (legacy fabio; unresolved to IRIs)
    - 'Muster' key in audio config is the schema template; skip it
"""

import csv
import json
import logging
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
OUTPUT = ROOT / "output"

# ── Vocabulary IRIs ──────────────────────────────────────────────────────────

MEDIATYPE_IRI: dict[str, str] = {
    "mt001": "http://ddb.vocnet.org/medientyp/mt001",
    "mt002": "http://ddb.vocnet.org/medientyp/mt002",
    "mt003": "http://ddb.vocnet.org/medientyp/mt003",
    "mt005": "http://ddb.vocnet.org/medientyp/mt005",
    "mt007": "http://ddb.vocnet.org/medientyp/mt007",
}

SECTOR_INT_TO_IRI: dict[int, str] = {
    i: f"http://ddb.vocnet.org/sparte/sparte{i:03d}" for i in range(1, 8)
}

SECTOR_CODE_TO_IRI: dict[str, str] = {
    f"sparte{i:03d}": f"http://ddb.vocnet.org/sparte/sparte{i:03d}"
    for i in range(1, 8)
}

CLASS_MAP: dict[str, str] = {
    # Music Ontology — IRI base: http://purl.org/ontology/mo/
    "mo:MusicalWork":             "http://purl.org/ontology/mo/MusicalWork",
    "mo:MusicalExpression":       "http://purl.org/ontology/mo/MusicalExpression",
    "mo:MusicalManifestation":    "http://purl.org/ontology/mo/MusicalManifestation",
    "mo:MusicalItem":             "http://purl.org/ontology/mo/MusicalItem",
    # Audio Commons Ontology — IRI base: https://w3id.org/ac-ontology/aco#
    "aco:AudioManifestation":     "https://w3id.org/ac-ontology/aco#AudioManifestation",
    "aco:AudioExpression":        "https://w3id.org/ac-ontology/aco#AudioExpression",
    "aco:AudioFile":              "https://w3id.org/ac-ontology/aco#AudioFile",
    "aco:AudioItem":              "https://w3id.org/ac-ontology/aco#AudioItem",
    # VRA Core — IRI base: http://purl.org/vra/
    "vra:Work":                   "http://purl.org/vra/Work",
    # mocho — IRI base: https://ise-fizkarlsruhe.github.io/ddbkg/mocho#
    "mocho:ImageWork":            "https://ise-fizkarlsruhe.github.io/ddbkg/mocho#ImageWork",
    "mocho:ImmovableWork":        "https://ise-fizkarlsruhe.github.io/ddbkg/mocho#ImmovableWork",
    "mocho:ImageObject":          "https://ise-fizkarlsruhe.github.io/ddbkg/mocho#ImageObject",
    "mocho:Manifestation":        "https://ise-fizkarlsruhe.github.io/ddbkg/mocho#Manifestation",
    # EBUCore Plus — IRI base: http://www.ebu.ch/metadata/ontologies/ebucoreplus#
    "ebucoreplus:EditorialWork":  "http://www.ebu.ch/metadata/ontologies/ebucoreplus#EditorialWork",
    "ebucoreplus:MediaResource":  "http://www.ebu.ch/metadata/ontologies/ebucoreplus#MediaResource",
}

CSV_FIELDS = [
    "mediatype", "sector", "dc_type_de", "dc_type_en", "dnb_uri",
    "rdf_type_w", "rdf_type_e", "rdf_type_m", "rdf_type_i",
    "source_vocab", "notes",
]


def resolve(prefixed: str) -> str:
    """Resolve a prefixed class name to a full IRI; pass through if unknown."""
    if not prefixed:
        return ""
    return CLASS_MAP.get(prefixed, prefixed)


def load_gnd_uri_index() -> dict[str, str]:
    """Return dc_type_de → gnd_uri from Phase B0 output."""
    path = OUTPUT / "dctype_to_gnd_uri.csv"
    if not path.exists():
        log.warning("dctype_to_gnd_uri.csv not found — dnb_uri column will rely on old-config dnb fields")
        return {}
    index: dict[str, str] = {}
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            dc_type_de = row.get("dc_type_de", "")
            gnd_uri = row.get("gnd_uri", "")
            if dc_type_de and gnd_uri:
                index[dc_type_de] = gnd_uri
    log.info("GND URI index: %d entries", len(index))
    return index


def _dnb_uri(dc_type_de: str, entry: dict, gnd_index: dict[str, str]) -> str:
    """
    Resolve dnb_uri for an entry.
    Priority: Phase B0 GND index (corpus-derived) > old-config 'dnb' field.
    """
    uri = gnd_index.get(dc_type_de, "")
    if not uri:
        old_dnb = entry.get("dnb", "")
        if isinstance(old_dnb, str) and old_dnb.startswith("https://d-nb.info/gnd/"):
            uri = old_dnb
    return uri


def load_audio_entries(gnd_index: dict[str, str]) -> list[dict[str, str]]:
    """
    Read audio_type2class.json and apply new MO/ACO class assignments per
    audio-type-class-mapping.md:

      Group A — old class[0] == 'mo:MusicalWork' (musical carriers)
          rdf_type_w = mo:MusicalWork  (future Work entity; not emitted for ProvidedCHO)
          rdf_type_m = mo:MusicalManifestation
      Groups B/C — all other entries (non-musical / generic audio)
          rdf_type_m = aco:AudioManifestation
    """
    path = SCRIPTS / "old-config" / "audio_type2class.json"
    with path.open(encoding="utf-8") as fh:
        config: dict = json.load(fh)

    mt_iri = MEDIATYPE_IRI["mt001"]
    rows: list[dict[str, str]] = []

    for dc_type_de, entry in config.items():
        if dc_type_de == "Muster":
            continue  # schema template — not a real dc:type

        old_class: list = entry.get("class", [])
        old_w: str = old_class[0] if old_class and isinstance(old_class[0], str) else ""

        sector_raw = entry.get("sector")
        if sector_raw is None:
            sector_iri = "any"
        else:
            sector_int = int(sector_raw)
            sector_iri = SECTOR_INT_TO_IRI.get(sector_int, "any")
            if sector_iri == "any":
                log.warning("Unknown sector int %d for audio entry '%s'", sector_int, dc_type_de)

        if old_w == "mo:MusicalWork":
            # Group A — musical carrier
            rdf_type_w = resolve("mo:MusicalWork")
            rdf_type_e = ""
            rdf_type_m = resolve("mo:MusicalManifestation")
            rdf_type_i = ""
            source_vocab = "mo"
        else:
            # Groups B / C — non-musical audio
            rdf_type_w = ""
            rdf_type_e = ""
            rdf_type_m = resolve("aco:AudioManifestation")
            rdf_type_i = ""
            source_vocab = "aco"

        rows.append({
            "mediatype": mt_iri,
            "sector": sector_iri,
            "dc_type_de": dc_type_de,
            "dc_type_en": entry.get("en", ""),
            "dnb_uri": _dnb_uri(dc_type_de, entry, gnd_index),
            "rdf_type_w": rdf_type_w,
            "rdf_type_e": rdf_type_e,
            "rdf_type_m": rdf_type_m,
            "rdf_type_i": rdf_type_i,
            "source_vocab": source_vocab,
            "notes": entry.get("remarks", ""),
        })

    log.info("Audio entries: %d", len(rows))
    return rows


def load_image_entries(gnd_index: dict[str, str]) -> list[dict[str, str]]:
    """
    Read image_type2class.json. Classes are already in new VRA/mocho vocabulary;
    sector IRI is already a full vocnet IRI in the value.
    """
    path = OUTPUT / "config" / "image_type2class.json"
    with path.open(encoding="utf-8") as fh:
        config: dict = json.load(fh)

    mt_iri = MEDIATYPE_IRI["mt002"]
    rows: list[dict[str, str]] = []

    for _key, entry in config.items():
        dc_type_de: str = entry.get("dc_type_de", "")
        sector_iri: str = entry.get("sector") or "any"
        cls: list = entry.get("class", ["", "", "", ""])

        rdf_type_w = resolve(cls[0]) if len(cls) > 0 else ""
        rdf_type_e = resolve(cls[1]) if len(cls) > 1 else ""
        rdf_type_m = resolve(cls[2]) if len(cls) > 2 else ""
        rdf_type_i = resolve(cls[3]) if len(cls) > 3 else ""

        w_class: str = cls[0] if cls else ""
        if w_class.startswith("vra:"):
            source_vocab = "vra"
        elif w_class.startswith("mocho:"):
            source_vocab = "mocho"
        else:
            source_vocab = "mocho"  # M-slot entries default to mocho (mocho:Manifestation)

        rows.append({
            "mediatype": mt_iri,
            "sector": sector_iri,
            "dc_type_de": dc_type_de,
            "dc_type_en": "",
            "dnb_uri": gnd_index.get(dc_type_de, ""),
            "rdf_type_w": rdf_type_w,
            "rdf_type_e": rdf_type_e,
            "rdf_type_m": rdf_type_m,
            "rdf_type_i": rdf_type_i,
            "source_vocab": source_vocab,
            "notes": entry.get("notes", ""),
        })

    log.info("Image entries: %d", len(rows))
    return rows


def load_video_entries(gnd_index: dict[str, str]) -> list[dict[str, str]]:
    """
    Read video_type2class.json. Each entry may have multiple sectors (short codes,
    e.g. 'sparte005'). Emit one CSV row per sector.
    """
    path = OUTPUT / "config" / "video_type2class.json"
    with path.open(encoding="utf-8") as fh:
        config: dict = json.load(fh)

    mt_iri = MEDIATYPE_IRI["mt005"]
    rows: list[dict[str, str]] = []

    for dc_type_de, entry in config.items():
        cls: list = entry.get("class", ["", "", "", ""])
        rdf_type_w = resolve(cls[0]) if len(cls) > 0 else ""
        rdf_type_e = resolve(cls[1]) if len(cls) > 1 else ""
        rdf_type_m = resolve(cls[2]) if len(cls) > 2 else ""
        rdf_type_i = resolve(cls[3]) if len(cls) > 3 else ""

        sector_codes: list[str] = entry.get("sectors", [])
        sector_iris: list[str] = (
            [SECTOR_CODE_TO_IRI.get(s, s) for s in sector_codes]
            if sector_codes
            else ["any"]
        )

        for sector_iri in sector_iris:
            rows.append({
                "mediatype": mt_iri,
                "sector": sector_iri,
                "dc_type_de": dc_type_de,
                "dc_type_en": entry.get("en", ""),
                "dnb_uri": gnd_index.get(dc_type_de, ""),
                "rdf_type_w": rdf_type_w,
                "rdf_type_e": rdf_type_e,
                "rdf_type_m": rdf_type_m,
                "rdf_type_i": rdf_type_i,
                "source_vocab": "ebucoreplus",
                "notes": entry.get("remarks", ""),
            })

    log.info("Video entries: %d rows (from %d dc:types)", len(rows), len(config))
    return rows


def load_general_entries(gnd_index: dict[str, str]) -> list[dict[str, str]]:
    """
    Read type2class.json (general / text dc:types). This config predates per-mediatype
    dispatch; entries use mediatype='any'. Old fabio class names are retained as prefixed
    strings (not resolved to IRIs) since text-type class assignment is a future task.
    """
    path = SCRIPTS / "old-config" / "type2class.json"
    with path.open(encoding="utf-8") as fh:
        config: dict = json.load(fh)

    rows: list[dict[str, str]] = []

    for dc_type_de, entry in config.items():
        # rdf_type slots are intentionally empty: text-type class assignment is a
        # future task. Entries are included only to populate dnb_uri / dc_type_en
        # and to mark the dc_type_de as known (avoiding true-fallback confusion).
        rows.append({
            "mediatype": "any",
            "sector": "any",
            "dc_type_de": dc_type_de,
            "dc_type_en": entry.get("en", ""),
            "dnb_uri": _dnb_uri(dc_type_de, entry, gnd_index),
            "rdf_type_w": "",
            "rdf_type_e": "",
            "rdf_type_m": "",
            "rdf_type_i": "",
            "source_vocab": "fabio",
            "notes": entry.get("remarks", ""),
        })

    log.info("General/text entries: %d", len(rows))
    return rows


def main() -> None:
    gnd_index = load_gnd_uri_index()

    all_rows: list[dict[str, str]] = []
    all_rows.extend(load_audio_entries(gnd_index))
    all_rows.extend(load_image_entries(gnd_index))
    all_rows.extend(load_video_entries(gnd_index))
    all_rows.extend(load_general_entries(gnd_index))

    out_path = OUTPUT / "lookup_dctype_to_class.csv"
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(all_rows)

    log.info("Wrote %d rows → %s", len(all_rows), out_path)

    # Summary
    by_mt: dict[str, int] = {}
    for row in all_rows:
        mt = row["mediatype"]
        by_mt[mt] = by_mt.get(mt, 0) + 1
    for mt, count in sorted(by_mt.items()):
        log.info("  %-55s %d rows", mt, count)


if __name__ == "__main__":
    main()
