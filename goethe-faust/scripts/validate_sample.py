#!/usr/bin/env python3
# Purpose:  Cross-check validation_sample.csv against lookup CSVs to flag
#           mismatches between §1.1 RULES-encoded classes and lookup table classes.
# Usage:    python scripts/validate_sample.py
# Inputs:   output/validation_sample.csv
#           output/config/lookup_htype_doco_rico.csv
#           output/config/lookup_mediatype_class.csv
#           output/config/lookup_dctype_to_class.csv
# Outputs:  output/validation_report.csv
#           Columns: sparte, mediatype, htype, dc_type, ddb_url,
#                    rule_w, rule_m, lookup_w, lookup_m, match, notes
# Deps:     stdlib only
# Assumptions:
#   - htype rows: compare rule w_class against lookup rdf_type+has_record_set_type
#   - mediatype rows: compare rule w_class/m_class against lookup rdf_type_w/rdf_type_m
#   - sparte005 + dc_type: check lookup_dctype_to_class first; fall back to mediatype lookup

import csv
from pathlib import Path

ROOT    = Path(__file__).resolve().parents[1]
SAMPLE  = ROOT / "output" / "validation_sample.csv"
HTYPE   = ROOT / "output" / "config" / "lookup_htype_doco_rico.csv"
MTCLS   = ROOT / "output" / "config" / "lookup_mediatype_class.csv"
DCCLS   = ROOT / "output" / "config" / "lookup_dctype_to_class.csv"
OUTPUT  = ROOT / "output" / "validation_report.csv"

SECTOR_PFX    = "http://ddb.vocnet.org/sparte/"
MEDIATYPE_PFX = "http://ddb.vocnet.org/medientyp/"

# Full IRI → prefixed notation, for normalising lookup CSV values
IRI_TO_PREFIX = {
    "http://purl.org/ontology/mo/MusicalWork":          "mo:MusicalWork",
    "http://purl.org/ontology/mo/MusicalManifestation": "mo:MusicalManifestation",
    "http://purl.org/vra/Work":                         "vra:Work",
    "http://purl.org/vra/Image":                        "vra:Image",
    "http://www.ebu.ch/metadata/ontologies/ebucoreplus#EditorialWork": "ec:EditorialWork",
    "http://www.ebu.ch/metadata/ontologies/ebucoreplus#MediaResource": "ec:MediaResource",
    "https://ise-fizkarlsruhe.github.io/ddbkg/mocho#ImageWork":        "mocho:ImageWork",
    "https://ise-fizkarlsruhe.github.io/ddbkg/mocho#ImageManifestation": "mocho:ImageManifestation",
    "https://ise-fizkarlsruhe.github.io/ddbkg/mocho#ImmovableWork":    "mocho:ImmovableWork",
    "https://ise-fizkarlsruhe.github.io/ddbkg/mocho#Manifestation":    "mocho:Manifestation",
    "https://w3id.org/ac-ontology/aco#AudioManifestation":             "aco:AudioManifestation",
}


def norm_prefix(s: str) -> str:
    return IRI_TO_PREFIX.get(s.strip(), s.strip())


def tok(s: str) -> frozenset:
    """Normalise a class string to a frozenset of prefixed tokens.
    Handles '+', ',' and whitespace separators; maps full IRIs to prefixes."""
    if not s or s.strip() in ("", "—"):
        return frozenset()
    parts = s.replace("+", ",").split(",")
    return frozenset(norm_prefix(p) for p in parts if p.strip())


def short(uri: str) -> str:
    """Strip full IRI to its local name for display (already prefixed in CSVs)."""
    return uri.strip()


def load_htype_lookup() -> dict:
    """Return htype_code → frozenset of all assigned class tokens."""
    result = {}
    with HTYPE.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            code = row["htype_code"].strip()
            # normalise "htype_NNN" → "htNNN" to match sample CSV format
            if code.startswith("htype_"):
                norm = "ht" + code[len("htype_"):]
            else:
                norm = code
            classes = tok(row.get("rdf_type", "")) | tok(row.get("has_record_set_type", ""))
            result[norm] = classes
    return result


