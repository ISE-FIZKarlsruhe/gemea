#!/usr/bin/env bash
# Purpose:   Transform a single sector SQLite database with N parallel workers, then merge.
#            --test mode uses a JSONL file instead of SQLite (splits file into worker chunks).
# Usage:     bash scripts/transform/run/run-transform-sector.sh [OPTIONS]
#              --sector SECTOR    SQLite stem, e.g. s2          (default: s2)
#              --sqlite-dir DIR   Directory with <sector>.sqlite (default: /data/gemea/sqlite)
#              --scripts-dir DIR  Path to gemea/scripts/ (holds the transform package; default: the scripts/ folder above this script)
#              --version VER      Output version tag              (default: YYYYMMDD today)
#              --workers N        Parallel worker count           (default: 50; 5 in --test mode)
#              --prov-db FILE     Path to prov.duckdb from prescan (optional; passed to each worker)
#              --merge            Merge stats + werk_staging after workers finish (default: off)
#              --merge-all        Merge stats + werk_staging + .nq after workers finish
#              --output-dir DIR   Override output directory (default: derived from mode)
#              --test             JSONL mode: input items-all-goethe-faust.json,
#                                 output /data/gemea/www/downloads/gemea/<version>/, 5 workers
# Inputs:    <sqlite-dir>/<sector>.sqlite  (normal mode)
#            $GOETHE_FAUST_DIR/data/items-all-goethe-faust.json  (--test mode; GOETHE_FAUST_DIR defaults to <scripts-dir>/../../goethe-faust)
# Outputs:   /data/gemea/www/downloads/gemea/<version>/nq/<sector>.nq       (--merge-all)
#            /data/gemea/www/downloads/gemea/<version>/<sector>-stats.json
#            /data/gemea/www/downloads/gemea/<version>/<sector>-werk-staging.duckdb
#            /data/gemea/www/downloads/gemea/<version>/<sector>-errors.jsonl
#            /data/gemea/www/downloads/gemea/<version>/<sector>.log
# Deps:      python3; duckdb (pip install duckdb) for .duckdb merge; split (coreutils)
# Assumes:   Config files at <scripts-dir>/../config/transform/

set -euo pipefail

# ── Defaults ──────────────────────────────────────────────────────────────────
SECTOR=s2
SQLITE_DIR=/data/gemea/sqlite
SCRIPTS_DIR="$(cd "$(dirname "$0")/../.." && pwd)"   # scripts/transform/run/ → scripts/
VERSION=$(date '+%Y%m%d')
WORKERS=50
PROV_DB=""
MERGE=true
MERGE_ALL=false
TEST=false
OUTPUT_DIR=""

# ── Arg parsing ───────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --sector)     SECTOR="$2";      shift 2 ;;
    --sqlite-dir) SQLITE_DIR="$2";  shift 2 ;;
    --scripts-dir)SCRIPTS_DIR="$2"; shift 2 ;;
    --version)    VERSION="$2";     shift 2 ;;
    --workers)    WORKERS="$2";     shift 2 ;;
    --prov-db)    PROV_DB="$2";     shift 2 ;;
    --merge)       MERGE=true;        shift 1 ;;
    --merge-all)   MERGE_ALL=true;   shift 1 ;;
    --test)        TEST=true;        shift 1 ;;
    --output-dir)  OUTPUT_DIR="$2";  shift 2 ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

# ── Derived paths ──────────────────────────────────────────────────────────────
ROOT="$(cd "$SCRIPTS_DIR/.." && pwd)"
CFG="$ROOT/config/transform"
# absolute path: the duckdb check below runs before any cd into $SCRIPTS_DIR
PYTHON=$( [[ -x "$ROOT/.venv/bin/python3" ]] && echo "$ROOT/.venv/bin/python3" || echo python3 )

if [[ "$TEST" == "true" ]]; then
  [[ "$WORKERS" -eq 50 ]] && WORKERS=5   # apply test default only if not explicitly overridden
  JSONL="${GOETHE_FAUST_DIR:-$ROOT/../goethe-faust}/data/items-all-goethe-faust.json"
  OUT="${OUTPUT_DIR:-/data/gemea/www/downloads/gemea/$VERSION}"
else
  DB="$SQLITE_DIR/${SECTOR}.sqlite"
  OUT="${OUTPUT_DIR:-/data/gemea/www/downloads/gemea/$VERSION}"
fi

# ── Preflight ─────────────────────────────────────────────────────────────────
[[ -d "$SCRIPTS_DIR" ]] || { echo "ERROR: scripts dir not found: $SCRIPTS_DIR" >&2; exit 1; }
[[ -d "$CFG" ]]         || { echo "ERROR: config dir not found: $CFG" >&2; exit 1; }
if [[ "$TEST" == "true" ]]; then
  [[ -f "$JSONL" ]] || { echo "ERROR: test JSONL not found: $JSONL" >&2; exit 1; }
