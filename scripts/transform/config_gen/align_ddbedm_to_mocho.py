#!/usr/bin/env python3
"""
align_ddbedm_to_mocho.py
========================
Data-driven ontology alignment: maps DDB-EDM fields present in
items-all-goethe-faust.json to their corresponding mocho/RDA properties.

Pipeline:
  1. Profile data  — load edm_field_profile.json (from profile_edm_fields.py)
  2. Resolve IRIs  — map JSON camelCase/plain keys to full EDM/DC IRIs
                     by parsing ddbedm_1.0.ttl with rdflib
  3. Load DC→RDA   — read mapping_dct_to_rda.csv for DC/DCT→RDA sub-property
                     pairs produced by the mocho dct_rda_map.py workflow
  4. Load mocho    — parse mocho-full.owl to collect all RDA properties
                     imported into mocho (IRIs + English labels + WEMI level)
  5. Align         — for each data field: DC/EDM IRI → RDA candidates →
                     restrict to mocho-known properties
                     Key explicit dimensions: ProvidedCHO.dcType → rdae:P20001
  5b. Align vocab  — controlled-vocabulary Concepts:
                     Concept.about from ddb.vocnet.org/sparte/ → sector
                     Concept.about from ddb.vocnet.org/medientyp/ → rdae:P20001
  6. Write outputs

Inputs:
  $GOETHE_FAUST_DIR/output/edm_field_profile.json   — field profile (run profile_edm_fields.py first)
  $MOCHO_DIR/ontology/ddbedm_1.0.ttl                 — DDB-EDM ontology
  $MOCHO_DIR/mocho-odk/src/ontology/mocho-full.owl   — mocho
  $MOCHO_DIR/output/mapping_dct_to_rda.csv           — DC→RDA mapping
  (GOETHE_FAUST_DIR and MOCHO_DIR default to the sibling checkouts ../goethe-faust and ../mocho)

Outputs:
  $GOETHE_FAUST_DIR/output/alignment_ddbedm_mocho.csv   — one row per (edm_field × rda_property)
  $GOETHE_FAUST_DIR/output/alignment_ddbedm_mocho.json  — summary + full alignment

Columns (CSV):
  entity_type, json_key, edm_prefix, edm_iri, record_count, coverage_pct,
  rda_iri, rda_label, wemi_level, match_method, in_mocho, vocab_label
  (vocab_label is non-empty only for concept_vocab_sector / concept_vocab_mediatype rows)

Usage:
  python3 scripts/transform/config_gen/profile_edm_fields.py   # run first if profile is stale
  python3 scripts/transform/config_gen/align_ddbedm_to_mocho.py [--input JSON] [--mocho-dir DIR] [--out-csv CSV] [--out-json JSON]

Dependencies: rdflib
Assumptions:
  - ddbedm_1.0.ttl declares all properties used in the DDB-EDM profile
  - mapping_dct_to_rda.csv schema: dct_term, rda_iri, rda_label
  - mocho-full.owl is RDF/XML and includes all imported RDA properties inline
"""

import argparse
import csv
import json
import os
from collections import defaultdict
from pathlib import Path

from rdflib import Graph, RDF, RDFS, OWL, Namespace
from rdflib.namespace import DC, DCTERMS, SKOS

GEMEA_ROOT = Path(__file__).resolve().parents[3]   # scripts/transform/config_gen/ → gemea root
GOETHE_FAUST_DIR = Path(os.environ.get("GOETHE_FAUST_DIR", GEMEA_ROOT.parent / "goethe-faust"))
MOCHO    = Path(os.environ.get("MOCHO_DIR", GEMEA_ROOT.parent / "mocho"))
PROFILE_PATH  = GOETHE_FAUST_DIR / "output" / "edm_field_profile.json"
OUT_CSV       = GOETHE_FAUST_DIR / "output" / "alignment_ddbedm_mocho.csv"
OUT_JSON      = GOETHE_FAUST_DIR / "output" / "alignment_ddbedm_mocho.json"