def load_mt_lookup() -> dict:
    """Return (sparte_code, mt_code) → (w_set, m_set)."""
    result = {}
    with MTCLS.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            sparte_iri = row["sparte"].strip()
            mt_iri     = row["mediatype"].strip()
            sparte = sparte_iri.replace(SECTOR_PFX, "").strip()
            mt     = mt_iri.replace(MEDIATYPE_PFX, "").strip()
            result[(sparte, mt)] = (
                tok(row.get("rdf_type_w", "")),
                tok(row.get("rdf_type_m", "")),
            )
    return result


def load_dc_lookup() -> dict:
    """Return (mt_code, sparte_code, dc_type_de) → (w_set, m_set).
    Sector 'any' entries are stored under '*'."""
    result = {}
    with DCCLS.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            mt_iri     = row["mediatype"].strip()
            sector_raw = row.get("sector", "").strip()
            dc_type    = row.get("dc_type_de", "").strip()
            mt     = mt_iri.replace(MEDIATYPE_PFX, "").strip()
            if sector_raw.startswith(SECTOR_PFX):
                sparte = sector_raw.replace(SECTOR_PFX, "").strip()
            else:
                sparte = sector_raw or "*"
            # rdf_type_w is col 5 (index), rdf_type_m col 7 — use fieldnames
            w = tok(row.get("rdf_type_w", ""))
            m = tok(row.get("rdf_type_m", ""))
            result[(mt, sparte, dc_type)] = (w, m)
            # also index under '*' sector for any-sector rows
            if sparte != "*":
                result.setdefault((mt, "*", dc_type), (w, m))
    return result


def lookup_classes(
    sparte: str, mt: str, ht: str, dc_type: str,
    htype_lk: dict, mt_lk: dict, dc_lk: dict,
) -> tuple:
    """Return (lookup_w_set, lookup_m_set, lookup_source) for the row."""
    if ht and ht != "—":
        classes = htype_lk.get(ht, frozenset())
        return (classes, frozenset(), f"htype:{ht}")

    if sparte == "sparte005" and dc_type and dc_type != "—":
        key = (mt, sparte, dc_type)
        if key in dc_lk:
            w, m = dc_lk[key]
            return (w, m, f"dc_lookup:{sparte}×{mt}×{dc_type}")
        key_any = (mt, "*", dc_type)
        if key_any in dc_lk:
            w, m = dc_lk[key_any]
            return (w, m, f"dc_lookup:any×{mt}×{dc_type}")

    if (sparte, mt) in mt_lk:
        w, m = mt_lk[(sparte, mt)]
        return (w, m, f"mt_lookup:{sparte}×{mt}")

    return (frozenset(), frozenset(), "NO LOOKUP ENTRY")


def set_str(s: frozenset) -> str:
    return " + ".join(sorted(s)) if s else "—"


def match_status(rule_w, rule_m, lookup_w, lookup_m,
                 sparte: str, mt: str, dc_type: str, mt_lk: dict) -> str:
    combined_rule   = rule_w | rule_m
    combined_lookup = lookup_w | lookup_m

    if not combined_lookup:
        return "MATCH_EMPTY" if not combined_rule else "NO_LOOKUP"
    if combined_rule == combined_lookup:
        return "MATCH"

    # Check if the mismatch is because a dc:type lookup overrides the base mediatype dispatch
    if dc_type and sparte == "sparte005":
        base_w, base_m = mt_lk.get((sparte, mt), (frozenset(), frozenset()))
        if combined_rule == (base_w | base_m) and combined_lookup != combined_rule:
            return "DC_TYPE_OVERRIDE"

    missing  = combined_lookup - combined_rule
    extra    = combined_rule - combined_lookup
    parts = []
    if missing:
        parts.append("MISSING:" + ",".join(sorted(missing)))
    if extra:
        parts.append("EXTRA:" + ",".join(sorted(extra)))
    return " | ".join(parts) if parts else "MATCH"


