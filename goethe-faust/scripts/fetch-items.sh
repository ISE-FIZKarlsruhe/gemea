#!/bin/bash
# Fetch DDB items by ID and save responses to items/<uuid>.json
# Usage: ./fetch-items.sh <ids-file> [limit]

IDS_FILE="${1:-ids-all-goethe-faust.txt}"
LIMIT="${2:-0}"  # 0 = no limit
API_BASE="https://api.deutsche-digitale-bibliothek.de/2/items"
OUTPUT_DIR="items"
JSONL_FILE="items-all-goethe-faust.json"

if [[ -z "$DDB_API_KEY" ]]; then
    echo "Error: DDB_API_KEY environment variable is not set" >&2
    exit 1
fi

mkdir -p "$OUTPUT_DIR"
trap 'rm -f "$JSONL_IDS_TMP"' EXIT

# Pre-load IDs already present in JSONL file into a sorted temp file for fast lookup
JSONL_IDS_TMP=$(mktemp)
if [[ -f "$JSONL_FILE" ]]; then
    echo "Pre-loading existing JSONL IDs from $JSONL_FILE ..."
    python3 - "$JSONL_FILE" <<'EOF' | sort > "$JSONL_IDS_TMP"
import json, sys
with open(sys.argv[1]) as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
            uid = d.get('properties', {}).get('item-id', '')
            if uid:
                print(uid)
        except Exception:
            pass
EOF
    echo "  Loaded $(wc -l < "$JSONL_IDS_TMP") IDs from JSONL."
fi

in_jsonl() {
    grep -Fxq "$1" "$JSONL_IDS_TMP"
}

count=0
skipped=0
fetched=0
failed=0
while IFS= read -r uuid; do
    [[ -z "$uuid" ]] && continue
    count=$((count + 1))
    if [[ "$LIMIT" -gt 0 && "$count" -gt "$LIMIT" ]]; then
        break
    fi

    output_file="$OUTPUT_DIR/$uuid.json"
    if [[ -f "$output_file" ]]; then
        # Already fetched individually; ensure it's also in the JSONL file
        if ! in_jsonl "$uuid"; then
            python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(json.dumps(d,ensure_ascii=False))" "$output_file" >> "$JSONL_FILE"
            echo "$uuid" >> "$JSONL_IDS_TMP"
        fi
        skipped=$((skipped + 1))
        echo "[$count] SKIP $uuid (already exists)"
        continue
    fi

    http_code=$(curl -s -w "%{http_code}" -o "$output_file.tmp" "$API_BASE/$uuid?oauth_consumer_key=$DDB_API_KEY")

    if [[ "$http_code" == "200" ]]; then
        python3 -m json.tool "$output_file.tmp" > "$output_file"
        # Append compact single-line JSON to JSONL file
        python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(json.dumps(d,ensure_ascii=False))" "$output_file" >> "$JSONL_FILE"
        rm -f "$output_file.tmp"
        fetched=$((fetched + 1))
        echo "[$count] OK   $uuid"
    else
        echo "[$count] FAIL $uuid (HTTP $http_code)"
        rm -f "$output_file.tmp"
        failed=$((failed + 1))
    fi

    sleep 0.2
done < "$IDS_FILE"

echo "Done. Total: $count | Fetched: $fetched | Skipped: $skipped | Failed: $failed"
