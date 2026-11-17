#!/usr/bin/env bash
# Purpose:   Full GeMeA corpus transform — 128 workers across 7 sectors in parallel
# Usage:     bash scripts/transform/run/run_gemea_transform.sh --new             # wipe previous output and restart
#            bash scripts/transform/run/run_gemea_transform.sh --resume          # skip sectors already done
#            bash scripts/transform/run/run_gemea_transform.sh --new --skip-prescan  # transform only (prescan already done)
#            Prepend PROV_DB_ARG=, CONCEPT_LABELS_DB_ARG=, AGENT_LABELS_DB_ARG=,
#            PARQUET_DIR_ARG=, PARQUET_MERGE_ARG= to override default paths.
#            Run from the gemea project root, inside a tmux/screen session.
# Inputs:    /data/ddb/data/s{1..7}.sqlite
# Outputs:   $OUT_BASE/s{1..7}/   (per-sector .nq, .duckdb, -stats.json, -errors.jsonl, .log)
#            $OUT_BASE/nq/ddbedm.nt, mocho.nt, prov.nt
#            $OUT_BASE/werk-staging-merged.duckdb
# Deps:      .venv/ created by scripts/transform/run/setup_venv.sh (optional), duckdb via pip3 --user,
#            scripts/transform/tools/split_nq.py (stdlib only)
# Notes:     Sectors with >1 worker: export → split JSONL → N parallel transforms.
#            Sectors with 1 worker:  export → transform (no split).
#            cd into scripts/ before python3 -m transform so duckdb (user site-packages)
#            is visible without PYTHONPATH.

set -euo pipefail

# ── Args ───────────────────────────────────────────────────────────────────────
MODE=""
SKIP_PRESCAN=false
for arg in "$@"; do
  case "$arg" in
    --new)           MODE=new ;;
    --resume)        MODE=resume ;;
    --skip-prescan)  SKIP_PRESCAN=true ;;
    *) echo "Usage: $0 [--new|--resume] [--skip-prescan]" >&2; exit 1 ;;
  esac
done
if [[ -z "$MODE" ]]; then
  echo "Error: specify --new (wipe and restart) or --resume (skip completed sectors)" >&2
  exit 1
fi
# ──────────────────────────────────────────────────────────────────────────────

# ── Paths ──────────────────────────────────────────────────────────────────────
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"   # scripts/transform/run/ → gemea root
SQLITE_DIR=/data/ddb/data
EXPORT_DIR=/data/ddb/gemea/json-export
SCRIPTS=$ROOT/scripts
OUT_BASE=/data/gemea/www/downloads/gemea/$(date '+%Y%m%d')
# ──────────────────────────────────────────────────────────────────────────────

CFG=$ROOT/config/transform
PYTHON=$( [[ -x "$ROOT/.venv/bin/python3" ]] && echo "$ROOT/.venv/bin/python3" || echo python3 )

# Workers per sector — 128 total (50% of 256 cores on the transform host)
# Distributed proportionally to record count so all sectors finish simultaneously.
declare -A WORKERS=([1]=17 [2]=86 [3]=1 [4]=6 [5]=8 [6]=9 [7]=1)

mkdir -p "$EXPORT_DIR" "$OUT_BASE"

PROV_DB=${PROV_DB_ARG:-$OUT_BASE/prov.duckdb}
CONCEPT_LABELS_DB=${CONCEPT_LABELS_DB_ARG:-$OUT_BASE/concept_labels.duckdb}
AGENT_LABELS_DB=${AGENT_LABELS_DB_ARG:-$OUT_BASE/agent_labels.duckdb}
PARQUET_DIR=${PARQUET_DIR_ARG:-$OUT_BASE/parquet}
PROV_SHARED=$OUT_BASE/prov-shared.nq

if [[ "$MODE" == "new" ]]; then
  echo "[$(date '+%F %T')] --new: wiping previous output"
  rm -rf "$OUT_BASE"/s{1..7} "$OUT_BASE/nq" \
         "$OUT_BASE/werk-staging-merged.duckdb" \
         "$OUT_BASE/prov.duckdb" \
         "$OUT_BASE/prov-shared.nq"
fi

mkdir -p "$PARQUET_DIR"

# ── Phase 0: prescan (parallel, one process per sector) ───────────────────────
if [[ "$SKIP_PRESCAN" == "true" ]]; then
  echo "[$(date '+%F %T')] Skipping prescan (--skip-prescan)"
  [[ -f "$PROV_DB" ]] || { echo "Error: --skip-prescan requires PROV_DB to exist: $PROV_DB" >&2; exit 1; }
