#!/usr/bin/env python3
"""
profile_edm_fields.py
=====================
Profile all field keys present under edm.RDF.* entity types in the JSONL
data file. Reports per-entity-type field names with record counts.

Purpose : Determine which EDM/DC properties are actually used in the data,
          as input to the DDB-EDM → mocho ontology alignment.
Usage   : python scripts/profile_edm_fields.py
Inputs  : data/items-all-goethe-faust.json  (JSONL)
Outputs : output/edm_field_profile.json
          output/edm_field_profile.csv
          stdout summary
Dependencies : none (stdlib only)
Assumptions  : JSONL where each line is a DDB item with an edm.RDF object.
"""

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

PROJECT  = Path(__file__).resolve().parent.parent
IN_PATH  = PROJECT / "data" / "items-all-goethe-faust.json"
OUT_JSON = PROJECT / "output" / "edm_field_profile.json"
OUT_CSV  = PROJECT / "output" / "edm_field_profile.csv"

# ── scan ──────────────────────────────────────────────────────────────────────

field_counts = defaultdict(Counter)   # entity_type -> {key: record_count}
entity_record_counts = Counter()       # entity_type -> records that have it
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

        rdf = rec.get("edm", {}).get("RDF", {})
        for entity_type, entity in rdf.items():
            items = entity if isinstance(entity, list) else [entity]
            has_entity = False
            for item in items:
                if not isinstance(item, dict):
                    continue
                has_entity = True
                for key in item:
                    field_counts[entity_type][key] += 1
            if has_entity:
                entity_record_counts[entity_type] += 1

print(f"Total records : {total:,}")

# ── build output ──────────────────────────────────────────────────────────────

rows = []
for entity_type in sorted(field_counts):
    n_records = entity_record_counts[entity_type]
    for key, count in field_counts[entity_type].most_common():
        rows.append({
            "entity_type":  entity_type,
            "json_key":     key,
            "record_count": count,
            "record_pct":   round(100 * count / total, 2),
            "entity_pct":   round(100 * count / n_records, 2) if n_records else 0,
        })

OUT_JSON.parent.mkdir(exist_ok=True)

# JSON output
output = {
    "total_records": total,
    "entity_counts": dict(entity_record_counts.most_common()),
    "fields": rows,
}
with open(OUT_JSON, "w", encoding="utf-8") as f:
    json.dump(output, f, indent=2, ensure_ascii=False)

# CSV output
with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(
        f, fieldnames=["entity_type", "json_key", "record_count", "record_pct", "entity_pct"]
    )
    writer.writeheader()
    writer.writerows(rows)

# ── print summary ─────────────────────────────────────────────────────────────

for entity_type in sorted(field_counts):
    n_records = entity_record_counts[entity_type]
    print(f"\n{entity_type}  ({n_records:,} records with this entity):")
    for key, count in field_counts[entity_type].most_common():
        pct = 100 * count / total
        print(f"  {key:<40}  {count:>8,}  ({pct:.1f}%)")

print(f"\nSaved {OUT_JSON}")
print(f"Saved {OUT_CSV}")
