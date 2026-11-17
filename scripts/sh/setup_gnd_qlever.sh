#!/usr/bin/env bash
# Purpose:      Convert GND JSON-LD authority files to N-Triples, build a QLever
#               index, and start a local QLever SPARQL server
# Usage:        ./setup_gnd_qlever.sh [--load <types>] [--start-only] [--rebuild]
# Inputs:       data/gnd/authorities-gnd-*.jsonld.gz
# Outputs:      qlever-gnd-index/nt/*.nt (intermediate),
#               qlever-gnd-index/gnd.* (binary index),
#               running QLever server at http://localhost:7001
# Dependencies: docker, python3, rdflib (pip install rdflib)
# Assumptions:  GND .jsonld.gz files are in data/gnd/ relative to project root;
#               port 7001 is available; Docker daemon is running

set -euo pipefail

# ── Configuration ─────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
DATA_DIR="$PROJECT_ROOT/data/gnd"

# Everything under one directory so a single Docker volume mount suffices.
# NT files and Qleverfile live alongside the binary index files.
INDEX_DIR="$PROJECT_ROOT/qlever-gnd-index"
NT_DIR="$INDEX_DIR/nt"
INDEX_NAME="gnd"

CONTAINER_NAME="gemea-qlever-gnd"
QLEVER_IMAGE="adfreiburg/qlever"
QLEVER_PORT="7001"
REBUILD=false

# Valid entity type names (space-separated)
ALL_ENTITY_TYPES="werk person geografikum koerperschaft kongress sachbegriff entityfacts"

# ── Entity type → source file ──────────────────────────────────────────────────
# Person file includes a date suffix; update if DNB releases a newer dump.
gnd_file_for() {
    local entity="$1"
    case "$entity" in
        werk)          echo "$DATA_DIR/authorities-gnd-werk_lds.jsonld.gz" ;;
        person)        echo "$DATA_DIR/authorities-gnd-person_lds_20260217.jsonld.gz" ;;
        geografikum)   echo "$DATA_DIR/authorities-gnd-geografikum_lds.jsonld.gz" ;;
        koerperschaft) echo "$DATA_DIR/authorities-gnd-koerperschaft_lds.jsonld.gz" ;;
        kongress)      echo "$DATA_DIR/authorities-gnd-kongress_lds.jsonld.gz" ;;
        sachbegriff)   echo "$DATA_DIR/authorities-gnd-sachbegriff_lds.jsonld.gz" ;;
        entityfacts)   echo "$DATA_DIR/authorities-gnd_entityfacts.jsonld.gz" ;;
        *) die "Unknown entity type: '$entity'. Valid: $ALL_ENTITY_TYPES" ;;
    esac
}

# ── Helpers ───────────────────────────────────────────────────────────────────
log() { echo "[$(date +%H:%M:%S)] $*"; }
die() { echo "ERROR: $*" >&2; exit 1; }

check_deps() {
    command -v docker  &>/dev/null || die "'docker' is required but not found"
    command -v python3 &>/dev/null || die "'python3' is required but not found"
    python3 -c "import rdflib" 2>/dev/null \
        || die "rdflib not installed — run: pip install rdflib"
}

# ── Step 1: JSON-LD → N-Triples ───────────────────────────────────────────────
# Uses scripts/jsonld_to_nt.py (rdflib) — no Docker, no JVM.
# Skipped if the .nt file already exists and --rebuild is not set.
convert_to_nt() {
    local gz_path="$1"
    local entity_type="$2"
    local nt_path="$NT_DIR/${entity_type}.nt"

    if [ ! -f "$gz_path" ]; then
        log "  WARNING: $gz_path not found — skipping $entity_type"
        return
    fi

    if [ -f "$nt_path" ] && [ "$REBUILD" = "false" ]; then
        log "  Skipping $entity_type (NT exists; use --rebuild to redo)"
        return
    fi

    local gz_size
    gz_size=$(du -sh "$gz_path" | cut -f1)
    log "Converting $entity_type ($gz_size) → $(basename "$nt_path")"
    mkdir -p "$NT_DIR"

    python3 "$SCRIPT_DIR/jsonld_to_nt.py" "$gz_path" "$nt_path" \
        || { rm -f "$nt_path"; die "Conversion failed for $entity_type"; }
}

# ── Step 2: Build index ────────────────────────────────────────────────────────
# qlever index reads the index name from a Qleverfile (sets the basename for
# index files), but --input-files must be passed on the CLI.
# A minimal Qleverfile with just the name field is written to INDEX_DIR.
build_index() {
    local nt_files_relative="$1"  # space-separated relative paths, e.g. "nt/werk.nt"

    # Write Qleverfile: name sets the index basename; system=native tells qlever
    # to run qlever-index directly instead of spawning a nested Docker container.
    printf '[DEFAULT]\nname = %s\n\n[runtime]\nsystem = native\n' "$INDEX_NAME" \
        > "$INDEX_DIR/Qleverfile"

    log "Building QLever index from: $nt_files_relative"

    docker run --rm \
        -u "$(id -u):$(id -g)" \
        -v "${INDEX_DIR}:/data" \
        -w /data \
        "$QLEVER_IMAGE" \
        -c "qlever index --input-files '${nt_files_relative}' --cat-input-files 'cat ${nt_files_relative}'" \
        || die "QLever index build failed"

    log "Index built at $INDEX_DIR"
}

