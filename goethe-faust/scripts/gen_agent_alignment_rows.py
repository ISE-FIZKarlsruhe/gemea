#!/usr/bin/env python3
"""
gen_agent_alignment_rows.py
===========================
Generates edm:Agent property alignment rows for
output/config/lookup_class_prop_alignment.csv and writes a coverage
report for each Agent property observed in the sample data.

Mapping basis:
  - Source property IRIs follow the Europeana EDM v5.2.7 specification
    for edm:Agent (rdaGr2:, skos:, edm:, foaf:, dc:, dct:, owl:, rdf:).
  - Target properties are gndo: equivalents where a direct match exists
    (confirmed from gnd_20251218.ttl); passthrough otherwise.
  - Target class is mocho:Agent (Phase 0 stub). Domain-specific gndo
    subclass dispatch (gndo:DifferentiatedPerson, gndo:CorporateBody,
    gndo:ConferenceOrEvent, gndo:Family) is deferred to Phase 1b —
    see transform-future-plan.md §10.

Inputs:
  data/items-excerpt-1000.json        — sample DDB EDM records
  output/config/lookup_class_prop_alignment.csv  — appended in-place

Outputs:
  output/config/lookup_class_prop_alignment.csv  — edm:Agent rows appended
  output/agent_property_coverage.csv             — per-property coverage stats

Usage:
  python3 scripts/gen_agent_alignment_rows.py [--dry-run]

Options:
  --dry-run   Print rows without writing to the CSV.

Dependencies: stdlib only
Assumptions:
  - Agent property keys in the JSON use EDM/rdaGr2 local names.
  - gndo:placeOfBirth / gndo:placeOfDeath may arrive as IRI or literal;
    placeOfBirthAsLiteral / placeOfDeathAsLiteral variant handling is
    deferred to Phase 1b.
"""

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = ROOT / "data" / "items-excerpt-1000.json"
CSV_FILE = ROOT / "output" / "config" / "lookup_class_prop_alignment.csv"
COVERAGE_FILE = ROOT / "output" / "agent_property_coverage.csv"

# edm:Agent property alignment table.
# Columns: json_key, edm_prop, target_prop, note
#
# edm_prop  — source IRI as confirmed from alignment_ddbedm_mocho.csv:
#   DDB extends http://www.europeana.eu/schemas/edm/ with Agent-demographic
#   properties (dateOfBirth, gender, placeOfBirth, etc.) under the edm: prefix.
#   NOT rdaGr2: (http://rdvocab.info/ElementsGr2/) as the Europeana spec suggests —
#   the DDB uses edm: throughout.
#
# Source namespaces in DDB EDM Agent nodes:
#   gndo:  dateOfBirth/Death/Establishment/Termination, gender, placeOfBirth/Death,
#          professionOrOccupation, biographicalOrHistoricalInformation
#   edm:   begin, end, altLabel, hasMet, isRelatedTo, wasPresentAt
#   foaf:  name
#   skos:  prefLabel, note
#   owl:   sameAs
#   dc:    identifier, date, type
#   dct:   hasPart, isPartOf
#
# target_prop — passthrough where edm_prop is already gndo:;
#   closest standard equivalent otherwise.
AGENT_PROP_MAP = [
    # json_key, edm_prop, target_prop, note
    ("altLabel",                "edm:altLabel",                            "skos:altLabel",                            "edm:altLabel rdfs:subPropertyOf skos:altLabel"),
    ("begin",                   "edm:begin",                               "edm:begin",                                "future §10: dispatch by type → gndo:dateOfBirth or gndo:dateOfEstablishment"),
    ("biographicalInformation", "gndo:biographicalOrHistoricalInformation","gndo:biographicalOrHistoricalInformation", "passthrough; domain: all gndo entities"),
    ("date",                    "dc:date",                                 "dc:date",                                  ""),
    ("dateOfBirth",             "gndo:dateOfBirth",                        "gndo:dateOfBirth",                         "passthrough; domain: gndo:DifferentiatedPerson"),
    ("dateOfDeath",             "gndo:dateOfDeath",                        "gndo:dateOfDeath",                         "passthrough; domain: gndo:DifferentiatedPerson"),
    ("dateOfEstablishment",     "gndo:dateOfEstablishment",                "gndo:dateOfEstablishment",                 "passthrough; domain: gndo:CorporateBody, gndo:ConferenceOrEvent"),
    ("dateOfTermination",       "gndo:dateOfTermination",                  "gndo:dateOfTermination",                   "passthrough; domain: gndo:CorporateBody, gndo:ConferenceOrEvent"),
    ("end",                     "edm:end",                                 "edm:end",                                  "future §10: dispatch by type → gndo:dateOfDeath or gndo:dateOfTermination"),
    ("gender",                  "gndo:gender",                             "gndo:gender",                              "passthrough; domain: gndo:DifferentiatedPerson"),
    ("hasMet",                  "edm:hasMet",                              "edm:hasMet",                               "no gndo equivalent"),
    ("hasPart",                 "dct:hasPart",                             "dct:hasPart",                              "no gndo equivalent"),
    ("identifier",              "dc:identifier",                           "gndo:gndIdentifier",                       "GND number as literal"),
    ("isPartOf",                "dct:isPartOf",                            "dct:isPartOf",                             "no gndo equivalent"),
    ("isRelatedTo",             "edm:isRelatedTo",                         "edm:isRelatedTo",                          "no gndo equivalent"),
    ("name",                    "foaf:name",                               "foaf:name",                                "future §10: → gndo:preferredNameForThePerson by type"),
    ("note",                    "skos:note",                               "skos:note",                                ""),
    ("placeOfBirth",            "gndo:placeOfBirth",                       "gndo:placeOfBirth",                        "passthrough; domain: gndo:DifferentiatedPerson; value IRI or literal"),
    ("placeOfDeath",            "gndo:placeOfDeath",                       "gndo:placeOfDeath",                        "passthrough; domain: gndo:DifferentiatedPerson"),
    ("prefLabel",               "skos:prefLabel",                          "skos:prefLabel",                           "future §10: → gndo:preferredNameForThePerson by type"),
    ("professionOrOccupation",  "gndo:professionOrOccupation",             "gndo:professionOrOccupation",              "passthrough; domain: gndo:DifferentiatedPerson, gndo:Family"),
    ("sameAs",                  "owl:sameAs",                              "owl:sameAs",                               ""),
    ("type",                    "dc:type",                                 "dc:type",                                  "DDB org type URI; structural"),
    ("wasPresentAt",            "edm:wasPresentAt",                        "edm:wasPresentAt",                         "no gndo equivalent"),
]

