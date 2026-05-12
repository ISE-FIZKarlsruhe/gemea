#!/usr/bin/env python3
"""
analyse_items.py
================
Read items-all-goethe-faust.json (JSONL) and aggregate six dimensions:

  1. metadata_format  — record type URI → human label, with counts
  2. sparte           — provider-info.domains[0] URI → counts
  3. provider         — provider-info.provider_id → name + count
  4. dc_type          — edm.RDF.ProvidedCHO.dcType text values, with counts
  5. dc_subject       — edm.RDF.ProvidedCHO.dcSubject text values, top-N
  6. view_fields      — view.item.fields (display usage) name → top-N value strings

Output saved to items-analysis.json.

Usage
-----
    python scripts/analyse_items.py
"""

import json
from collections import Counter, defaultdict
from pathlib import Path

PROJECT  = Path(__file__).resolve().parent.parent
IN_PATH  = PROJECT / "data" / "items-all-goethe-faust.json"
OUT_PATH = PROJECT / "output" / "items-analysis.json"

# ── helpers ──────────────────────────────────────────────────────────────────

RECORD_TYPE_LABELS = {
    "urn:isbn:1-931666-22-9":                           "EAD",
    "http://www.lido-schema.org/":                      "LIDO",
    "http://www.loc.gov/MARC21/slim":                   "MARC 21",
    "http://www.loc.gov/MARC21/slim/":                  "MARC 21",
    "http://www.mets.org/":                             "METS",
    "http://www.loc.gov/METS/":                         "METS",
    "http://www.europeana.eu/schemas/edm/":             "EDM",
    "http://www.openarchives.org/OAI/2.0/oai_dc/":     "OAI-DC",
    "http://www.loc.gov/ead/":                          "EAD",
    "http://www.loc.gov/mods/v3":                       "MODS",
    "http://purl.org/dc/elements/1.1/":                 "Dublin Core",
    "http://www.rjm.de/denkxweb/denkxml/":              "DenkXweb",
}

SPARTE_LABELS = {
    "http://ddb.vocnet.org/sparte/sparte001": "Archiv",
    "http://ddb.vocnet.org/sparte/sparte002": "Bibliothek",
    "http://ddb.vocnet.org/sparte/sparte003": "Denkmalfach",
    "http://ddb.vocnet.org/sparte/sparte004": "Mediathek",
    "http://ddb.vocnet.org/sparte/sparte005": "Museum",
    "http://ddb.vocnet.org/sparte/sparte006": "Sonstige",
    "http://ddb.vocnet.org/sparte/sparte007": "Wissenschaftliche Einrichtung",
}


def extract_text(field):
    """Return text content from a {resource, lang, $} dict or None."""
    if isinstance(field, dict):
        return field.get("$") or None
    return None


def get_dc_type(pcho):
    """Return list of non-empty dcType strings from ProvidedCHO."""
    raw = pcho.get("dcType")
    if raw is None:
        return []
    if isinstance(raw, dict):
        t = extract_text(raw)
        return [t] if t else []
    if isinstance(raw, list):
        return [t for item in raw for t in [extract_text(item)] if t]
    return []


def get_dc_subjects(pcho):
    """Return list of non-empty dcSubject/dcTermsSubject strings."""
    subjects = []
    for key in ("dcSubject", "dcTermsSubject", "dcTermSubject"):
        raw = pcho.get(key)
        if raw is None:
            continue
        if isinstance(raw, dict):
            t = extract_text(raw)
            if t:
                subjects.append(t)
        elif isinstance(raw, list):
            for item in raw:
                t = extract_text(item)
                if t:
                    subjects.append(t)
    return subjects


def get_display_fields(view):
    """Return list of (field_name, value_string) from display-usage field groups."""
    pairs = []
    fields_groups = view.get("item", {}).get("fields", [])
    if not isinstance(fields_groups, list):
        return pairs
    for group in fields_groups:
        if group.get("usage") != "display":
            continue
        field_list = group.get("field", [])
        if isinstance(field_list, dict):
            field_list = [field_list]
        for fi in field_list:
            name = fi.get("name")
            if not name:
                continue
            values = fi.get("value", [])
            if isinstance(values, dict):
                values = [values]
            for v in values:
                content = v.get("content") if isinstance(v, dict) else None
                if content:
                    pairs.append((name, content))
    return pairs


