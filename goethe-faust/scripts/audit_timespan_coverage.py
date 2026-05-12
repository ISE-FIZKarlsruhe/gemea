#!/usr/bin/env python3
"""
audit_timespan_coverage.py
==========================
Audit temporal coverage of items-all-goethe-faust.json.

For every record that lacks a usable year in edm.RDF.TimeSpan.begin/.end,
this script inspects edm.RDF.ProvidedCHO for alternative date fields
(dc:date, dc:created, dc:issued and their dcterms equivalents) to determine
how many additional records could be recovered if those fields were used as
fallbacks in build_dataframe.py.

Additionally reports all ProvidedCHO keys present in TimeSpan-missing records,
so that any further date-bearing fields can be identified.

Inputs
------
  data/items-all-goethe-faust.json  — JSONL dataset

Outputs
-------
  Printed summary to stdout (no files written)

Usage
-----
    python scripts/audit_timespan_coverage.py
"""

import json
import re
from collections import Counter
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
IN_PATH = PROJECT / "data" / "items-all-goethe-faust.json"

# Matches a 4-digit year at the start of or preceded by a non-digit,
# e.g. handles both "1830" and "18300213" (YYYYMMDD).
YEAR_RE = re.compile(r'(?<!\d)(1[0-9]{3}|20[0-2][0-9])(?!\d)|^(1[0-9]{3}|20[0-2][0-9])')


def extract_year(val):
    """Return the first 4-digit year (1000–2029) found in val, or None.
    Handles ISO dates, YYYYMMDD, free-text, and dict {"$": ...} values."""
    if not val:
        return None
    if isinstance(val, dict):
        val = val.get("$") or ""
    m = YEAR_RE.search(str(val))
    if m:
        return int(m.group(1) or m.group(2))
    return None


def first_text(raw):
    """Return the first non-empty text value from a ProvidedCHO field.
    Handles plain strings, lists of strings, and lists of {"$": ...} dicts."""
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


# ── Scan ──────────────────────────────────────────────────────────────────────

print(f"Scanning {IN_PATH.name} ...")

total       = 0
ts_has      = 0          # records with a usable TimeSpan year
pcho_keys   = Counter()  # key frequencies in TimeSpan-missing ProvidedCHOs
fallback_hits = Counter() # how many missing records each fallback field covers

# Candidate fallback field names to probe (as they appear in the raw JSON)
FALLBACK_FIELDS = [
    "date",      # dc:date — confirmed present
    "issued",    # dc:issued — confirmed present
    "dcDate",
    "dctermsCreated",
    "dcCreated",
    "dctermsIssued",
    "dcIssued",
    "dctermsDate",
]

sample_pcho = []   # up to 5 ProvidedCHO dicts from missing records, for inspection

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

        pcho = rec.get("edm", {}).get("RDF", {}).get("ProvidedCHO", {}) or {}
        ts   = rec.get("edm", {}).get("RDF", {}).get("TimeSpan")

        has_ts_year = isinstance(ts, dict) and (
            extract_year(ts.get("begin")) or extract_year(ts.get("end"))
        )

        if has_ts_year:
            ts_has += 1
        else:
            # Tally all keys present in this ProvidedCHO
            for k in pcho:
                pcho_keys[k] += 1

            # Check each candidate fallback field
            for field in FALLBACK_FIELDS:
                val = first_text(pcho.get(field))
                if val and extract_year(val):
                    fallback_hits[field] += 1

            if len(sample_pcho) < 5:
                sample_pcho.append(pcho)

# ── Report ────────────────────────────────────────────────────────────────────

missing = total - ts_has

print(f"\nTotal records        : {total:,}")
print(f"Has TimeSpan year    : {ts_has:,}  ({100 * ts_has / total:.1f}%)")
print(f"Missing TimeSpan     : {missing:,}  ({100 * missing / total:.1f}%)")

print(f"\n── Fallback field coverage (of {missing:,} missing records) ──")
if fallback_hits:
    for field, n in sorted(fallback_hits.items(), key=lambda x: -x[1]):
        print(f"  {field:<25} {n:,}  ({100 * n / missing:.1f}% of missing)")
else:
    print("  None of the candidate fallback fields yield a usable year.")

print(f"\n── All ProvidedCHO keys in TimeSpan-missing records ──")
for k, n in pcho_keys.most_common():
    print(f"  {k:<40} {n:,}")

print(f"\n── Sample ProvidedCHO dicts (up to 5, date-like keys only) ──")
date_keywords = ("date", "creat", "issu", "time", "year", "dat")
for i, pcho in enumerate(sample_pcho):
    date_fields = {k: v for k, v in pcho.items()
                   if any(kw in k.lower() for kw in date_keywords)}
    print(f"  record {i + 1}: {date_fields if date_fields else '(none)'}")