EDM_CLASS = "edm:Agent"
TARGET_CLASS = "mocho:Agent"
WEMI = ""  # agents are not WEMI entities


def load_data() -> list[dict]:
    with open(DATA_FILE) as f:
        return json.load(f)


def collect_coverage(records: list[dict]) -> dict[str, dict]:
    """Count non-null occurrences and collect a sample value per Agent key."""
    stats: dict[str, dict] = {row[0]: {"count": 0, "sample": None} for row in AGENT_PROP_MAP}
    total_agents = 0
    for rec in records:
        agents = rec.get("edm", {}).get("RDF", {}).get("Agent", []) or []
        for agent in agents:
            if not isinstance(agent, dict):
                continue
            total_agents += 1
            for key in stats:
                val = agent.get(key)
                if val is not None:
                    stats[key]["count"] += 1
                    if stats[key]["sample"] is None:
                        stats[key]["sample"] = str(val)[:80]
    print(f"Scanned {len(records)} records, {total_agents} Agent nodes.")
    return stats


def build_csv_rows() -> list[dict]:
    """Build lookup_class_prop_alignment rows for edm:Agent."""
    rows = []
    for _key, edm_prop, target_prop, _note in AGENT_PROP_MAP:
        rows.append({
            "edm_class": EDM_CLASS,
            "target_class": TARGET_CLASS,
            "wemi": WEMI,
            "edm_prop": edm_prop,
            "target_prop": target_prop,
        })
    return rows


def append_to_lookup_csv(rows: list[dict]) -> None:
    with open(CSV_FILE, "a", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["edm_class", "target_class", "wemi", "edm_prop", "target_prop"],
        )
        for row in rows:
            writer.writerow(row)
    print(f"Appended {len(rows)} rows to {CSV_FILE.relative_to(ROOT)}")


def write_coverage(stats: dict[str, dict]) -> None:
    with open(COVERAGE_FILE, "w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["json_key", "edm_prop", "target_prop", "count_non_null", "sample_value"],
        )
        writer.writeheader()
        for key, edm_prop, target_prop, _note in AGENT_PROP_MAP:
            writer.writerow({
                "json_key": key,
                "edm_prop": edm_prop,
                "target_prop": target_prop,
                "count_non_null": stats[key]["count"],
                "sample_value": stats[key]["sample"] or "",
            })
    print(f"Wrote coverage report to {COVERAGE_FILE.relative_to(ROOT)}")


def check_duplicate_rows() -> bool:
    """Return True if edm:Agent rows already exist in the lookup CSV."""
    with open(CSV_FILE) as f:
        return any(row.startswith("edm:Agent,") for row in f)


def main() -> None:
    dry_run = "--dry-run" in sys.argv

    records = load_data()
    stats = collect_coverage(records)
    rows = build_csv_rows()

    if dry_run:
        print("\n--- CSV rows (dry run) ---")
        for row in rows:
            print(",".join(row[k] for k in ["edm_class", "target_class", "wemi", "edm_prop", "target_prop"]))
    else:
        if check_duplicate_rows():
            print("edm:Agent rows already present in lookup CSV — skipping append.")
        else:
            append_to_lookup_csv(rows)
        write_coverage(stats)


if __name__ == "__main__":
    main()
