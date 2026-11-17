#!/usr/bin/env bash
# Purpose:   Show per-sector transform progress: processed, remaining, rate, elapsed, ETA
# Usage:     bash scripts/transform/run/monitor_transform.sh [OUT_BASE] [INTERVAL_SEC]
#            OUT_BASE defaults to /data/gemea/dryrun
#            INTERVAL_SEC defaults to 5
# Inputs:    $OUT_BASE/s{1..7}/*.log  (written automatically by python -m transform)
# Deps:      grep (with -oP/PCRE), awk, cat (stdlib on Linux)
# Notes:     Works for both dryrun (one log per sector) and full transform (multiple
#            chunk logs per sector — stats are summed/aggregated across chunks).

OUT_BASE=${1:-/data/gemea/dryrun}
INTERVAL=${2:-5}
SEP=$(printf '─%.0s' {1..84})

_sector_row() {
    local n=$1
    local dir="$OUT_BASE/s${n}"
    local logs=()
    while IFS= read -r -d '' f; do logs+=("$f"); done \
        < <(find "$dir" -maxdepth 2 -name "*.log" ! -name "export.log" -print0 2>/dev/null)

    if [[ ${#logs[@]} -eq 0 ]]; then
        printf "  s%-2s  %10s  %10s  %10s  %12s  %10s  %s\n" \
            "$n" "—" "—" "—" "—" "—" "not started"
        return
    fi

    local combined
    combined=$(cat "${logs[@]}" 2>/dev/null)

    # ── Finished? ────────────────────────────────────────────────────────────
    local done_line
    done_line=$(echo "$combined" | grep -E " (Done|Interrupted):" | tail -1)
    if [[ -n "$done_line" ]]; then
        local proc elapsed rate
        proc=$(echo    "$done_line" | grep -oP '\d+(?= records in)')
        elapsed=$(echo "$done_line" | grep -oP 'in \K[0-9:]+')
        rate=$(echo    "$done_line" | grep -oP '[\d.]+(?= rec/s)')
        printf "  s%-2s  %10s  %10s  %10s  %12s  %10s  %s\n" \
            "$n" "${proc:-?}" "—" "0" "${rate:+${rate} r/s}" "${elapsed:-?}" "DONE"
        return
    fi

    # ── In progress ──────────────────────────────────────────────────────────
    local prog_line
    prog_line=$(echo "$combined" | grep "Progress:" | tail -1)

    if [[ -z "$prog_line" ]]; then
        printf "  s%-2s  %10s  %10s  %10s  %12s  %10s  %s\n" \
            "$n" "—" "—" "—" "—" "—" "starting…"
        return
    fi

    local proc total rate elapsed eta remaining
    proc=$(echo    "$prog_line" | grep -oP 'Progress: \K[\d,]+' | tr -d ',')
    total=$(echo   "$prog_line" | grep -oP '/\K[\d,]+(?= records)' | tr -d ',')
    rate=$(echo    "$prog_line" | grep -oP '[\d.]+(?= rec/s)')
    elapsed=$(echo "$prog_line" | grep -oP 'elapsed \K[0-9:]+')
    eta=$(echo     "$prog_line" | grep -oP 'ETA \K[0-9:]+')

    if [[ -n "$total" && -n "$proc" ]]; then
        remaining=$(( total - proc ))
    else
        remaining="?"
        total="?"
    fi

    printf "  s%-2s  %10s  %10s  %10s  %12s  %10s  %s\n" \
        "$n" "${proc:-?}" "${total}" "${remaining}" \
        "${rate:+${rate} r/s}" "${elapsed:-?}" "${eta:----}"
}

_all_done() {
    local n done_count=0
    for n in 1 2 3 4 5 6 7; do
        local dir="$OUT_BASE/s${n}"
        local logs=()
        while IFS= read -r -d '' f; do logs+=("$f"); done \
            < <(find "$dir" -maxdepth 2 -name "*.log" ! -name "export.log" -print0 2>/dev/null)
        [[ ${#logs[@]} -eq 0 ]] && return 1
        combined=$(cat "${logs[@]}" 2>/dev/null)
        echo "$combined" | grep -qE " (Done|Interrupted):" && (( done_count++ ))
    done
    [[ $done_count -eq 7 ]]
}

while true; do
    clear
    echo "  GeMeA Transform Monitor  —  $(date '+%F %T')"
    echo "  $OUT_BASE"
    echo
    printf "  %-4s  %10s  %10s  %10s  %12s  %10s  %s\n" \
        "Sect" "Processed" "Total" "Remaining" "Rate" "Elapsed" "ETA"
    echo "  $SEP"
    for n in 1 2 3 4 5 6 7; do
        _sector_row "$n"
    done
    echo "  $SEP"
    echo "  Refresh: ${INTERVAL}s  —  Ctrl-C to stop"

    _all_done && { echo; echo "  All sectors done."; break; }
    sleep "$INTERVAL"
done