else
echo "[$(date '+%F %T')] Starting prescan phase (7 sectors in parallel)"
for n in 1 2 3 4 5 6 7; do
  (
    echo "[$(date '+%F %T')] [s${n}] prescan starting"
    cd "$SCRIPTS"
    "$PYTHON" -m transform.prescan \
      --db                "$SQLITE_DIR/s${n}.sqlite" \
      --prov-db           "$PROV_DB" \
      --concept-labels-db "$CONCEPT_LABELS_DB" \
      --agent-labels-db   "$AGENT_LABELS_DB" \
      --lido              "$CFG/lido_event_types.csv" \
      --parquet-out       "$PARQUET_DIR/s${n}_meta.parquet"
    echo "[$(date '+%F %T')] [s${n}] prescan done"
  ) &
done
wait
echo "[$(date '+%F %T')] Prescan phase complete"

# Remove lock files left by prescan (safe to do after all processes finish)
rm -f "${PROV_DB}.lock" "${CONCEPT_LABELS_DB}.lock" "${AGENT_LABELS_DB}.lock"

# Phase 0.5: generate prov-shared.nq from prov.duckdb metadata
echo "[$(date '+%F %T')] Generating prov-shared.nq from prov.duckdb"
cd "$SCRIPTS"
"$PYTHON" -m transform.prescan regen \
  --prov-db  "$PROV_DB" \
  --prov-out "$PROV_SHARED"
echo "[$(date '+%F %T')] prov-shared.nq ready"
fi  # end SKIP_PRESCAN check

# optional: merge per-sector Parquet into one combined file
if [[ -n "${PARQUET_MERGE_ARG:-}" ]]; then
  echo "[$(date '+%F %T')] Merging per-sector Parquet → $PARQUET_MERGE_ARG"
  cd "$SCRIPTS"
  "$PYTHON" -m transform merge_parquet \
    --indir "$PARQUET_DIR" \
    --out   "$PARQUET_MERGE_ARG"
  echo "[$(date '+%F %T')] Parquet merge done"
fi
# ──────────────────────────────────────────────────────────────────────────────

echo "[$(date '+%F %T')] Starting GeMeA transform (${MODE}) — 128 workers across 7 sectors"
echo "  ROOT       = $ROOT"
echo "  SQLITE_DIR = $SQLITE_DIR"
echo "  EXPORT_DIR = $EXPORT_DIR"
echo "  OUT_BASE   = $OUT_BASE"
echo "  Workers    = s1:${WORKERS[1]} s2:${WORKERS[2]} s3:${WORKERS[3]} s4:${WORKERS[4]}" \
     "s5:${WORKERS[5]} s6:${WORKERS[6]} s7:${WORKERS[7]}"

