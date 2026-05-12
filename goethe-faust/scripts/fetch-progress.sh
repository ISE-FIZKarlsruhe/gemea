#!/bin/bash
# Check fetch progress for fetch-items.sh

IDS_FILE="${1:-ids-all-goethe-faust.txt}"
OUTPUT_DIR="items"
JSONL_FILE="items-all-goethe-faust.json"

total=$(wc -l < "$IDS_FILE")
fetched=$(ls "$OUTPUT_DIR" | wc -l)
in_jsonl=$(wc -l < "$JSONL_FILE")
remaining=$((total - fetched))

echo "IDs file:  $IDS_FILE"
echo "Total:     $total"
echo "Fetched:   $fetched"
echo "In JSONL:  $in_jsonl"
echo "Remaining: $remaining"
