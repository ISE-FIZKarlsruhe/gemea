#!/usr/bin/env python3
"""
build_dataframe.py
==================
Build a flat per-object DataFrame from items-all-goethe-faust.json (JSONL).

Each row corresponds to one object (item-id). Columns:

  object_id       — item identifier (properties.item-id)
  sector          — DDB sector label (from provider-info.domains, first vocnet URI)
  provider_name   — provider-info.provider-name
  timespan_begin  — year extracted from edm.RDF.TimeSpan.begin, falling back to
                    edm.RDF.ProvidedCHO.issued when TimeSpan is absent (int or None)
  timespan_end    — year extracted from edm.RDF.TimeSpan.end (int or None)
  dc_type         — list of dc:type text values
  dc_subject      — list of dc:subject / dcTermsSubject text values
  metadata_format — human-readable format label (from source.description.record.type)
  view_fields     — list of display field names
  digitized       — bool: view digitalisat field == "true"

Year extraction notes
---------------------
YEAR_RE handles both free-text years and YYYYMMDD compact dates (e.g. "18300213").
For timespan_begin, the priority order is:
  1. edm.RDF.TimeSpan.begin
  2. edm.RDF.TimeSpan.end   (if begin is absent)
  3. edm.RDF.ProvidedCHO.issued

Output
------
  output/items-dataframe.parquet   — primary (preserves list columns natively)
  output/items-dataframe-sample.csv — first 500 rows, lists serialised as JSON

Usage
-----
    pip install pandas pyarrow
    python scripts/build_dataframe.py
"""

import json
import re
import pandas as pd
from pathlib import Path

PROJECT  = Path(__file__).resolve().parent.parent
IN_PATH  = PROJECT / "data" / "items-all-goethe-faust.json"
OUT_PQ   = PROJECT / "output" / "items-dataframe.parquet"
OUT_CSV  = PROJECT / "output" / "items-dataframe-sample.csv"

# ── lookup tables ─────────────────────────────────────────────────────────────

SPARTE_LABELS = {
    "http://ddb.vocnet.org/sparte/sparte001": "Archive",
    "http://ddb.vocnet.org/sparte/sparte002": "Library",
    "http://ddb.vocnet.org/sparte/sparte003": "Monument conservation",
    "http://ddb.vocnet.org/sparte/sparte004": "Media library",
    "http://ddb.vocnet.org/sparte/sparte005": "Museum",
    "http://ddb.vocnet.org/sparte/sparte006": "Other",
    "http://ddb.vocnet.org/sparte/sparte007": "Research institution",
}

RECORD_TYPE_LABELS = {
    "urn:isbn:1-931666-22-9":                      "EAD",
    "http://www.lido-schema.org/":                 "LIDO",
    "http://www.loc.gov/MARC21/slim":              "MARC 21",
    "http://www.loc.gov/MARC21/slim/":             "MARC 21",
    "http://www.mets.org/":                        "METS",
    "http://www.loc.gov/METS/":                    "METS",
    "http://www.europeana.eu/schemas/edm/":        "EDM",
    "http://www.openarchives.org/OAI/2.0/oai_dc/": "OAI-DC",
    "http://www.loc.gov/ead/":                     "EAD",
    "http://www.loc.gov/mods/v3":                  "MODS",
    "http://purl.org/dc/elements/1.1/":            "Dublin Core",
    "http://www.rjm.de/denkxweb/denkxml/":         "DenkXweb",
}

# Two alternatives:
#  1. year surrounded by non-digits (ISO, free-text)
#  2. year followed by exactly 4 more digits (YYYYMMDD, e.g. "18300213")
YEAR_RE = re.compile(
    r'(?<!\d)(1[0-9]{3}|20[0-2][0-9])(?!\d)'
    r'|(?<!\d)(1[0-9]{3}|20[0-2][0-9])(?=\d{4}(?!\d))'
)

# ── field extractors ──────────────────────────────────────────────────────────

def extract_year(val):
    """Return the first 4-digit year (1000–2029) found in val, or None.
    Handles ISO, YYYYMMDD, free-text, and {"$": ...} dict values."""
    if not val:
        return None
    if isinstance(val, dict):
        val = val.get("$") or ""
    m = YEAR_RE.search(str(val))
    return int(m.group(1) or m.group(2)) if m else None


def first_text(raw):
    """Return the first non-empty string from a plain string, list of strings,
    or list of {"$": ...} dicts."""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        return raw.get("$")
    if isinstance(raw, list) and raw:
        item = raw[0]
        if isinstance(item, str):
            return item
        if isinstance(item, dict):
            return item.get("$")
    return None