_parser = argparse.ArgumentParser(description="Align DDB-EDM fields to mocho/RDA properties.")
_parser.add_argument("--input", type=Path, default=PROFILE_PATH, help=f"field profile JSON (default: {PROFILE_PATH})")
_parser.add_argument("--mocho-dir", type=Path, default=MOCHO, help=f"mocho checkout (default: {MOCHO})")
_parser.add_argument("--out-csv", type=Path, default=OUT_CSV, help=f"alignment CSV (default: {OUT_CSV})")
_parser.add_argument("--out-json", type=Path, default=OUT_JSON, help=f"alignment JSON (default: {OUT_JSON})")
_args = _parser.parse_args()
PROFILE_PATH, MOCHO, OUT_CSV, OUT_JSON = _args.input, _args.mocho_dir, _args.out_csv, _args.out_json

DDBEDM_TTL    = MOCHO / "ontology" / "ddbedm_1.0.ttl"
MOCHO_OWL     = MOCHO / "mocho-odk" / "src" / "ontology" / "mocho-full.owl"
DCT_RDA_CSV   = MOCHO / "output" / "mapping_dct_to_rda.csv"

# ── namespace constants ────────────────────────────────────────────────────────

EDM  = Namespace("http://www.europeana.eu/schemas/edm/")
ORE  = Namespace("http://www.openarchives.org/ore/terms/")
GEO  = Namespace("http://www.w3.org/2003/01/geo/wgs84_pos#")
FOAF = Namespace("http://xmlns.com/foaf/0.1/")
RDA_ELEMENTS = "http://rdaregistry.info/Elements/"

WEMI_LEVELS = {
    f"{RDA_ELEMENTS}w/": "Work",
    f"{RDA_ELEMENTS}e/": "Expression",
    f"{RDA_ELEMENTS}m/": "Manifestation",
    f"{RDA_ELEMENTS}i/": "Item",
    f"{RDA_ELEMENTS}a/": "Agent",
    f"{RDA_ELEMENTS}c/": "Corporate Body",
    f"{RDA_ELEMENTS}p/": "Person",
}

# ── controlled vocabulary constants ───────────────────────────────────────────
# DDB sector (Sparte) vocabulary — carried in edm:Concept.about
SPARTE_VOCAB = {
    "http://ddb.vocnet.org/sparte/sparte001": "Archive",
    "http://ddb.vocnet.org/sparte/sparte002": "Library",
    "http://ddb.vocnet.org/sparte/sparte003": "Monument Preservation",
    "http://ddb.vocnet.org/sparte/sparte004": "Research",
    "http://ddb.vocnet.org/sparte/sparte005": "Media Library",
    "http://ddb.vocnet.org/sparte/sparte006": "Museum",
    "http://ddb.vocnet.org/sparte/sparte007": "Others",
}

# DDB media type (Medientyp) vocabulary — carried in edm:Concept.about
MEDIENTYP_VOCAB = {
    "http://ddb.vocnet.org/medientyp/mt001": "Audio",
    "http://ddb.vocnet.org/medientyp/mt002": "Photo",
    "http://ddb.vocnet.org/medientyp/mt003": "Text",
    "http://ddb.vocnet.org/medientyp/mt005": "Video",
    "http://ddb.vocnet.org/medientyp/mt007": "Not Digitized",
}

# Media type aligns to rdae:P20001 (has content type) — same target as dc:type
MEDIENTYP_RDA_IRI = "http://rdaregistry.info/Elements/e/P20001"

# ── step 1: load field profile ─────────────────────────────────────────────────

print("Loading field profile …")
with open(PROFILE_PATH) as f:
    profile = json.load(f)

total_records = profile["total_records"]
# {(entity_type, json_key): {record_count, record_pct}}
data_fields = {
    (row["entity_type"], row["json_key"]): row
    for row in profile["fields"]
}
print(f"  {len(data_fields)} (entity_type, json_key) pairs from {total_records:,} records")