# ── Step 4: Start server ───────────────────────────────────────────────────────
# Runs qlever start (which launches ServerMain in the background), then waits
# for it so the container stays alive. Health-checked from the host via curl.
start_server() {
    if docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
        log "QLever already running ($CONTAINER_NAME)"
        return
    fi

    if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
        log "Removing stopped container..."
        docker rm "$CONTAINER_NAME" >/dev/null
    fi

    # Verify index exists before attempting to start
    ls "$INDEX_DIR/${INDEX_NAME}."* >/dev/null 2>&1 \
        || die "No QLever index found at $INDEX_DIR. Run without --start-only first."

    log "Starting QLever server on port $QLEVER_PORT..."
    docker run -d \
        --name  "$CONTAINER_NAME" \
        -v "${INDEX_DIR}:/data" \
        -p "${QLEVER_PORT}:7001" \
        -w /data \
        "$QLEVER_IMAGE" \
        -c "qlever start --port 7001 --num-threads 8 --memory-for-queries 4096M && wait"

    # Poll host-side until SPARQL endpoint responds (up to 60s)
    log "Waiting for QLever to be ready..."
    local attempts=0
    until curl -sf "http://localhost:${QLEVER_PORT}" &>/dev/null; do
        sleep 2
        attempts=$((attempts + 1))
        [ $attempts -ge 30 ] \
            && die "QLever did not respond after 60s — check: docker logs $CONTAINER_NAME"
    done
    log "QLever is ready"
}

# ── Usage ──────────────────────────────────────────────────────────────────────
usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Convert GND JSON-LD files to N-Triples, build a QLever index, and start the
SPARQL server. QLever requires all data at index-build time (not incremental).

Options:
  --load <types>    Comma-separated entity types, or 'all'
                    Valid: $ALL_ENTITY_TYPES
                    Default: werk
  --start-only      Start the server from an existing index (skip build)
  --rebuild         Force reconversion and full index rebuild
  --help            Show this help

Examples:
  $(basename "$0")                        # convert werk + build + start
  $(basename "$0") --load all             # all entity types
  $(basename "$0") --load werk,person
  $(basename "$0") --start-only           # restart server only
  $(basename "$0") --rebuild              # force full rebuild

Paths:
  Source:   $DATA_DIR/
  NT files: $NT_DIR/
  Index:    $INDEX_DIR/
  SPARQL:   http://localhost:${QLEVER_PORT}

Notes:
  - NT conversion is skipped if the file already exists (unless --rebuild).
  - Index build always overwrites. Adding entity types requires --rebuild.
  - Container keeps running via 'wait' on the ServerMain background process.
EOF
}

# ── Main ───────────────────────────────────────────────────────────────────────
main() {
    local load_types="werk"
    local start_only=false

    while [ $# -gt 0 ]; do
        case "$1" in
            --load)       load_types="$2"; shift 2 ;;
            --start-only) start_only=true; shift ;;
            --rebuild)    REBUILD=true; shift ;;
            --help|-h)    usage; exit 0 ;;
            *) die "Unknown argument: '$1'. Use --help for usage." ;;
        esac
    done

    check_deps

    if $start_only; then
        start_server
        log "  SPARQL endpoint: http://localhost:${QLEVER_PORT}"
        exit 0
    fi

    # Resolve entity type list
    local types_list
    if [ "$load_types" = "all" ]; then
        types_list="$ALL_ENTITY_TYPES"
    else
        types_list="$(echo "$load_types" | tr ',' ' ')"
    fi

    # Step 1: Convert each type to NT
    for entity in $types_list; do
        entity="$(echo "$entity" | tr -d ' ')"
        convert_to_nt "$(gnd_file_for "$entity")" "$entity"
    done

    # Step 2: Build index — collect relative NT paths (nt/<entity>.nt)
    local nt_files_relative=""
    for entity in $types_list; do
        entity="$(echo "$entity" | tr -d ' ')"
        [ -f "$NT_DIR/${entity}.nt" ] \
            && nt_files_relative="${nt_files_relative} nt/${entity}.nt"
    done
    nt_files_relative="$(echo "$nt_files_relative" | xargs)"  # trim whitespace
    [ -n "$nt_files_relative" ] || die "No NT files found to index."
    build_index "$nt_files_relative"

    # Step 4: Start server
    start_server

    log "All done."
    log "  SPARQL endpoint: http://localhost:${QLEVER_PORT}"
}

main "$@"