# ── main ─────────────────────────────────────────────────────────────────────

metadata_format_counter = Counter()   # URI → count
sparte_counter          = Counter()   # URI → count
provider_counter        = Counter()   # provider_id → count
provider_names          = {}          # provider_id → name
dc_type_counter         = Counter()   # text → count
dc_subject_counter      = Counter()   # text → count
field_name_counter      = Counter()   # name → count
field_value_counter     = defaultdict(Counter)  # name → {value → count}

total = 0

with open(IN_PATH) as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        total += 1

        # 1. Metadata format
        record_type = (
            rec.get("source", {})
               .get("description", {})
               .get("record", {})
               .get("type")
        )
        if record_type:
            metadata_format_counter[record_type] += 1

        # 2. Sparte + 3. Provider
        pi = rec.get("provider-info", {})
        domains = pi.get("domains", [])
        if isinstance(domains, list):
            for d in domains:
                if d and d.strip():
                    sparte_counter[d.strip()] += 1
        elif isinstance(domains, str) and domains.strip():
            sparte_counter[domains.strip()] += 1

        pid  = pi.get("provider-id") or pi.get("provider_id") or ""
        pname = pi.get("provider-name", "")
        if pid:
            provider_counter[pid] += 1
            if pid not in provider_names and pname:
                provider_names[pid] = pname

        # 4. dcType
        pcho = rec.get("edm", {}).get("RDF", {}).get("ProvidedCHO", {})
        for t in get_dc_type(pcho):
            dc_type_counter[t] += 1

        # 5. dcSubject
        for s in get_dc_subjects(pcho):
            dc_subject_counter[s] += 1

        # 6. View fields (display)
        for name, value in get_display_fields(rec.get("view", {})):
            field_name_counter[name] += 1
            field_value_counter[name][value] += 1


# ── build output ──────────────────────────────────────────────────────────────

def labelled_counts(counter, labels=None, top=None):
    """Return sorted list of {label, uri, count} dicts."""
    items = counter.most_common(top) if top else counter.most_common()
    result = []
    for uri, count in items:
        label = (labels or {}).get(uri, uri)
        entry = {"label": label, "count": count}
        if labels is not None:
            entry["uri"] = uri
        result.append(entry)
    return result


TOP_SUBJECTS = 100
TOP_FIELD_VALUES = 20

output = {
    "total_records": total,
    "metadata_format": labelled_counts(metadata_format_counter, RECORD_TYPE_LABELS),
    "sparte": labelled_counts(sparte_counter, SPARTE_LABELS),
    "provider": [
        {"provider_id": pid, "name": provider_names.get(pid, ""), "count": cnt}
        for pid, cnt in provider_counter.most_common()
    ],
    "dc_type": [{"value": v, "count": c} for v, c in dc_type_counter.most_common()],
    "dc_subject": [{"value": v, "count": c} for v, c in dc_subject_counter.most_common(TOP_SUBJECTS)],
    "view_fields": {
        name: {
            "total_occurrences": field_name_counter[name],
            "top_values": [
                {"value": v, "count": c}
                for v, c in field_value_counter[name].most_common(TOP_FIELD_VALUES)
            ],
        }
        for name in sorted(field_name_counter, key=lambda n: -field_name_counter[n])
    },
}

with open(OUT_PATH, "w", encoding="utf-8") as f:
    json.dump(output, f, indent=2, ensure_ascii=False)

print(f"Records processed : {total}")
print(f"Metadata formats  : {len(metadata_format_counter)}")
print(f"Sparte entries    : {len(sparte_counter)}")
print(f"Unique providers  : {len(provider_counter)}")
print(f"dcType values     : {len(dc_type_counter)}")
print(f"dcSubject values  : {len(dc_subject_counter)}")
print(f"View field names  : {len(field_name_counter)}")
print(f"\nSaved to {OUT_PATH}")
