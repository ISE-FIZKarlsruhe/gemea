#!/usr/bin/env python3
import argparse
import json
from typing import Any, List, Set, Tuple


def add_id_name(id_val: Any, name_val: Any, out: Set[Tuple[str, str]]) -> None:
    if isinstance(id_val, str) and isinstance(name_val, str):
        out.add((id_val, name_val))


def extract_from_view_fields(view_obj: Any, out: Set[Tuple[str, str]]) -> None:
    if not isinstance(view_obj, dict):
        return

    view_fields = view_obj.get("fields")
    if isinstance(view_fields, list):
        # view.fields: each entry is a direct {id, name} object
        for entry in view_fields:
            if isinstance(entry, dict):
                add_id_name(entry.get("id"), entry.get("name"), out)
        return

    item = view_obj.get("item")
    if not isinstance(item, dict):
        return
    item_fields = item.get("fields")
    if not isinstance(item_fields, list):
        return
    # view.item.fields: each entry wraps a 'field' list of {id, name} objects
    for entry in item_fields:
        if not isinstance(entry, dict) or entry.get("usage") != "display":
            continue
        field_list = entry.get("field")
        if isinstance(field_list, list):
            for field_item in field_list:
                if isinstance(field_item, dict):
                    add_id_name(field_item.get("id"), field_item.get("name"), out)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract all id/name pairs from the 'view' object into a dict and save as JSON."
    )
    parser.add_argument("input", help="Path to the input JSON file")
    parser.add_argument("output", help="Path to the output JSON file")
    args = parser.parse_args()

    id_name_pairs: Set[Tuple[str, str]] = set()

    with open(args.input, "r", encoding="utf-8") as f:
        first_char = f.read(1)
        f.seek(0)
        if first_char == "[":
            data = json.load(f)
            items = data if isinstance(data, list) else [data]
        else:
            items = []
            for line in f:
                line = line.strip()
                if line:
                    items.append(json.loads(line))

    for item in items:
        if isinstance(item, dict):
            view_obj = item.get("view")
            if view_obj is not None:
                extract_from_view_fields(view_obj, id_name_pairs)

    output_list = [
        [id_val, name_val]
        for id_val, name_val in sorted(id_name_pairs)
    ]

    lines = ",\n  ".join(
        json.dumps(pair, ensure_ascii=False) for pair in output_list
    )
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(f"[\n  {lines}\n]\n")


if __name__ == "__main__":
    main()