# ── step 2: resolve JSON keys to EDM/DC IRIs ──────────────────────────────────

print("\nParsing DDB-EDM ontology …")
g_edm = Graph()
g_edm.parse(DDBEDM_TTL)

# Build lookup: normalised_local_name → [(prefix_label, full_iri)]
# where normalised = lowercase of local name
# Hard-coded overrides for known DDB data quirks: json_key → (prefix, iri)
# dcTermSubject (no 's') is a DDB data variant for dcterms:subject
# identifier (no prefix) is dc:identifier in ProvidedCHO context
# occuredAt (one 'r') is a DDB API typo for edm:occurredAt
OVERRIDES = {
    "dcTermSubject": ("dcterms", str(DCTERMS) + "subject"),
    "identifier":    ("dc",      str(DC) + "identifier"),
    "occuredAt":     ("edm",     "http://www.europeana.eu/schemas/edm/occurredAt"),
}

prop_lookup = defaultdict(list)  # normalised_local_name → [(prefix_label, full_iri)]
for prop_type in (OWL.ObjectProperty, OWL.DatatypeProperty, OWL.AnnotationProperty):
    for subj in g_edm.subjects(RDF.type, prop_type):
        iri = str(subj)
        local = iri.rsplit("/", 1)[-1].rsplit("#", 1)[-1]
        try:
            prefix = g_edm.qname(subj).split(":")[0]
        except Exception:
            prefix = ""
        prop_lookup[local.lower()].append((prefix, iri))

# Also include classes for context
for subj in g_edm.subjects(RDF.type, OWL.Class):
    iri = str(subj)
    local = iri.rsplit("/", 1)[-1].rsplit("#", 1)[-1]
    prop_lookup[local.lower()].append(("class", iri))

print(f"  {len(prop_lookup)} unique local names from DDB-EDM")


def resolve_iri(entity_type: str, json_key: str):
    """Map a JSON camelCase/plain key to (prefix_label, full_iri).

    Checks hard-coded overrides first, then the ontology lookup, then
    heuristic prefix-stripping.

    Resolution order:
    1. Direct match of json_key (lowercase) against the ontology lookup.
    2. Strip known camelCase prefixes (dcTerms, dc, edm, ore, foaf, geo, skos)
       and match the lowercased remainder.
    3. Entity-type-specific defaults for keys without an obvious namespace.
    Returns (prefix_label, iri) or None if unresolved.
    """
    # Hard-coded overrides
    if json_key in OVERRIDES:
        return OVERRIDES[json_key]

    key_lower = json_key.lower()

    # Direct match
    if key_lower in prop_lookup:
        return prop_lookup[key_lower][0]

    # Strip camelCase namespace prefixes in order (longest first to avoid greedy mismatch)
    prefixes = [
        ("dcterms", "dcterms", str(DCTERMS)),
        ("dcterms", "dcTerms", str(DCTERMS)),
        ("dc",      "dc",      str(DC)),
        ("edm",     "edm",     str(EDM)),
        ("ore",     "ore",     str(ORE)),
        ("skos",    "skos",    str(SKOS)),
        ("geo",     "geo",     str(GEO)),
        ("foaf",    "foaf",    str(FOAF)),
    ]
    for label, strip_prefix, ns in prefixes:
        if json_key.startswith(strip_prefix) and len(json_key) > len(strip_prefix):
            local = json_key[len(strip_prefix):]
            local_lower = local[0].lower() + local[1:]
            iri = ns + local_lower
            # Verify it exists in the ontology or return it as a best-effort match
            if local_lower in prop_lookup:
                return prop_lookup[local_lower][0]
            # Best-effort: construct IRI even if not explicitly in ontology
            return (label, iri)

    # Entity-type-specific defaults for unnamespaced keys
    entity_defaults = {
        # TimeSpan: begin/end are edm: properties
        "TimeSpan": str(EDM),
        # Place: lat/long/alt are geo:
        "Place":    str(GEO),
        # Agent: EDM agent properties
        "Agent":    str(EDM),
    }
    ns = entity_defaults.get(entity_type)
    if ns:
        iri = ns + json_key
        return (entity_type.lower(), iri)

    return None