def main() -> None:
    htype_lk = load_htype_lookup()
    mt_lk    = load_mt_lookup()
    dc_lk    = load_dc_lookup()

    out_rows  = []
    counts    = {"MATCH": 0, "MATCH_EMPTY": 0, "MISMATCH": 0, "NO_LOOKUP": 0, "NO_EXAMPLE": 0, "DC_TYPE_OVERRIDE": 0}

    with SAMPLE.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            sparte_raw = row["sparte"].split()[0]   # "sparte001 Archive" → "sparte001"
            mt_raw     = row["mediatype"].split()[0] if row["mediatype"] != "*" else "*"
            ht         = row["htype"] if row["htype"] != "—" else ""
            dc_type    = row["dc_type"] if row["dc_type"] != "—" else ""
            ddb_url    = row["ddb_url"]

            rule_w = tok(row["w_class"])
            rule_m = tok(row["m_class"])

            if ddb_url == "NO EXAMPLE FOUND":
                status = "NO_EXAMPLE"
                lk_w = lk_m = frozenset()
                source = "—"
            else:
                lk_w, lk_m, source = lookup_classes(
                    sparte_raw, mt_raw, ht, dc_type, htype_lk, mt_lk, dc_lk
                )
                status = match_status(rule_w, rule_m, lk_w, lk_m,
                                     sparte_raw, mt_raw, dc_type, mt_lk)
                if status == "NO_LOOKUP":
                    counts["NO_LOOKUP"] += 1
                elif status == "MATCH":
                    counts["MATCH"] += 1
                elif status == "DC_TYPE_OVERRIDE":
                    counts["DC_TYPE_OVERRIDE"] += 1
                elif status == "MATCH_EMPTY":
                    counts["MATCH_EMPTY"] += 1
                else:
                    counts["MISMATCH"] += 1

            if status == "NO_EXAMPLE":
                counts["NO_EXAMPLE"] += 1

            out_rows.append({
                "sparte":     row["sparte"],
                "mediatype":  row["mediatype"],
                "htype":      row["htype"],
                "dc_type":    row["dc_type"],
                "ddb_url":    ddb_url,
                "rule_w":     set_str(rule_w),
                "rule_m":     set_str(rule_m),
                "lookup_w":   set_str(lk_w),
                "lookup_m":   set_str(lk_m),
                "match":      status,
                "source":     source,
            })

    fields = ["sparte", "mediatype", "htype", "dc_type", "ddb_url",
              "rule_w", "rule_m", "lookup_w", "lookup_m", "match", "source"]
    with OUTPUT.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(out_rows)

    print(f"Wrote {len(out_rows)} rows → {OUTPUT}")
    print(f"  MATCH            : {counts['MATCH']}")
    print(f"  MATCH_EMPTY      : {counts['MATCH_EMPTY']}  (both rule and lookup have no classes — correct)")
    print(f"  DC_TYPE_OVERRIDE : {counts['DC_TYPE_OVERRIDE']}  (dc:type lookup overrides base mediatype dispatch — expected)")
    print(f"  MISMATCH         : {counts['MISMATCH']}")
    print(f"  NO_LOOKUP        : {counts['NO_LOOKUP']}")
    print(f"  NO_EXAMPLE       : {counts['NO_EXAMPLE']}")

    mismatches = [r for r in out_rows if r["match"] not in ("MATCH", "MATCH_EMPTY", "NO_LOOKUP", "NO_EXAMPLE", "DC_TYPE_OVERRIDE")]
    if mismatches:
        print(f"\nMismatches ({len(mismatches)}):")
        for r in mismatches:
            print(f"  {r['sparte']} | {r['mediatype']} | ht={r['htype']} | dc={r['dc_type']}")
            print(f"    rule  : W={r['rule_w']}  M={r['rule_m']}")
            print(f"    lookup: W={r['lookup_w']}  M={r['lookup_m']}")
            print(f"    {r['match']}")


if __name__ == "__main__":
    main()