def extract_text_list(raw):
    """Return a flat list of non-empty text strings from a field value."""
    if raw is None:
        return []
    if isinstance(raw, dict):
        t = raw.get("$")
        return [t] if t else []
    if isinstance(raw, list):
        return [item.get("$") for item in raw
                if isinstance(item, dict) and item.get("$")]
    return []


def get_sector(rec):
    domains = rec.get("provider-info", {}).get("domains", [])
    if isinstance(domains, list):
        for d in domains:
            if d and "vocnet" in d:
                return SPARTE_LABELS.get(d.strip(), d.strip())
    return None


def get_dc_type(pcho):
    raw = pcho.get("dcType")
    return extract_text_list(raw)


def get_dc_subject(pcho):
    subjects = []
    for key in ("dcSubject", "dcTermsSubject", "dcTermSubject"):
        subjects.extend(extract_text_list(pcho.get(key)))
    return subjects


def get_view_fields(rec):
    """Return list of display-field names."""
    names = []
    for group in rec.get("view", {}).get("item", {}).get("fields", []):
        if group.get("usage") != "display":
            continue
        fl = group.get("field", [])
        if isinstance(fl, dict):
            fl = [fl]
        for fi in fl:
            name = fi.get("name") if isinstance(fi, dict) else None
            if name:
                names.append(name)
    return names


def get_digitized(rec):
    for group in rec.get("view", {}).get("item", {}).get("fields", []):
        fl = group.get("field", [])
        if isinstance(fl, dict):
            fl = [fl]
        for fi in fl:
            if isinstance(fi, dict) and fi.get("id") == "digitalisat":
                vals = fi.get("value", [])
                if vals and isinstance(vals, list):
                    return vals[0].get("content", "").lower() == "true"
    return False


# ── scan ──────────────────────────────────────────────────────────────────────

print("Building DataFrame ...")
rows = []

with open(IN_PATH) as f:
    for i, line in enumerate(f):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue

        props   = rec.get("properties", {}) or {}
        pcho    = rec.get("edm", {}).get("RDF", {}).get("ProvidedCHO", {}) or {}
        ts      = rec.get("edm", {}).get("RDF", {}).get("TimeSpan") or {}
        src_rec = rec.get("source", {}).get("description", {}).get("record", {}) or {}

        rec_type_uri = src_rec.get("type")
        fmt = RECORD_TYPE_LABELS.get(rec_type_uri, rec_type_uri) if rec_type_uri else None

        # timespan_begin: TimeSpan.begin → TimeSpan.end → ProvidedCHO.issued
        ts_begin = extract_year(ts.get("begin") if isinstance(ts, dict) else None)
        ts_end   = extract_year(ts.get("end")   if isinstance(ts, dict) else None)
        if ts_begin is None and ts_end is None:
            ts_begin = extract_year(first_text(pcho.get("issued")))

        rows.append({
            "object_id":      props.get("item-id"),
            "sector":         get_sector(rec),
            "provider_name":  (rec.get("provider-info") or {}).get("provider-name"),
            "timespan_begin": ts_begin,
            "timespan_end":   ts_end,
            "dc_type":        get_dc_type(pcho),
            "dc_subject":     get_dc_subject(pcho),
            "metadata_format": fmt,
            "view_fields":    get_view_fields(rec),
            "digitized":      get_digitized(rec),
        })

        if (i + 1) % 20000 == 0:
            print(f"  {i+1:,} records processed ...")

df = pd.DataFrame(rows)

print(f"\nDataFrame shape  : {df.shape[0]:,} rows × {df.shape[1]} columns")
print(f"Columns          : {list(df.columns)}")
print(f"\nNull counts:")
for col in df.columns:
    n_null = df[col].isna().sum()
    if n_null:
        print(f"  {col:<20}: {n_null:,} null")

print(f"\nSector distribution:")
print(df["sector"].value_counts().to_string())

# ── save ──────────────────────────────────────────────────────────────────────

df.to_parquet(OUT_PQ, index=False)
print(f"\nSaved Parquet    : {OUT_PQ}  ({OUT_PQ.stat().st_size / 1e6:.1f} MB)")

# CSV sample: serialise list columns as JSON strings
df_csv = df.head(500).copy()
for col in ("dc_type", "dc_subject", "view_fields"):
    df_csv[col] = df_csv[col].apply(json.dumps, ensure_ascii=False)
df_csv.to_csv(OUT_CSV, index=False)
print(f"Saved CSV sample : {OUT_CSV}  (first 500 rows)")