# Resolve all data fields
field_iris = {}  # (entity_type, json_key) -> (prefix_label, iri) or None
for (entity_type, json_key) in data_fields:
    field_iris[(entity_type, json_key)] = resolve_iri(entity_type, json_key)

resolved   = sum(1 for v in field_iris.values() if v is not None)
unresolved = sum(1 for v in field_iris.values() if v is None)
print(f"  Resolved: {resolved}  Unresolved: {unresolved}")

# ── step 3: load DC→RDA mapping ───────────────────────────────────────────────

print("\nLoading DC→RDA mapping …")
# dct_term → [(rda_iri, rda_label)]
dct_to_rda: dict[str, list[tuple[str, str]]] = defaultdict(list)
with open(DCT_RDA_CSV, newline="", encoding="utf-8") as f:
    for row in csv.DictReader(f):
        dct_to_rda[row["dct_term"]].append((row["rda_iri"], row["rda_label"]))

print(f"  {len(dct_to_rda)} DC/DCT terms → {sum(len(v) for v in dct_to_rda.values())} RDA pairs")

# ── step 4: load mocho RDA properties ────────────────────────────────────────

print("\nLoading mocho-full.owl …")
g_mocho = Graph()
g_mocho.parse(MOCHO_OWL)

mocho_rda: dict[str, str] = {}  # iri → English label
for prop_type in (OWL.ObjectProperty, OWL.DatatypeProperty, OWL.AnnotationProperty):
    for subj in g_mocho.subjects(RDF.type, prop_type):
        iri = str(subj)
        if RDA_ELEMENTS not in iri:
            continue
        # Prefer English label
        label = ""
        for lbl in g_mocho.objects(subj, RDFS.label):
            if getattr(lbl, "language", None) == "en":
                label = str(lbl)
                break
        if not label:
            for lbl in g_mocho.objects(subj, RDFS.label):
                label = str(lbl)
                break
        mocho_rda[iri] = label

print(f"  {len(mocho_rda)} RDA properties in mocho")


def wemi_level(iri: str) -> str:
    for prefix, level in WEMI_LEVELS.items():
        if iri.startswith(prefix):
            return level
    return ""


# ── step 5: align ──────────────────────────────────────────────────────────────

print("\nAligning …")

# Build reverse map: full IRI → prefixed dct_term label
# e.g. "http://purl.org/dc/elements/1.1/creator" → ["dc:creator", "dcterms:creator"]
iri_to_dct: dict[str, list[str]] = defaultdict(list)
for term in dct_to_rda:
    prefix, local = term.split(":")
    if prefix == "dc":
        ns = str(DC)
    elif prefix == "dcterms":
        ns = str(DCTERMS)
    else:
        continue
    full_iri = ns + local
    iri_to_dct[full_iri].append(term)

rows = []
summary_matched = 0
summary_unmatched = 0
unmatched_keys = []

