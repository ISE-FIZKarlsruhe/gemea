#!/usr/bin/env bash
# Purpose:   Dryrun — first LIMIT records per sector across all 7 sectors in parallel
# Usage:     bash scripts/transform/run/run_gemea_dryrun.sh --new      # wipe previous output and restart
#            bash scripts/transform/run/run_gemea_dryrun.sh --resume   # skip sectors already done
#            Run from the gemea project root, inside a tmux/screen session.
# Inputs:    /data/ddb/data/s{1..7}.sqlite
# Outputs:   $OUT_BASE/s{1..7}/   (per-sector .nq, .duckdb, -stats.json, -errors.jsonl, .log)
#            $OUT_BASE/werk-staging-merged.duckdb
#            $OUT_BASE/nt/<graph>.nt
# Deps:      .venv/ created by scripts/setup_venv.sh (optional), duckdb via pip3 --user,
#            scripts/transform/tools/split_nq.py (stdlib only)
# Notes:     Each sector exports exactly LIMIT records from SQLite then transforms.
#            dryrun-s{N}.jsonl files are written to EXPORT_DIR (separate from production).
#            Runs from SCRIPTS dir (cd) so transform package is found without PYTHONPATH.

set -euo pipefail

# ── Args ───────────────────────────────────────────────────────────────────────
MODE=""
for arg in "$@"; do
  case "$arg" in
    --new)    MODE=new ;;
    --resume) MODE=resume ;;
    *) echo "Usage: $0 [--new|--resume]" >&2; exit 1 ;;
  esac
done
if [[ -z "$MODE" ]]; then
  echo "Error: specify --new (wipe and restart) or --resume (skip completed sectors)" >&2
  exit 1
fi
# ──────────────────────────────────────────────────────────────────────────────

# ── Paths (set before running) ─────────────────────────────────────────────────
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"   # scripts/transform/run/ → gemea root
SQLITE_DIR=/data/ddb/data
EXPORT_DIR=/data/ddb/gemea/dryrun/json-export
OUT_BASE=/data/ddb/gemea/dryrun
# ──────────────────────────────────────────────────────────────────────────────

CFG=$ROOT/config/transform
SCRIPTS=$ROOT/scripts
PYTHON=$( [[ -x "$ROOT/.venv/bin/python3" ]] && echo "$ROOT/.venv/bin/python3" || echo python3 )
LIMIT=10000

mkdir -p "$EXPORT_DIR" "$OUT_BASE"

if [[ "$MODE" == "new" ]]; then
  echo "[$(date '+%F %T')] --new: wiping previous output"
  rm -rf "$OUT_BASE"/s{1..7} "$OUT_BASE/nt" "$OUT_BASE/werk-staging-merged.duckdb"
fi

echo "[$(date '+%F %T')] Starting GeMeA dryrun (${MODE}) — 7 sectors, ${LIMIT} records each"
echo "  ROOT       = $ROOT"
echo "  SQLITE_DIR = $SQLITE_DIR"
echo "  EXPORT_DIR = $EXPORT_DIR"
echo "  OUT_BASE   = $OUT_BASE"

# ── Phase 1+2: export (limited) then transform, pipelined per sector ──────────
for n in 1 2 3 4 5 6 7; do
  (
    mkdir -p "$OUT_BASE/s${n}"

    # --resume: skip if .nq already exists and is non-empty
    if [[ "$MODE" == "resume" ]]; then
      existing=$(find "$OUT_BASE/s${n}" -name "*.nq" -size +0 2>/dev/null | head -1)
      if [[ -n "$existing" ]]; then
        echo "[$(date '+%F %T')] [s${n}] skipping — output exists"
        exit 0
      fi
    fi

    echo "[$(date '+%F %T')] [s${n}] export starting (limit ${LIMIT})"
    cd "$SCRIPTS"
    "$PYTHON" -m transform.sqlite_export \
      --db    "$SQLITE_DIR/s${n}.sqlite" \
      --out   "$EXPORT_DIR/dryrun-s${n}.jsonl" \
      --limit "$LIMIT" \
      2>> "$OUT_BASE/s${n}/export.log"

    echo "[$(date '+%F %T')] [s${n}] export done — transform starting"
    "$PYTHON" -m transform \
      --jsonl        "$EXPORT_DIR/dryrun-s${n}.jsonl" \
      --outdir       "$OUT_BASE/s${n}" \
      --stats        dispatch \
      --log-interval 1000 \
      --total        "$LIMIT" \
      --alignment    "$CFG/lookup_class_prop_alignment.csv" \
      --lido         "$CFG/lido_event_types.csv" \
      --htype        "$CFG/lookup_htype_doco_rico.csv" \
      --mediatype    "$CFG/lookup_mediatype_class.csv" \
      --audio        "$CFG/audio_type2class.json"

    echo "[$(date '+%F %T')] [s${n}] transform done"
  ) &
done

echo "[$(date '+%F %T')] All 7 sector workers launched — waiting for completion..."
wait
echo "[$(date '+%F %T')] All sectors complete"

# ── Phase 3: merge DuckDB werk_staging ───────────────────────────────────────
OUT_BASE="$OUT_BASE" "$PYTHON" <<'PYEOF'
import glob, os, sys
try:
    import duckdb
except ImportError:
    print("duckdb not available — skipping werk_staging merge")
    sys.exit(0)
out_base = os.environ["OUT_BASE"]
shards = sorted(glob.glob(f"{out_base}/s*/*-werk-staging.duckdb"))
if not shards:
    print("No werk_staging shards found — skipping DuckDB merge")
    sys.exit(0)
out = f"{out_base}/werk-staging-merged.duckdb"
conn = duckdb.connect(out)
conn.execute(f"CREATE OR REPLACE TABLE werk_staging AS SELECT * FROM '{shards[0]}'")
for p in shards[1:]:
    conn.execute(f"INSERT INTO werk_staging SELECT * FROM '{p}'")
rows = conn.execute("SELECT COUNT(*) FROM werk_staging").fetchone()[0]
conn.close()
print(f"werk_staging merged ({len(shards)} shards, {rows} rows) → {out}")
PYEOF

# ── Phase 4: split each sector .nq → per-sector .nt, then merge by graph ─────
NT_DIR=$OUT_BASE/nt
mkdir -p "$NT_DIR"
echo "[$(date '+%F %T')] Splitting per-sector N-Quads into .nt files"
for n in 1 2 3 4 5 6 7; do
  for nq in "$OUT_BASE/s${n}"/*.nq; do
    [[ -f "$nq" ]] || continue
    "$PYTHON" "$SCRIPTS/transform/tools/split_nq.py" "$nq"
    echo "[$(date '+%F %T')] [s${n}] split $(basename "$nq")"
  done
done

echo "[$(date '+%F %T')] Merging per-graph .nt files → $NT_DIR"
declare -A seen
for f in "$OUT_BASE"/s*/*-*.nt; do
  [[ -f "$f" ]] || continue
  s="${f##*-}"; s="${s%.nt}"
  seen["$s"]=1
done
for slug in "${!seen[@]}"; do
  cat "$OUT_BASE"/s*/*-"${slug}.nt" > "$NT_DIR/${slug}.nt"
  echo "[$(date '+%F %T')] → $NT_DIR/${slug}.nt"
done
echo "[$(date '+%F %T')] Split+merge complete"

echo "[$(date '+%F %T')] Done. All output in: $OUT_BASE"
