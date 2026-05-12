"""
Purpose:    Transform DDB-EDM JSONL records to mocho-aligned N-Quads.
            Produces four named-graph streams: ddbedm (verbatim EDM passthrough),
            mocho (mocho-aligned triples), prov (PROV-O Layer 1), and a DuckDB
            werk_staging table for GND Werk linking (link_gnd_works.py, Phase 0).
            Reference implementation for the mocho ingest pipeline.
            Decisions: transform-adr.md D11/D15/D17, transform-script-adr.md D1–D27.
Usage:      python -m transform
                [--jsonl FILE] [--ids FILE] [--outdir DIR]
                [--alignment FILE] [--lido FILE] [--htype FILE]
                [--mediatype FILE] [--audio FILE]
                [--stats LEVEL] [--log-level LEVEL]
                [--limit N] [--total N] [--log-interval N] [--debug]
Inputs:     data/items-all-goethe-faust.json              JSONL, one record per line
            data/ids-all-goethe-faust.txt                  32-char object IDs, one per line
            output/config/lookup_class_prop_alignment.csv  (target_class, edm_prop) → target_prop
            output/config/lido_event_types.csv             lido_uri → agent predicates per WEMI
            output/config/lookup_htype_doco_rico.csv       htype_code → (rdf_type, rst_iris)
            output/config/lookup_mediatype_class.csv       (sparte, mediatype) → class dispatch row
            output/config/audio_type2class.json            mt001 dc:type → group (A/B/C)
Outputs:    output/transform/YYYYMMDD_HHMMSS/               run directory (one per invocation)
              <stem>.nq                                    combined N-Quads (all named graphs)
              <stem>-werk-staging.duckdb                  W-slot staging rows
              <stem>-stats.json                           run statistics
              <stem>-errors.jsonl                         per-record errors (written live)
              <stem>.log                                  run log
            <stem> is the input filename without extension (e.g. s2 → s2.nq)
Deps:       stdlib only + duckdb (pip install duckdb)
Assumes:    JSONL: one JSON object per line; record structure edm.RDF.*
            All config CSVs present at output/config/
            Graceful exit on SIGINT/SIGTERM: partial stats and errors are written.
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import time
import traceback
from collections import Counter
from datetime import datetime
from pathlib import Path

from .constants import (
    DEFAULT_JSONL, DEFAULT_ALIGNMENT, DEFAULT_LIDO, DEFAULT_HTYPE,
    DEFAULT_MEDIATYPE, DEFAULT_AUDIO, DEFAULT_OUTPUT_BASE,
)
from .utils import get_object_id, _to_curie
from .loaders import (
    load_ids, load_class_prop_alignment, load_lido_event_types,
    load_htype_map, load_mediatype_class, load_audio_type2class,
)
from .transform import transform_record


_stop = False


def _handle_signal(sig, frame) -> None:
    global _stop
    _stop = True
    print(f"\nSignal {sig} received — stopping after current record ...", file=sys.stderr)


signal.signal(signal.SIGINT,  _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


def _fmt_duration(seconds: float) -> str:
    h, r = divmod(int(seconds), 3600)
    m, s = divmod(r, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "merge":
        from .merge import main as _merge_main
        sys.argv = [sys.argv[0]] + sys.argv[2:]
        _merge_main()
        return

    parser = argparse.ArgumentParser(
        description="Transform DDB-EDM JSONL to mocho N-Quads (§8)"
    )

    io = parser.add_argument_group("I/O")
    io.add_argument("--jsonl",  type=Path, default=DEFAULT_JSONL,
                    help="JSONL input file (one DDB-EDM JSON object per line); "
                         "default: data/items-all-goethe-faust.json")
    io.add_argument("--db",     type=Path, default=None,
                    help="SQLite sector file — reads directly without intermediate JSONL export; "
                         "mutually exclusive with --jsonl")
    io.add_argument("--offset", type=int,  default=0,
                    help="Skip first N rows in SQLite (for parallel workers; use with --db and --limit)")
    io.add_argument("--ids",    type=str,  default=None,
                    help="Path to ID allowlist file (one 32-char DDB ID per line), "
                         "or '-' to read from stdin; omit to process all records")
    io.add_argument("--outdir", type=Path, default=None,
                    help="Output directory; auto-timestamped if omitted "
                         "(default: output/transform/YYYYMMDD_HHMMSS)")
    io.add_argument("--stem", type=str, default=None,
                    help="Output filename stem (overrides the input filename); "
                         "e.g. --stem items-all-goethe-faust → items-all-goethe-faust.nq, etc.")

    cfg = parser.add_argument_group("Config")
    cfg.add_argument("--alignment", type=Path, default=DEFAULT_ALIGNMENT,
                     help="Property alignment lookup CSV "
                          "(target_class, edm_prop) → mocho property; "
                          "default: output/config/lookup_class_prop_alignment.csv")
    cfg.add_argument("--lido",      type=Path, default=DEFAULT_LIDO,
                     help="LIDO event type dispatch CSV "
                          "(event URI → agent predicates per WEMI level); "
                          "default: output/config/lido_event_types.csv")
    cfg.add_argument("--htype",     type=Path, default=DEFAULT_HTYPE,
                     help="htype → class lookup CSV "
                          "(htype_code → rdf:type IRIs for §1.1 dispatch); "
                          "default: output/config/lookup_htype_doco_rico.csv")
    cfg.add_argument("--mediatype", type=Path, default=DEFAULT_MEDIATYPE,
                     help="Mediatype × sector → class lookup CSV "
                          "(sector, mediatype → WEMI class IRIs); "
                          "default: output/config/lookup_mediatype_class.csv")
    cfg.add_argument("--audio",     type=Path, default=DEFAULT_AUDIO,
                     help="mt001 dc:type → audio group JSON "
                          "(dc:type value → A/B/C group for audio class dispatch); "
                          "default: output/config/audio_type2class.json")

    run = parser.add_argument_group("Run control")
    run.add_argument("--stats",     choices=["none", "basic", "dispatch", "full"],
                     default="basic",
                     help="Stats verbosity written to transform_stats.json: "
                          "none=nothing written, basic=run/records/triples/werk_staging, "
                          "dispatch=basic+WEMI class counts (recommended for full-corpus runs), "
                          "full=dispatch+per-predicate mocho counts (slow — use with --limit); "
                          "default: basic")
    run.add_argument("--log-level", default="INFO",
                     choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                     dest="log_level",
                     help="Console and file log verbosity; default: INFO")
    run.add_argument("--limit",        type=int, default=None,
                     help="Stop after N records — for smoke-testing and sampling")
    run.add_argument("--total",        type=int, default=None,
                     help="Expected total records — enables ETA in progress log")
    run.add_argument("--log-interval", type=int, default=100_000, dest="log_interval",
                     help="Log a progress line every N records (default: 100000)")
    run.add_argument("--debug",     action="store_true",
                     help="Enable DEBUG logging (shorthand for --log-level DEBUG)")

    args = parser.parse_args()
    if args.debug:
        args.log_level = "DEBUG"

    ts     = datetime.now().strftime("%Y%m%d_%H%M%S")
    outdir = args.outdir or (DEFAULT_OUTPUT_BASE / ts)
    outdir.mkdir(parents=True, exist_ok=True)

    if args.stem:
        stem = args.stem
    elif args.db:
        stem = args.db.stem + (f"-{args.offset}" if args.offset else "")
    else:
        stem = Path(args.jsonl).stem
    out_path    = outdir / f"{stem}.nq"
    werk_path   = outdir / f"{stem}-werk-staging.duckdb"
    stats_path  = outdir / f"{stem}-stats.json"
    errors_path = outdir / f"{stem}-errors.jsonl"
    log_path    = outdir / f"{stem}.log"

    logging.basicConfig(
        filename=str(log_path),
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    log = logging.getLogger(__name__)

    log.info("Loading config tables")
    class_prop_align    = load_class_prop_alignment(args.alignment)
    lido_dispatch       = load_lido_event_types(args.lido)
    htype_map           = load_htype_map(args.htype)
    mediatype_class_map = load_mediatype_class(args.mediatype)
    audio_type2class    = load_audio_type2class(args.audio)

    ids_set: set[str] | None = None
    if args.ids == "-":
        ids_set = {line.strip() for line in sys.stdin if line.strip()}
    elif args.ids:
        ids_set = load_ids(Path(args.ids))

    try:
        import duckdb
        conn = duckdb.connect(str(werk_path))
    except ImportError:
        log.warning("duckdb not available — werk_staging will not be written")
        conn = None
    if conn is not None:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS werk_staging (
            ddb_obj_id       VARCHAR PRIMARY KEY,
            cho_uri          VARCHAR,
            target_class     VARCHAR,
            dc_title         VARCHAR,
            dc_alternative   VARCHAR[],
            dc_created       VARCHAR,
            creator_uris     VARCHAR[],
            creator_literals VARCHAR[]
        )
    """)

    stats_level = args.stats

    stats:  Counter = Counter()

    class_counts: dict[str, Counter] = {"W": Counter(), "E": Counter(), "M": Counter(), "": Counter()}
    mt_dist:         Counter = Counter()
    ht_dist:         Counter = Counter()
    ddbedm_cls:      Counter = Counter()
    ddbedm_preds:    Counter = Counter()
    mocho_preds_all: Counter = Counter()
    mocho_preds_new: Counter = Counter()
    werk_by_class:   Counter = Counter()

    start_time = time.monotonic()
    interrupted = False

    def _iter_input():
        if args.db:
            import gzip
            import sqlite3 as _sqlite3
            conn = _sqlite3.connect(str(args.db))
            try:
                q = "SELECT uid, bufgz FROM objs"
                if args.offset:
                    q += f" LIMIT -1 OFFSET {args.offset}"
                for uid, buf in conn.execute(q):
                    try:
                        yield json.dumps(json.loads(gzip.decompress(buf)))
                    except Exception as exc:
                        log.warning("%s uid=%s: %s", args.db.name, uid, exc)
                        yield ""
            finally:
                conn.close()
        else:
            with open(args.jsonl, encoding="utf-8") as f:
                yield from f

    with open(out_path, "w", encoding="utf-8") as out, \
         open(errors_path, "w", encoding="utf-8") as err_fh:

        for line_no, raw in enumerate(_iter_input(), 1):
            if _stop:
                interrupted = True
                log.warning("Interrupted at line %d — writing partial output", line_no)
                break

            raw = raw.strip()
            if not raw:
                continue
            if args.limit and line_no > args.limit:
                break

            try:
                record = json.loads(raw)
            except json.JSONDecodeError as exc:
                entry = {"line": line_no, "issue": f"JSON parse error: {exc}"}
                err_fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
                stats["json_errors"] += 1
                continue

            obj_id = get_object_id(record) or f"line:{line_no}"
            try:
                streams, werk_row, dispatch_info, pred_info = transform_record(
                    record, ids_set,
                    mediatype_class_map, htype_map, audio_type2class,
                    class_prop_align, lido_dispatch,
                )
            except Exception as exc:
                entry = {
                    "id":        obj_id,
                    "issue":     str(exc),
                    "traceback": traceback.format_exc(),
                }
                err_fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
                stats["record_errors"] += 1
                continue

            if not streams:
                stats["filtered"] += 1
                continue

            for graph_name, graph_lines in streams.items():
                for nq in graph_lines:
                    out.write(nq + "\n")
                    stats["triples_total"] += 1
                    stats[f"triples_{graph_name}"] += 1

            stats["records_processed"]  += 1
            stats["uri_sanitized"]      += pred_info.get("uri_sanitized",   0)
            stats["uri_split"]          += pred_info.get("uri_split",       0)
            stats["uri_about_split"]    += pred_info.get("uri_about_split", 0)

            if stats_level in ("dispatch", "full"):
                if dispatch_info.get("is_mt007"):
                    stats["skipped_mt007"] += 1
                elif dispatch_info.get("fallback"):
                    stats["dispatch_fallback"] += 1
                elif dispatch_info.get("htype_used"):
                    stats["dispatch_htype"] += 1
                else:
                    stats["dispatch_mediatype"] += 1

                tc = dispatch_info.get("target_class", "")
                if tc:
                    wemi = dispatch_info.get("wemi", "M")
                    class_counts[wemi][_to_curie(tc)] += 1

                mt = (dispatch_info.get("mediatype", "") or "").split("/")[-1]
                if mt:
                    mt_dist[mt] += 1

                rdf_top = record["edm"]["RDF"]
                cho_raw = rdf_top.get("ProvidedCHO") or {}
                if isinstance(cho_raw, list):
                    cho_raw = cho_raw[0] if cho_raw else {}
                raw_ht = (cho_raw.get("hierarchyType") or "").strip()
                if raw_ht:
                    ht_dist[raw_ht.replace("htype_", "ht")] += 1

                ddbedm_cls.update(pred_info["ddbedm_classes"])
                ddbedm_preds.update(pred_info["ddbedm_preds"])
                mocho_preds_all.update(pred_info["mocho_preds_all"])
                mocho_preds_new.update(pred_info["mocho_preds_new"])

            if werk_row and conn is not None:
                conn.execute(
                    "INSERT OR REPLACE INTO werk_staging VALUES (?,?,?,?,?,?,?,?)",
                    [
                        werk_row["ddb_obj_id"],
                        werk_row["cho_uri"],
                        werk_row["target_class"],
                        werk_row["dc_title"],
                        werk_row["dc_alternative"],
                        werk_row["dc_created"],
                        werk_row["creator_uris"],
                        werk_row["creator_literals"],
                    ],
                )
                stats["werk_staging_rows"] += 1
                werk_by_class[_to_curie(werk_row["target_class"])] += 1

            processed = stats["records_processed"]
            if args.log_interval and processed and processed % args.log_interval == 0:
                elapsed = time.monotonic() - start_time
                rate    = processed / elapsed if elapsed > 0 else 0
                errors  = stats["json_errors"] + stats["record_errors"]
                eta_str = ""
                if args.total and rate > 0:
                    remaining = (args.total - processed) / rate
                    eta_str   = f" | ETA {_fmt_duration(remaining)}"
                total_str = f"/{args.total:,}" if args.total else ""
                log.info(
                    "Progress: %s%s records | triples %d | errors %d"
                    " | %.0f rec/s | elapsed %s%s",
                    f"{processed:,}", total_str,
                    stats["triples_total"], errors,
                    rate, _fmt_duration(elapsed), eta_str,
                )

    if conn is not None:
        conn.close()

    elapsed_total = time.monotonic() - start_time

    if stats_level != "none":
        stats_out: dict = {
            "run": {
                "timestamp":   ts,
                "input":       str(args.jsonl),
                "stats_level": stats_level,
                "elapsed_s":   round(elapsed_total, 1),
                "interrupted": interrupted,
            },
            "records": {
                "processed":          stats["records_processed"],
                "skipped_not_in_ids": stats["filtered"],
                "by_mediatype": dict(mt_dist.most_common()),
                "by_htype":     dict(ht_dist.most_common()),
                "uri_sanitized":   stats["uri_sanitized"],
                "uri_split":       stats["uri_split"],
                "uri_about_split": stats["uri_about_split"],
                "errors": {
                    "json_parse": stats["json_errors"],
                    "transform":  stats["record_errors"],
                },
            },
            "triples": {
                "total":    stats["triples_total"],
                "by_graph": {
                    "ddbedm": stats["triples_ddbedm"],
                    "mocho":  stats["triples_mocho"],
                    "prov":   stats["triples_prov"],
                },
            },
            "werk_staging": {
                "rows":     stats["werk_staging_rows"],
                "by_class": dict(werk_by_class.most_common()),
            },
        }

        if stats_level in ("dispatch", "full"):
            stats_out["dispatch"] = {
                "htype_hits":            stats["dispatch_htype"],
                "mediatype_hits":        stats["dispatch_mediatype"],
                "fallback_d9":           stats["dispatch_fallback"],
                "skipped_mt007":         stats["skipped_mt007"],
                "work_classes":          dict(class_counts["W"].most_common()),
                "expression_classes":    dict(class_counts["E"].most_common()),
                "manifestation_classes": dict(class_counts["M"].most_common()),
                "rico_classes":          dict(class_counts[""].most_common()),
            }
            stats_out["ddbedm_classes"] = dict(ddbedm_cls.most_common())
            stats_out["ddbedm_vocab"]   = {"properties_all": dict(ddbedm_preds.most_common())}
            stats_out["mocho_vocab"]    = {
                "properties_all": dict(mocho_preds_all.most_common()),
                "properties_new": dict(mocho_preds_new.most_common()),
            }

        with open(stats_path, "w", encoding="utf-8") as f:
            json.dump(stats_out, f, indent=2)

    status = "Interrupted" if interrupted else "Done"
    log.info(
        "%s: %d records in %s (%.0f rec/s) | triples %d (mocho %d) | errors %d",
        status,
        stats["records_processed"],
        _fmt_duration(elapsed_total),
        stats["records_processed"] / elapsed_total if elapsed_total > 0 else 0,
        stats["triples_total"],
        stats["triples_mocho"],
        stats["record_errors"] + stats["json_errors"],
    )


if __name__ == "__main__":
    main()