for (entity_type, json_key), field_row in sorted(data_fields.items()):
    record_count = field_row["record_count"]
    coverage_pct = round(100 * record_count / total_records, 2)

    resolved_pair = field_iris.get((entity_type, json_key))
    if resolved_pair is None:
        edm_prefix = ""
        edm_iri = ""
    else:
        edm_prefix, edm_iri = resolved_pair

    # Skip structural/internal keys
    if json_key in ("about",):
        continue

    # Find RDA candidates via DCT→RDA map
    rda_candidates = []
    match_method = "unmatched"

    if edm_iri:
        # Check direct match in DCT map
        dct_terms = iri_to_dct.get(edm_iri, [])
        for dct_term in dct_terms:
            for rda_iri, rda_label in dct_to_rda.get(dct_term, []):
                if rda_iri in mocho_rda:
                    rda_candidates.append((rda_iri, mocho_rda[rda_iri], "via_dct_map"))
                else:
                    rda_candidates.append((rda_iri, rda_label, "via_dct_map_not_in_mocho"))

        # If no DCT match, check if the EDM IRI is directly in mocho
        if not rda_candidates and edm_iri in mocho_rda:
            rda_candidates.append((edm_iri, mocho_rda[edm_iri], "direct"))

    if rda_candidates:
        # Separate into mocho-present and mocho-absent
        in_mocho   = [(i, l, m) for i, l, m in rda_candidates if "not_in_mocho" not in m]
        not_mocho  = [(i, l, m) for i, l, m in rda_candidates if "not_in_mocho" in m]

        for rda_iri, rda_label, method in in_mocho:
            rows.append({
                "entity_type":   entity_type,
                "json_key":      json_key,
                "edm_prefix":    edm_prefix,
                "edm_iri":       edm_iri,
                "record_count":  record_count,
                "coverage_pct":  coverage_pct,
                "rda_iri":       rda_iri,
                "rda_label":     rda_label,
                "wemi_level":    wemi_level(rda_iri),
                "match_method":  method,
                "in_mocho":      True,
            })
        summary_matched += 1

        # Also record any RDA candidates not yet in mocho (useful diagnostic)
        for rda_iri, rda_label, method in not_mocho:
            rows.append({
                "entity_type":   entity_type,
                "json_key":      json_key,
                "edm_prefix":    edm_prefix,
                "edm_iri":       edm_iri,
                "record_count":  record_count,
                "coverage_pct":  coverage_pct,
                "rda_iri":       rda_iri,
                "rda_label":     rda_label,
                "wemi_level":    wemi_level(rda_iri),
                "match_method":  "via_dct_map_not_in_mocho",
                "in_mocho":      False,
            })
    else:
        rows.append({
            "entity_type":   entity_type,
            "json_key":      json_key,
            "edm_prefix":    edm_prefix,
            "edm_iri":       edm_iri,
            "record_count":  record_count,
            "coverage_pct":  coverage_pct,
            "rda_iri":       "",
            "rda_label":     "",
            "wemi_level":    "",
            "match_method":  "unmatched",
            "in_mocho":      False,
        })
        summary_unmatched += 1
        unmatched_keys.append(f"{entity_type}.{json_key} [{edm_iri}]")

# ── step 5b: align controlled vocabulary Concepts ─────────────────────────────
# Sector and media type are encoded as edm:Concept entities identified by
# their about IRI.  The main loop skips Concept.about; we handle it here.

print("\nAligning controlled vocabulary Concepts …")

# Resolve media type RDA label from mocho (falls back to hard-coded)
mt_rda_label = mocho_rda.get(MEDIENTYP_RDA_IRI, "has content type")
mt_rda_in_mocho = MEDIENTYP_RDA_IRI in mocho_rda

vocab_rows: list[dict] = []

# Sector: DDB institutional sector classification.
# No direct RDA bibliographic property; recorded as concept_vocab_sector
# so downstream can use it for faceting / provenance filtering.
for iri, label in SPARTE_VOCAB.items():
    vocab_rows.append({
        "entity_type":  "Concept",
        "json_key":     "about",
        "edm_prefix":   "ddb-sparte",
        "edm_iri":      iri,
        "record_count": "",
        "coverage_pct": "",
        "rda_iri":      "",
        "rda_label":    "",
        "wemi_level":   "",
        "match_method": "concept_vocab_sector",
        "in_mocho":     False,
        "vocab_label":  label,
    })

