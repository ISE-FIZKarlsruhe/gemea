#!/usr/bin/env python3
# Purpose:  Merge multiple per-worker transform stats JSON files into one combined summary.
# Usage:    python3 scripts/transform/tools/merge_stats.py <OUT_BASE> [--out FILE]
#           OUT_BASE: directory containing s{1..7}/ subdirs with *-stats.json files
#           --out:    output path (default: <OUT_BASE>/combined-stats.json)
# Inputs:   <OUT_BASE>/**/*-stats.json
# Outputs:  combined-stats.json
# Deps:     stdlib only

import argparse
import json
from collections import defaultdict
from pathlib import Path


def _add_dict(acc: dict, src: dict) -> None:
    for k, v in src.items():
        if isinstance(v, (int, float)):
            acc[k] = acc.get(k, 0) + v
        elif isinstance(v, dict):
            if k not in acc:
                acc[k] = {}
            _add_dict(acc[k], v)


def merge(paths: list) -> dict:
    records   = defaultdict(int)
    by_mt     = defaultdict(int)
    by_ht     = defaultdict(int)
    errors    = defaultdict(int)
    triples   = defaultdict(int)
    by_graph  = defaultdict(int)
    werk      = defaultdict(int)
    by_class  = defaultdict(int)
    dispatch  = defaultdict(int)
    dclass    = {}   # dispatch sub-dicts (work_classes etc.)
    elapsed   = 0.0
    n_shards  = 0
    stats_level = "none"
    interrupted = False

    for p in paths:
        with open(p) as f:
            s = json.load(f)

        n_shards  += 1
        elapsed   += s.get("run", {}).get("elapsed_s", 0)
        stats_level = s.get("run", {}).get("stats_level", stats_level)
        if s.get("run", {}).get("interrupted"):
            interrupted = True

        r = s.get("records", {})
        records["processed"]          += r.get("processed", 0)
        records["skipped_not_in_ids"] += r.get("skipped_not_in_ids", 0)
        records["uri_sanitized"]      += r.get("uri_sanitized", 0)
        records["uri_split"]          += r.get("uri_split", 0)
        records["uri_about_split"]    += r.get("uri_about_split", 0)
        for k, v in r.get("by_mediatype", {}).items():
            by_mt[k] += v
        for k, v in r.get("by_htype", {}).items():
            by_ht[k] += v
        e = r.get("errors", {})
        errors["json_parse"] += e.get("json_parse", 0)
        errors["transform"]  += e.get("transform", 0)

        t = s.get("triples", {})
        triples["total"] += t.get("total", 0)
        for k, v in t.get("by_graph", {}).items():
            by_graph[k] += v

        w = s.get("werk_staging", {})
        werk["rows"] += w.get("rows", 0)
        for k, v in w.get("by_class", {}).items():
            by_class[k] += v

        d = s.get("dispatch", {})
        for k, v in d.items():
            if isinstance(v, int):
                dispatch[k] += v
            elif isinstance(v, dict):
                if k not in dclass:
                    dclass[k] = defaultdict(int)
                for ck, cv in v.items():
                    dclass[k][ck] += cv

    out = {
        "run": {
            "shards":      n_shards,
            "elapsed_s":   round(elapsed, 1),
            "stats_level": stats_level,
            "interrupted": interrupted,
        },
        "records": {
            "processed":          records["processed"],
            "skipped_not_in_ids": records["skipped_not_in_ids"],
            "by_mediatype": dict(sorted(by_mt.items(), key=lambda x: -x[1])),
            "by_htype":     dict(sorted(by_ht.items(), key=lambda x: -x[1])),
            "uri_sanitized":   records["uri_sanitized"],
            "uri_split":       records["uri_split"],
            "uri_about_split": records["uri_about_split"],
            "errors": dict(errors),
        },
        "triples": {
            "total":    triples["total"],
            "by_graph": dict(by_graph),
        },
        "werk_staging": {
            "rows":     werk["rows"],
            "by_class": dict(sorted(by_class.items(), key=lambda x: -x[1])),
        },
    }

    if dclass or dispatch:
        out["dispatch"] = {**dict(dispatch), **{k: dict(v) for k, v in dclass.items()}}

    return out


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Merge per-worker *-stats.json files into a combined summary"
    )
    parser.add_argument("out_base", type=Path,
                        help="Output base directory (searched recursively for *-stats.json)")
    parser.add_argument("--out", type=Path, default=None,
                        help="Output file (default: <out_base>/combined-stats.json)")
    args = parser.parse_args()

    out_path = args.out or args.out_base / f"{args.out_base.name}-stats.json"
    paths = sorted(p for p in args.out_base.rglob("*-stats.json") if p != out_path)
    if not paths:
        print(f"No shard *-stats.json files found under {args.out_base}")
        return

    result = merge(paths)
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2)

    for p in paths:
        p.unlink()

    r = result["records"]
    t = result["triples"]
    print(f"Merged {len(paths)} shards → {out_path}")
    print(f"  records:  {r['processed']:,}  (errors: {sum(r['errors'].values())})")
    print(f"  triples:  {t['total']:,}  {dict(t['by_graph'])}")


if __name__ == "__main__":
    main()
