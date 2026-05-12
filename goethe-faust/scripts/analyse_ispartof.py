#!/usr/bin/env python3
"""
Purpose: Analyse dcterms:isPartOf on ProvidedCHO.
         Classify resource URIs as: full DDB item URL, bare 32-char UUID,
         other URI, or label-only. Report counts and examples.
Usage:   python3 scripts/analyse_ispartof.py [path/to/items.json]
Inputs:  DDB items JSONL (default: data/items-all-goethe-faust.json)
Outputs: stdout summary + data/analysis/ispartof_coverage.csv
Dependencies: standard library only
"""

import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path

PROJECT  = Path(__file__).resolve().parent.parent
IN_PATH  = Path(sys.argv[1]) if len(sys.argv) > 1 else PROJECT / "data" / "items-all-goethe-faust.json"
OUT_PATH = PROJECT / "data" / "analysis" / "ispartof_coverage.csv"
OUT_PATH.parent.mkdir(parents=True, exist_ok=True)

DDB_ITEM_BASE = "http://www.deutsche-digitale-bibliothek.de/item/"
RE_UUID       = re.compile(r'^[A-Z0-9]{32}$')


def coerce_list(v) -> list:
    return [] if v is None else (v if isinstance(v, list) else [v])


def classify_resource(r: str) -> str:
    if r.startswith(DDB_ITEM_BASE):
        tail = r[len(DDB_ITEM_BASE):]
        if RE_UUID.match(tail):
            return 'ddb_full_url'
        return 'ddb_url_other'
    if RE_UUID.match(r):
        return 'bare_uuid'
    if r:
        return 'other_uri'
    return ''


with IN_PATH.open() as fh:
    first = fh.read(1)
with IN_PATH.open() as fh:
    records = json.load(fh) if first == '[' else [json.loads(l) for l in fh if l.strip()]

total_records  = len(records)
records_with   = 0
total_values   = 0
type_ctr       = Counter()
other_uri_ctr  = Counter()
rows           = []

for r in records:
    cho  = r.get('edm', {}).get('RDF', {}).get('ProvidedCHO', {})
    vals = coerce_list(cho.get('isPartOf'))
    has_val = False
    for v in vals:
        if not isinstance(v, dict):
            continue
        resource = (v.get('resource') or '').strip()
        label    = (v.get('$') or '').strip()
        if not resource and not label:
            continue
        total_values += 1
        has_val = True
        kind = classify_resource(resource) if resource else 'label_only'
        type_ctr[kind] += 1
        if kind == 'other_uri':
            other_uri_ctr[resource] += 1
        rows.append({
            'resource': resource,
            'label':    label,
            'kind':     kind,
        })
    if has_val:
        records_with += 1

with OUT_PATH.open('w', newline='', encoding='utf-8') as f:
    writer = csv.DictWriter(f, fieldnames=['resource', 'label', 'kind'])
    writer.writeheader()
    writer.writerows(rows)

def pct(n, d): return f"{100*n/d:.1f}%" if d else "n/a"

print(f"Total records:                   {total_records:>8,}")
print(f"Records with isPartOf:           {records_with:>8,}  ({pct(records_with, total_records)})")
print(f"Total isPartOf values:           {total_values:>8,}")
print()
print("Resource classification:")
for kind in ('ddb_full_url', 'bare_uuid', 'ddb_url_other', 'other_uri', 'label_only'):
    n = type_ctr.get(kind, 0)
    print(f"  {kind:<20} {n:>8,}  ({pct(n, total_values)})")
print()
if other_uri_ctr:
    print(f"Top other URIs ({len(other_uri_ctr)} distinct):")
    for uri, n in other_uri_ctr.most_common(10):
        print(f"  {n:>5,}  {uri}")
print(f"\nOutput: {OUT_PATH}")