# Media type: DDB media type classification → rdae:P20001 (has content type)
# Parallel to ProvidedCHO.dcType — both classify the intellectual content kind.
for iri, label in MEDIENTYP_VOCAB.items():
    vocab_rows.append({
        "entity_type":  "Concept",
        "json_key":     "about",
        "edm_prefix":   "ddb-medientyp",
        "edm_iri":      iri,
        "record_count": "",
        "coverage_pct": "",
        "rda_iri":      MEDIENTYP_RDA_IRI if mt_rda_in_mocho else "",
        "rda_label":    mt_rda_label if mt_rda_in_mocho else "",
        "wemi_level":   wemi_level(MEDIENTYP_RDA_IRI),
        "match_method": "concept_vocab_mediatype",
        "in_mocho":     mt_rda_in_mocho,
        "vocab_label":  label,
    })

rows.extend(vocab_rows)
print(f"  {len(SPARTE_VOCAB)} sector entries, {len(MEDIENTYP_VOCAB)} media type entries")

# Backfill vocab_label on rows produced by the main alignment loop
for row in rows:
    row.setdefault("vocab_label", "")

# ── step 6: write outputs ──────────────────────────────────────────────────────

OUT_CSV.parent.mkdir(exist_ok=True)

fieldnames = [
    "entity_type", "json_key", "edm_prefix", "edm_iri",
    "record_count", "coverage_pct",
    "rda_iri", "rda_label", "wemi_level", "match_method", "in_mocho",
    "vocab_label",
]

with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)

# Group for JSON: per (entity_type, json_key) → list of RDA mappings.
# Vocab rows (concept_vocab_*) are routed to vocab_concept_alignments instead.
groups: dict[str, dict] = {}
vocab_groups: dict[str, list] = {"sector": [], "media_type": []}

for row in rows:
    if row["match_method"].startswith("concept_vocab_"):
        domain = "sector" if row["match_method"] == "concept_vocab_sector" else "media_type"
        vocab_groups[domain].append({
            "iri":        row["edm_iri"],
            "label":      row["vocab_label"],
            "rda_iri":    row["rda_iri"],
            "rda_label":  row["rda_label"],
            "wemi_level": row["wemi_level"],
            "in_mocho":   row["in_mocho"],
        })
        continue

    key = f"{row['entity_type']}.{row['json_key']}"
    if key not in groups:
        groups[key] = {
            "entity_type":  row["entity_type"],
            "json_key":     row["json_key"],
            "edm_prefix":   row["edm_prefix"],
            "edm_iri":      row["edm_iri"],
            "record_count": row["record_count"],
            "coverage_pct": row["coverage_pct"],
            "rda_mappings": [],
        }
    if row["rda_iri"]:
        groups[key]["rda_mappings"].append({
            "rda_iri":      row["rda_iri"],
            "rda_label":    row["rda_label"],
            "wemi_level":   row["wemi_level"],
            "match_method": row["match_method"],
            "in_mocho":     row["in_mocho"],
        })

output = {
    "total_records": total_records,
    "summary": {
        "data_fields_with_rda_mapping": summary_matched,
        "data_fields_unmatched":        summary_unmatched,
        "total_alignment_rows":         len(rows),
        "unmatched_fields":             sorted(unmatched_keys),
    },
    "alignment": list(groups.values()),
    "vocab_concept_alignments": vocab_groups,
}

with open(OUT_JSON, "w", encoding="utf-8") as f:
    json.dump(output, f, indent=2, ensure_ascii=False)

# ── print summary ─────────────────────────────────────────────────────────────

print(f"\n{'='*60}")
print(f"Data fields with RDA mapping : {summary_matched}")
print(f"Data fields unmatched        : {summary_unmatched}")
print(f"Sector vocab entries         : {len(vocab_groups['sector'])}")
print(f"Media type vocab entries     : {len(vocab_groups['media_type'])}")
print(f"Total alignment rows         : {len(rows)}")

print("\nUnmatched fields:")
for key in sorted(unmatched_keys):
    print(f"  {key}")

print(f"\nSaved {OUT_CSV}")
print(f"Saved {OUT_JSON}")