else
  [[ -f "$DB" ]]    || { echo "ERROR: SQLite not found: $DB" >&2; exit 1; }
fi
if [[ -n "$PROV_DB" ]]; then
  [[ -f "$PROV_DB" ]] || { echo "ERROR: prov-db not found: $PROV_DB" >&2; exit 1; }
fi
$PYTHON -c "import duckdb" 2>/dev/null \
  || { echo "ERROR: duckdb not available in Python environment — werk_staging will not be written." >&2
       echo "       Fix: $PYTHON -m pip install duckdb" >&2
       exit 1; }
mkdir -p "$OUT" "$OUT/nq" \
  || { echo "ERROR: cannot create output directory: $OUT" >&2; exit 1; }

# ── Launch workers ─────────────────────────────────────────────────────────────
if [[ "$TEST" == "true" ]]; then
  TOTAL=$(wc -l < "$JSONL")
  CHUNK_DIR=$(mktemp -d "${TMPDIR:-/tmp}/transform-chunks.XXXXXX")   # macOS mktemp ignores TMPDIR without a template
  trap 'rm -rf "$CHUNK_DIR"' EXIT
  LINES_PER_CHUNK=$(( (TOTAL + WORKERS - 1) / WORKERS ))
  split -l "$LINES_PER_CHUNK" "$JSONL" "$CHUNK_DIR/chunk_"

  echo "[$(date '+%F %T')] TEST mode  total=$TOTAL  workers=$WORKERS"
  echo "  JSONL   = $JSONL"
  echo "  OUT     = $OUT"

  PROV_ARG=(); [[ -n "$PROV_DB" ]] && PROV_ARG=(--prov-db "$PROV_DB")
  for chunk in "$CHUNK_DIR"/chunk_*; do
    CHUNK_TOTAL=$(wc -l < "$chunk")
    (
      cd "$SCRIPTS_DIR"
      $PYTHON -m transform \
        --jsonl     "$chunk" \
        --outdir    "$OUT/nq" \
        --stats     dispatch \
        --total     "$CHUNK_TOTAL" \
        --alignment "$CFG/lookup_class_prop_alignment.csv" \
        --lido      "$CFG/lido_event_types.csv" \
        --htype     "$CFG/lookup_htype_doco_rico.csv" \
        --mediatype "$CFG/lookup_mediatype_class.csv" \
        --audio     "$CFG/audio_type2class.json" \
        ${PROV_ARG[@]+"${PROV_ARG[@]}"}
    ) &
  done
else
  TOTAL=$(sqlite3 "$DB" "SELECT COUNT(*) FROM objs")
  CHUNK=$(( (TOTAL + WORKERS - 1) / WORKERS ))

  echo "[$(date '+%F %T')] sector=$SECTOR  total=$TOTAL  workers=$WORKERS  chunk=$CHUNK"
  echo "  DB      = $DB"
  echo "  OUT     = $OUT"
  echo "  VERSION = $VERSION"
  [[ -n "$PROV_DB" ]] && echo "  PROV_DB = $PROV_DB"

  PROV_ARG=(); [[ -n "$PROV_DB" ]] && PROV_ARG=(--prov-db "$PROV_DB")
  for i in $(seq 0 $((WORKERS - 1))); do
    OFFSET=$(( i * CHUNK ))
    (
      cd "$SCRIPTS_DIR"
      $PYTHON -m transform \
        --db        "$DB" \
        --offset    "$OFFSET" \
        --limit     "$CHUNK" \
        --outdir    "$OUT/nq" \
        --stats     dispatch \
        --total     "$CHUNK" \
        --alignment "$CFG/lookup_class_prop_alignment.csv" \
        --lido      "$CFG/lido_event_types.csv" \
        --htype     "$CFG/lookup_htype_doco_rico.csv" \
        --mediatype "$CFG/lookup_mediatype_class.csv" \
        --audio     "$CFG/audio_type2class.json" \
        ${PROV_ARG[@]+"${PROV_ARG[@]}"}
    ) &
  done
fi

echo "[$(date '+%F %T')] All $WORKERS workers launched — waiting..."
wait
echo "[$(date '+%F %T')] Workers done"

# ── Merge ──────────────────────────────────────────────────────────────────────
if [[ "$MERGE_ALL" == "true" ]]; then
  echo "[$(date '+%F %T')] Merging shards (stats + werk_staging + .nq)"
  cd "$SCRIPTS_DIR"
  $PYTHON -m transform merge "$OUT/nq" --outdir "$OUT" --stem "$SECTOR"
elif [[ "$MERGE" == "true" ]]; then
  echo "[$(date '+%F %T')] Merging shards (stats + werk_staging only)"
  cd "$SCRIPTS_DIR"
  $PYTHON -m transform merge "$OUT/nq" --outdir "$OUT" --stem "$SECTOR" --skip-nq
else
  echo "[$(date '+%F %T')] Skipping merge (pass --merge or --merge-all)"
fi

echo "[$(date '+%F %T')] Done. Output: $OUT"
