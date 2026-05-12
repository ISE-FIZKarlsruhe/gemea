# Purpose: Profile all non-edm JSON key paths across DDB item records.
#          Produces a CSV of unique (sector, mediatype, chain, description) rows
#          to identify direct paths for mocho:sector and mocho:mediaType extraction.
# Usage:   python scripts/profile_json_keys.py [--input FILE] [--output FILE]
# Inputs:  data/items-excerpt-1000.json (default) or any DDB items JSON array
# Outputs: output/edm_json_key_profile.csv
# Dependencies: stdlib only
# Assumptions: Top-level JSON is an array of records. edm key excluded at top level.

import csv
import json
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DESCRIPTIONS = {
    "properties.item-id":               "DDB object ID / UUID",
    "properties.dataset-id":            "Dataset ID",
    "properties.dataset-label":         "Dataset label",
    "properties.revision-id":           "Ingest revision number",
    "properties.ingest-date":           "Ingest timestamp",
    "properties.cortex-type":           "Cortex type (e.g. Kultur)",
    "properties.mapping-version":       "Mapping version",
    "properties.automatically-translated": "Auto-translated flag",
    "provider-info.domains[0]":         "vocnet sector IRI (sparte)",
    "provider-info.provider-name":      "Institution name",
    "provider-info.provider-id":        "DDB provider ID",
    "provider-info.provider-ddb-id":    "DDB provider UUID",
    "provider-info.provider-isil":      "ISIL URI of institution",
    "provider-info.provider-uri":       "Institution website",
    "preview.media":                    "Mediatype string (image, audio, text, video…)",
    "preview.type":                     "Cortex type (display)",
    "preview.title":                    "Item display title",
    "preview.subtitle":                 "Item subtitle",
    "preview.thumbnail.href":           "Thumbnail binary UUID",
    "view.item.identifier":             "Provider-side item identifier",
    "view.item.label":                  "Item display label",
    "view.item.title":                  "Item title",
    "view.item.subtitle":               "Item subtitle",
    "view.item.media":                  "Mediatype string (display layer)",
    "view.item.origin":                 "Original item URL at provider",
    "view.item.category":               "Cortex category",
    "view.item.institution.id":         "Institution DDB UUID",
    "view.item.institution.name":       "Institution display name",
    "view.item.institution.url":        "Institution URL",
}

MAX_DEPTH = 8


def get_sector(record: dict) -> str:
    domains = record.get("provider-info", {}).get("domains", [])
    for d in domains:
        if d:
            return d
    return ""


def get_mediatype(record: dict) -> str:
    return record.get("preview", {}).get("media", "") or ""


def traverse(value, path: str, depth: int, paths: set):
    if depth > MAX_DEPTH:
        return
    if isinstance(value, dict):
        for k, v in value.items():
            child = f"{path}.{k}" if path else k
            paths.add(child)
            traverse(v, child, depth + 1, paths)
    elif isinstance(value, list):
        has_dict = any(isinstance(item, dict) for item in value)
        if has_dict:
            # abstract array of objects: path[] + recurse on union of keys
            union: dict = {}
            for item in value:
                if isinstance(item, dict):
                    for k, v in item.items():
                        if k not in union:
                            union[k] = v
            for k, v in union.items():
                child = f"{path}[].{k}"
                paths.add(child)
                traverse(v, child, depth + 1, paths)
        else:
            # primitive list: emit indexed paths
            for i, item in enumerate(value):
                child = f"{path}[{i}]"
                paths.add(child)
                traverse(item, child, depth + 1, paths)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=str(ROOT / "data" / "items-excerpt-1000.json"))
    parser.add_argument("--output", default=str(ROOT / "output" / "json_key_profile.csv"))
    args = parser.parse_args()

    with open(args.input, encoding="utf-8") as f:
        records = json.load(f)

    rows: set[tuple] = set()  # (sector, mediatype, chain)

    for record in records:
        sector = get_sector(record)
        mediatype = get_mediatype(record)
        paths: set[str] = set()
        for key, value in record.items():
            if key == "edm":
                continue
            paths.add(key)
            traverse(value, key, depth=1, paths=paths)
        for chain in paths:
            rows.add((sector, mediatype, chain))

    sorted_rows = sorted(rows, key=lambda r: (r[2], r[0], r[1]))

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["sector", "mediatype", "chain", "description"])
        for sector, mediatype, chain in sorted_rows:
            writer.writerow([sector, mediatype, chain, DESCRIPTIONS.get(chain, "")])

    print(f"Written {len(sorted_rows)} rows to {out_path}")
    unique_chains = {r[2] for r in rows}
    print(f"Unique chains: {len(unique_chains)}")


if __name__ == "__main__":
    main()