# ── Phase 1+2: export then transform (chunked where W>1) ──────────────────────
for n in 1 2 3 4 5 6 7; do
  W=${WORKERS[$n]}
  (
    mkdir -p "$OUT_BASE/s${n}"

    # --resume: skip if any .nq already exists and is non-empty
    if [[ "$MODE" == "resume" ]]; then
      existing=$(find "$OUT_BASE/s${n}" -name "*.nq" -size +0 2>/dev/null | head -1)
      if [[ -n "$existing" ]]; then
        echo "[$(date '+%F %T')] [s${n}] skipping — output exists"
        exit 0
      fi
    fi

    # Export full sector JSONL
    echo "[$(date '+%F %T')] [s${n}] export starting"
    cd "$SCRIPTS"
    "$PYTHON" -m transform.sqlite_export \
      --db  "$SQLITE_DIR/s${n}.sqlite" \
      --out "$EXPORT_DIR/s${n}.jsonl" \
      2>> "$OUT_BASE/s${n}/export.log"
    echo "[$(date '+%F %T')] [s${n}] export done"

    if [[ $W -eq 1 ]]; then
      # Single worker — transform directly
      TOTAL=$(wc -l < "$EXPORT_DIR/s${n}.jsonl")
      echo "[$(date '+%F %T')] [s${n}] transform starting (1 worker, ${TOTAL} records)"
      cd "$SCRIPTS"
      "$PYTHON" -m transform \
        --jsonl      "$EXPORT_DIR/s${n}.jsonl" \
        --outdir     "$OUT_BASE/s${n}" \
        --stats      dispatch \
        --total      "$TOTAL" \
        --alignment  "$CFG/lookup_class_prop_alignment.csv" \
        --lido       "$CFG/lido_event_types.csv" \
        --htype      "$CFG/lookup_htype_doco_rico.csv" \
        --mediatype  "$CFG/lookup_mediatype_class.csv" \
        --audio      "$CFG/audio_type2class.json" \
        --prov-db    "$PROV_DB"
    else
      # Multiple workers — split JSONL into W chunks then transform in parallel
      CHUNK_DIR="$EXPORT_DIR/s${n}_chunks"
      mkdir -p "$CHUNK_DIR"
      echo "[$(date '+%F %T')] [s${n}] splitting into ${W} chunks"
      split -n l/$W "$EXPORT_DIR/s${n}.jsonl" "$CHUNK_DIR/chunk_"

      echo "[$(date '+%F %T')] [s${n}] launching ${W} transform workers"
      for chunk in "$CHUNK_DIR"/chunk_*; do
        stem=$(basename "$chunk")
        TOTAL=$(wc -l < "$chunk")
        mkdir -p "$OUT_BASE/s${n}/$stem"
        (
          cd "$SCRIPTS"
          "$PYTHON" -m transform \
            --jsonl      "$chunk" \
            --outdir     "$OUT_BASE/s${n}/$stem" \
            --stats      dispatch \
            --total      "$TOTAL" \
            --alignment  "$CFG/lookup_class_prop_alignment.csv" \
            --lido       "$CFG/lido_event_types.csv" \
            --htype      "$CFG/lookup_htype_doco_rico.csv" \
            --mediatype  "$CFG/lookup_mediatype_class.csv" \
            --audio      "$CFG/audio_type2class.json" \
            --prov-db    "$PROV_DB"
        ) &
      done
      wait  # wait for all chunk workers of this sector
    fi

    echo "[$(date '+%F %T')] [s${n}] done"
  ) &
done

echo "[$(date '+%F %T')] All sector workers launched — waiting for completion..."
wait
echo "[$(date '+%F %T')] All sectors complete — merging"

# ── Phase 3: merge DuckDB werk_staging ───────────────────────────────────────
OUT_BASE="$OUT_BASE" "$PYTHON" <<'PYEOF'
import glob, os, sys
try:
    import duckdb
except ImportError:
    print("duckdb not available — skipping werk_staging merge")
    sys.exit(0)
out_base = os.environ["OUT_BASE"]
shards = sorted(glob.glob(f"{out_base}/**/*-werk-staging.duckdb", recursive=True))
if not shards:
    print("No werk_staging shards found — skipping DuckDB merge")
    sys.exit(0)
out = f"{out_base}/werk-staging-merged.duckdb"
conn = duckdb.connect(out)
conn.execute(f"CREATE TABLE werk_staging AS SELECT * FROM '{shards[0]}'")
for p in shards[1:]:
    conn.execute(f"INSERT INTO werk_staging SELECT * FROM '{p}'")
rows = conn.execute("SELECT COUNT(*) FROM werk_staging").fetchone()[0]
conn.close()
print(f"werk_staging merged ({len(shards)} shards, {rows} rows) → {out}")
PYEOF

# ── Phase 4: split each .nq → per-chunk .nt, then merge by graph ─────────────
NT_DIR=$OUT_BASE/nq
mkdir -p "$NT_DIR"
echo "[$(date '+%F %T')] Splitting N-Quads into per-chunk .nt files"
while IFS= read -r nq; do
  "$PYTHON" "$SCRIPTS/transform/tools/split_nq.py" "$nq" &
done < <(find "$OUT_BASE" -name "*.nq" | sort)
# include shared PROV-O node triples from prescan
[[ -f "$PROV_SHARED" ]] && "$PYTHON" "$SCRIPTS/transform/tools/split_nq.py" "$PROV_SHARED" &
wait
echo "[$(date '+%F %T')] All splits done — merging by graph → $NT_DIR"

declare -A seen
while IFS= read -r f; do
  s="${f##*-}"; s="${s%.nt}"
  seen["$s"]=1
done < <(find "$OUT_BASE" -name "*-*.nt" ! -path "$NT_DIR/*")
for slug in "${!seen[@]}"; do
  find "$OUT_BASE" -name "*-${slug}.nt" ! -path "$NT_DIR/*" | sort | xargs cat > "$NT_DIR/${slug}.nt"
  echo "[$(date '+%F %T')] → $NT_DIR/${slug}.nt"
done
echo "[$(date '+%F %T')] Split+merge complete"

echo "[$(date '+%F %T')] Done. Output: $OUT_BASE"
