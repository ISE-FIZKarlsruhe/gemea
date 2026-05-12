#!/usr/bin/env python3
"""
Extract and pretty-print view.fields for a given item ID.

Usage:   python scripts/extract_view_fields.py <item-id>
Inputs:  data/items/<item-id>.json
Outputs: view-<item-id>.json (in project root)
"""
import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract view.fields from an item JSON and save pretty-printed."
    )
    parser.add_argument("item_id", help="Item ID (filename without .json)")
    args = parser.parse_args()

    base = Path(__file__).parent.parent
    input_path = base / "data" / "items" / f"{args.item_id}.json"
    output_path = base / f"view-{args.item_id}.json"

    with open(input_path, encoding="utf-8") as f:
        data = json.load(f)

    view = data["view"]
    fields = view.get("fields") or view["item"]["fields"]

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(fields, f, ensure_ascii=False, indent=2)

    print(f"Written {len(fields)} fields to {output_path}")


if __name__ == "__main__":
    main()
