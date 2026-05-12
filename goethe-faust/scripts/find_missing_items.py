#!/usr/bin/env python3
"""
find_missing_items.py
=====================
Compare IDs in data/ids-all-goethe-faust.txt against the item records already
fetched in data/items-all-goethe-faust.json, then write the missing IDs to
data/ids-missing.txt for re-fetching.

The JSONL file may be incomplete if the earlier fetch run was interrupted or
if individual API requests failed. This script identifies the gap so that
fetch-items.sh can be targeted at only the missing records.

Inputs
------
  data/ids-all-goethe-faust.txt    — one item ID per line (master ID list)
  data/items-all-goethe-faust.json — JSONL of already-fetched item records;
                                     each line is a JSON object with
                                     properties.item-id as the identifier

Output
------
  data/ids-missing.txt — IDs present in the txt list but absent from the JSONL;
                         suitable as the <ids-file> argument to fetch-items.sh

Usage
-----
    python scripts/find_missing_items.py

    # Then re-fetch the missing records:
    cd data && bash ../scripts/fetch-items.sh ids-missing.txt
"""

import json
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
DATA    = PROJECT / "data"

IDS_TXT  = DATA / "ids-all-goethe-faust.txt"
JSONL    = DATA / "items-all-goethe-faust.json"
OUT_FILE = DATA / "ids-missing.txt"

# ── Load master ID list ───────────────────────────────────────────────────────

with open(IDS_TXT) as f:
    txt_ids = [line.strip() for line in f if line.strip()]
txt_set = set(txt_ids)
print(f"IDs in {IDS_TXT.name}: {len(txt_set):,}")

# ── Load IDs already present in JSONL ────────────────────────────────────────

jsonl_ids = set()
with open(JSONL) as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
            oid = rec.get("properties", {}).get("item-id")
            if oid:
                jsonl_ids.add(oid)
        except json.JSONDecodeError:
            pass
print(f"IDs in {JSONL.name}: {len(jsonl_ids):,}")

# ── Compute and write missing IDs ─────────────────────────────────────────────

missing = [i for i in txt_ids if i not in jsonl_ids]   # preserve original order
print(f"Missing IDs : {len(missing)}")

with open(OUT_FILE, "w") as f:
    for i in missing:
        f.write(i + "\n")
print(f"Written to  : {OUT_FILE}")
print()
if missing:
    print("Re-fetch with:")
    print("    cd data && bash ../scripts/fetch-items.sh ids-missing.txt")
