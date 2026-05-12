#!/usr/bin/env python3
"""
fetch-search-all.py
===================
Fetch all DDB search results for the query "goethe" via the DDB Solr API
and merge them into a single JSON file.

Paginates through results in batches of 1,000, with a 0.3 s delay between
requests to avoid rate-limiting. Saves the full merged Solr response.

Input
-----
  DDB Solr API (live, requires network access):
    https://api.deutsche-digitale-bibliothek.de/2/search/index/search/select

Output
------
  data/ddb-search-goethe-all.json  — merged Solr response with all docs

Usage
-----
    python scripts/fetch-search-all.py

Notes
-----
  TOTAL must match the actual result count for the query. Update it if the
  dataset changes. The commented-out line shows the Faust-specific count.
"""

import json
import os
import urllib.request
import time

API_KEY = os.environ.get("DDB_API_KEY")
if not API_KEY:
    raise SystemExit("DDB_API_KEY environment variable is not set")

API = "https://api.deutsche-digitale-bibliothek.de/2/search/index/search/select"
QUERY = "q=goethe"
ROWS = 1000
#TOTAL = 25282   #faust
TOTAL = 97173   #Goethe
from pathlib import Path
PROJECT = Path(__file__).resolve().parent.parent
OUTPUT = PROJECT / "data" / "ddb-search-goethe-all.json"

all_docs = []
base = None
start = 1

while start < TOTAL:
    url = f"{API}?{QUERY}&start={start}&rows={ROWS}&oauth_consumer_key={API_KEY}"
    print(f"Fetching start={start} rows={ROWS} ({len(all_docs)}/{TOTAL} docs so far)...")
    print(url)
    with urllib.request.urlopen(url) as resp:
        data = json.loads(resp.read())
    docs = data["response"]["docs"]
    if base is None:
        base = data
    all_docs.extend(docs)
    print(f"  Got {len(docs)} docs")
    if len(docs) == 0:
        break
    start += ROWS
    time.sleep(0.3)
base["response"]["docs"] = all_docs
base["response"]["start"] = 0

with open(OUTPUT, "w", encoding="utf-8") as f:
    json.dump(base, f, indent=2, ensure_ascii=False)

print(f"Done. Saved {len(all_docs)} docs to {OUTPUT}")
