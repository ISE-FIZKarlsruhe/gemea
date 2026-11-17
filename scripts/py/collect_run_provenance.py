#!/usr/bin/env python3
# Purpose:      Gather what PROV-CRED needs from one Werk linking run
#               into one provenance record, and count the triples of its sub-graphs. Read-only on
#               the run's files.
# Usage:        On the GPU host, for the facts only that host has (input SHA-256, model revision, versions, GPUs):
#                   nice -n 10 ionice -c3 uv run --no-project --python 3.12 --with 'flair==0.15.1' \
#                       --with 'torch==2.7.1+cu118' --extra-index-url https://download.pytorch.org/whl/cu118 \
#                       python scripts/py/collect_run_provenance.py host-facts \
#                       [--input-parquet data/out/s2/s2_meta_de_content.parquet] \
#                       [--rule-script data/processed/nerl-gnd/t10_rule_baseline.py] --output <run folder>/host_facts.json
#               On the Mac, after copying the run and host_facts.json:
#                   .venv/bin/python scripts/py/collect_run_provenance.py record --run-dir <run folder> \
#                       [--host-facts <run folder>/host_facts.json] [--force] [--dry-run]
#                   .venv/bin/python scripts/py/collect_run_provenance.py counts --run-dir <run folder> \
#                       [--date yyyymmdd] [--force] [--dry-run]
# Inputs:       --run-dir  a run folder with werk_spans/run-shardK.json and manifest-shardK.jsonl,
#                          werk_spans_shardK.log, werk_links_part0N.log and .csv.parts/run.json,
#                          werk_links_merge.json, and SHA256SUMS or the files it names.
#               --host-facts  the JSON of host-facts; without it, items e, f, g and j are missing.
# Outputs:      host-facts: JSON to --output (refuses to overwrite) or stdout.
#               record: <run-dir>/provcred_record.json, items a to j,
#                   each with value, source and status (found or missing); missing items are
#                   listed, never guessed.
#               counts: <run-dir>/subgraph_counts_<date>.csv: graph, linked_rows, distinct_works,
#                   triples; one row per matched_span_source, then works, then all.
#               Exit codes: 0 success, 1 failure, 2 usage error.
# Dependencies: standard library; host-facts reads flair and torch versions if installed.
# Assumptions:  Logs carry local time and run.json and manifests UTC; the offset is read from each
#               part (its "Wrote" line against its output finished_at) and must agree across parts.
#               The linker run ID is the run_id of werk_links_merge.json when it has one, else the
#               earliest part start in UTC (run ID format); step 1's run-id.json and the
#               input SHA-256, model caches and versions of run-shardK.json are used when present
#               (runs from gemea 2026-11-11 on), host_facts.json otherwise. Triples:
#               2 per linked row in its span-source sub-graph, 3 per distinct Work in /works.
#               The model cache is looked for under ~/.flair, ~/.cache/huggingface, $HF_HOME and
#               $FLAIR_CACHE_ROOT; the download date is the oldest modification time in its snapshot.

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata
import json
import logging
import os
import re
import socket
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, NoReturn

MODEL_DIR_NAME = "models--flair--ner-english-ontonotes-large"
LOG_TIME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),(\d{3}) ")
OFFSET_STEP = timedelta(minutes=15)
TRIPLES_PER_LINK = 2
TRIPLES_PER_WORK = 3
COUNT_COLUMNS = ["graph", "linked_rows", "distinct_works", "triples"]

logger = logging.getLogger("collect_run_provenance")


def die(message: str) -> NoReturn:
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        die(f"cannot read {path} ({e}); copy the run's records again")


def is_empty(value: Any) -> bool:
    """None, an empty list or dict, or a dict whose values are all empty."""
    if isinstance(value, dict):
        return all(is_empty(v) for v in value.values())
    return value is None or value == []


def item(value: Any, source: str) -> dict[str, Any]:
    return {"value": value, "source": source, "status": "missing" if is_empty(value) else "found"}


def utc_id(moment: datetime) -> str:
    """The run ID of a UTC time: 20261110T090205Z."""
    return moment.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")  # noqa: UP017 (3.9-compatible)


# --- Logs and times ---


def log_times(path: Path) -> tuple[datetime, datetime, str]:
    """First and last local timestamps of a log, and its last timestamped line."""
    first = last = None
    last_line = ""
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = LOG_TIME_RE.match(line)
            if m:
                moment = datetime.strptime(f"{m.group(1)}.{m.group(2)}", "%Y-%m-%d %H:%M:%S.%f")
                first = first or moment
                last, last_line = moment, line
    if first is None or last is None:
        die(f"{path} has no timestamped line; copy the full log")
    return first, last, last_line


def rounded_offset(local: datetime, utc: datetime) -> timedelta:
    """Local minus UTC, rounded to 15 minutes."""
    delta = local - utc.replace(tzinfo=None)
    return round(delta / OFFSET_STEP) * OFFSET_STEP


def part_offsets(parts: list[tuple[Path, Path]]) -> timedelta:
    """The host's UTC offset, from each part's 'Wrote' line against its output finished_at; all must agree."""
    offsets = set()
    for log, state in parts:
        _, last, line = log_times(log)
        if "Wrote" not in line:
            die(f"{log} does not end with 'Wrote'; the part has not finished")
        finished = datetime.fromisoformat(read_json(state)["output"]["finished_at"])
        offsets.add(rounded_offset(last, finished))
    if len(offsets) != 1:
        die(f"the parts give different UTC offsets ({sorted(offsets)}); check the host clock")
    return offsets.pop()


def to_utc(local: datetime, offset: timedelta) -> datetime:
    return (local - offset).replace(tzinfo=timezone.utc)  # noqa: UP017 (3.9-compatible)


def last_manifest_time(path: Path) -> datetime | None:
    """The latest finished_at of a step 1 manifest (UTC)."""
    latest = None
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            moment = datetime.fromisoformat(json.loads(line)["finished_at"])
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
        latest = moment if latest is None or moment > latest else latest
    return latest


# --- Checksums ---


def listed_sums(run_dir: Path) -> dict[str, str]:
    path = run_dir / "SHA256SUMS"
    if not path.is_file():
        return {}
    sums = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) == 2:
            sums[parts[1].lstrip("*")] = parts[0]
    return sums


def checksum(run_dir: Path, name: str, sums: dict[str, str]) -> dict[str, Any]:
    """The SHA-256 of a run file: hashed if present (and compared with SHA256SUMS), else from SHA256SUMS."""
    path = run_dir / name
    if path.is_file():
        value = sha256(path)
        if name in sums and sums[name] != value:
            die(f"{path} does not match SHA256SUMS; copy it again")
        return item(value, f"sha256 of {name}" + (", equal to SHA256SUMS" if name in sums else ""))
    return item(sums.get(name), f"SHA256SUMS ({name} not on this host)")


# --- record ---


def run_files(run_dir: Path) -> tuple[list[tuple[Path, Path]], list[int]]:
    parts = sorted(run_dir.glob("werk_links_part0?.log"))
    if not parts:
        die(f"no werk_links_part0?.log in {run_dir}; pass a run folder")
    pairs = []
    for log in parts:
        state = run_dir / log.name.replace(".log", ".csv.parts") / "run.json"
        if not state.is_file():
            die(f"{state} not found; copy each part's run.json")
        pairs.append((log, state))
    shards = sorted(int(p.stem.removeprefix("run-shard")) for p in (run_dir / "werk_spans").glob("run-shard*.json"))
    if not shards:
        die(f"no werk_spans/run-shard*.json in {run_dir}; copy step 1's run records")
    return pairs, shards


def step1_items(run_dir: Path, shards: list[int], offset: timedelta) -> dict[str, Any]:
    shard_times = {}
    for k in shards:
        log = run_dir / f"werk_spans_shard{k}.log"
        manifest = run_dir / "werk_spans" / f"manifest-shard{k}.jsonl"
        start = to_utc(log_times(log)[0], offset).isoformat() if log.is_file() else None
        end = last_manifest_time(manifest) if manifest.is_file() else None
        shard_times[f"shard{k}"] = {"started_at": start, "ended_at": end.isoformat() if end else None}
    starts = [t["started_at"] for t in shard_times.values() if t["started_at"]]
    first = min(starts) if starts else None
    identity_path = run_dir / "werk_spans" / "run-id.json"
    recorded = read_json(identity_path)["run_id"] if identity_path.is_file() else None
    return {
        "c_step1_times": item(shard_times, "first log line of werk_spans_shardK.log (local, to UTC); last finished_at of manifest-shardK.jsonl"),
        "d_step1_id_candidates": item(
            {"utc_start_of_earlier_shard": utc_id(datetime.fromisoformat(first)) if first else None, "run_id_json": recorded},
            "earliest shard start above; the SHA-256 of werk_spans.jsonl is item i. Not chosen",
        ),
    }


def host_items(facts: dict[str, Any] | None, settings: dict[str, Any]) -> dict[str, Any]:
    if facts is None and settings.get("input_sha256"):
        facts = {
            "input_parquet": {"path": settings.get("input"), "sha256": settings["input_sha256"],
                              "size": settings.get("input_size"), "mtime": settings.get("input_mtime")},
            "model_caches": settings.get("model_caches"),
            "versions": settings.get("versions"),
        }  # fmt: skip
        logger.info("Input SHA-256, model caches and versions from run-shardK.json; hosts and GPUs need host_facts.json")
    if facts is None:
        missing = "host_facts.json not given; run host-facts on the GPU host"
        return {key: item(None, missing) for key in ("e_input_sha256", "f_model_revision", "g_flair_torch_versions", "j_hosts_and_gpus")}
    parquet = facts.get("input_parquet") or {}
    matches = None
    if parquet.get("size") is not None:
        matches = parquet["size"] == settings.get("input_size") and parquet.get("mtime") == settings.get("input_mtime")
    return {
        "e_input_sha256": item(
            {"path": parquet.get("path"), "sha256": parquet.get("sha256"), "size_and_mtime_match_run_shard0": matches},
            "host_facts.json input_parquet, against run-shard0.json input_size and input_mtime",
        ),
        "f_model_revision": item(facts.get("model_caches"), "host_facts.json model_caches (refs/main, oldest snapshot mtime)"),
        "g_flair_torch_versions": item(facts.get("versions"), "host_facts.json versions (importlib.metadata in the step 1 environment)"),
        "j_hosts_and_gpus": item({"host": facts.get("host"), "gpus": facts.get("gpus"), "timezone": facts.get("timezone")}, "host_facts.json"),
    }


def build_record(run_dir: Path, facts: dict[str, Any] | None) -> dict[str, Any]:
    parts, shards = run_files(run_dir)
    offset = part_offsets(parts)
    merge = read_json(run_dir / "werk_links_merge.json")
    settings = read_json(run_dir / "werk_spans" / f"run-shard{shards[0]}.json")
    part_times = {}
    for log, state in parts:
        start = to_utc(log_times(log)[0], offset)
        part_times[log.stem.removeprefix("werk_links_")] = {
            "started_at": start.isoformat(),
            "ended_at": read_json(state)["output"]["finished_at"],
        }
    first_start = min(datetime.fromisoformat(t["started_at"]) for t in part_times.values())
    if merge.get("run_id"):
        run_id = item(merge["run_id"], "werk_links_merge.json run_id")
    else:
        run_id = item(utc_id(first_start), "earliest first log line of werk_links_part0?.log, to UTC")
    sums = listed_sums(run_dir)
    links_sha = checksum(run_dir, "werk_links.csv", sums)
    if links_sha["value"] and links_sha["value"] != merge["output"]["sha256"]:
        die("werk_links.csv does not match werk_links_merge.json; copy the run again")
    rule_host = (facts or {}).get("rule_script") or {}
    return {
        "run_dir": str(run_dir),
        "collected_at": datetime.now(timezone.utc).isoformat(),  # noqa: UP017 (3.9-compatible)
        "utc_offset_of_logs": item(str(offset), "each part's 'Wrote' log line against its run.json output finished_at"),
        "a_linker_run_id": run_id,
        "b_linker_times": item(
            {"parts": part_times, "merge": {"started_at": None, "ended_at": merge["output"].get("finished_at")}},
            "part logs (start) and run.json output finished_at (end); merge end from werk_links_merge.json, its start is not recorded",
        ),
        **step1_items(run_dir, shards, offset),
        **host_items(facts, settings),
        "h_rule_script": item(
            {"rule_sha256": settings.get("rule_sha256"), "sha256_on_host": rule_host.get("sha256"), "bibner_werk_commit": "050820b"},
            "run-shardK.json rule_sha256; host_facts.json rule_script; commit from the extract_werk_spans.py header",
        ),
        "i_output_sha256": item(
            {"werk_spans.jsonl": checksum(run_dir, "werk_spans.jsonl", sums)["value"], "werk_links.csv": links_sha["value"]},
            "sha256 or SHA256SUMS; werk_links.csv equal to werk_links_merge.json output",
        ),
        "commits": item(
            {"step1": settings.get("commit"), "step2": merge["script"]["commit"], "merge": merge["merged_by"]["commit"]},
            "run-shardK.json, werk_links_merge.json",
        ),
    }


# --- counts ---


def subgraph_counts(links: Path) -> list[list[Any]]:
    """Linked rows per span source, distinct Works, and triples."""
    rows: Counter[str] = Counter()
    works: set[str] = set()
    with open(links, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        missing = {"status", "gnd_uri", "matched_span_source"} - set(reader.fieldnames or [])
        if missing:
            die(f"{links} has no column {', '.join(sorted(missing))}; pass a link_gnd_local.py output")
        for row in reader:
            if row["status"] == "linked":
                rows[row["matched_span_source"] or "(none)"] += 1
                works.add(row["gnd_uri"])
    table: list[list[Any]] = [[source, n, "", n * TRIPLES_PER_LINK] for source, n in sorted(rows.items())]
    table.append(["works", "", len(works), len(works) * TRIPLES_PER_WORK])
    linked = sum(rows.values())
    table.append(["all", linked, len(works), linked * TRIPLES_PER_LINK + len(works) * TRIPLES_PER_WORK])
    return table


# --- host-facts ---


def model_caches() -> list[dict[str, Any]]:
    roots = [Path.home() / ".flair", Path.home() / ".cache" / "huggingface"]
    roots += [Path(os.environ[v]) for v in ("HF_HOME", "FLAIR_CACHE_ROOT") if os.environ.get(v)]
    found = []
    for root in roots:
        if not root.is_dir():
            continue
        for model_dir in root.rglob(MODEL_DIR_NAME):
            if ".locks" in model_dir.parts:  # huggingface_hub's lock folder, not a copy of the model
                continue
            ref = model_dir / "refs" / "main"
            revision = ref.read_text(encoding="utf-8").strip() if ref.is_file() else None
            snapshot = model_dir / "snapshots" / revision if revision else None
            mtimes = [p.stat().st_mtime for p in snapshot.rglob("*") if p.is_file()] if snapshot and snapshot.is_dir() else []
            downloaded = datetime.fromtimestamp(min(mtimes), timezone.utc).date().isoformat() if mtimes else None  # noqa: UP017
            found.append({"path": str(model_dir), "revision": revision, "download_date": downloaded})
    return found


def package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def gpu_names() -> list[str]:
    try:
        result = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"], capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def file_facts(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"path": str(path), "sha256": None, "size": None, "mtime": None}
    stat = path.stat()
    return {"path": str(path.resolve()), "sha256": sha256(path), "size": stat.st_size, "mtime": stat.st_mtime}


def host_facts(args: argparse.Namespace) -> dict[str, Any]:
    caches = model_caches()
    if len(caches) > 1:
        logger.warning("Found %d copies of the model; all are recorded", len(caches))
    return {
        "collected_at": datetime.now(timezone.utc).isoformat(),  # noqa: UP017 (3.9-compatible)
        "host": socket.gethostname(),
        "timezone": {"name": time.strftime("%Z"), "utc_offset": time.strftime("%z")},
        "input_parquet": file_facts(args.input_parquet),
        "rule_script": file_facts(args.rule_script),
        "model_caches": caches,
        "versions": {"flair": package_version("flair"), "torch": package_version("torch")},
        "gpus": gpu_names(),
    }


# --- Main ---


def write_text_atomic(path: Path, text: str) -> None:
    temp = path.with_name(path.name + ".tmp")
    try:
        temp.write_text(text, encoding="utf-8")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def guard(path: Path, force: bool) -> None:
    if path.exists() and not force:
        die(f"{path} exists; pass --force to overwrite")
    if not os.access(path.parent, os.W_OK):
        die(f"{path.parent} is not writable; choose another folder")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Gather the PROV-CRED record of one Werk linking run.")
    sub = parser.add_subparsers(dest="command", required=True)
    host = sub.add_parser("host-facts", help="on the GPU host: input SHA-256, model revision, versions, GPUs")
    host.add_argument("--input-parquet", type=Path, default=Path("data/out/s2/s2_meta_de_content.parquet"))
    host.add_argument("--rule-script", type=Path, default=Path("data/processed/nerl-gnd/t10_rule_baseline.py"))
    host.add_argument("--output", type=Path, help="JSON file to write (default: stdout)")
    record = sub.add_parser("record", help="write <run-dir>/provcred_record.json")
    record.add_argument("--run-dir", type=Path, required=True)
    record.add_argument("--host-facts", type=Path)
    counts = sub.add_parser("counts", help="write <run-dir>/subgraph_counts_<date>.csv")
    counts.add_argument("--run-dir", type=Path, required=True)
    counts.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    for p in (record, counts):
        p.add_argument("--force", action="store_true", help="overwrite the output")
        p.add_argument("--dry-run", action="store_true", help="print the output; write nothing")
    args = parser.parse_args(argv)
    if args.command != "host-facts" and not args.run_dir.is_dir():
        parser.error(f"--run-dir {args.run_dir} is not a folder")
    if args.command == "counts" and not re.fullmatch(r"\d{8}", args.date):
        parser.error("--date must be yyyymmdd")
    return args


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args(argv)
    if args.command == "host-facts":
        if args.output:
            guard(args.output, force=False)
        text = json.dumps(host_facts(args), indent=2) + "\n"
        if args.output:
            write_text_atomic(args.output, text)
            logger.info("Wrote %s", args.output)
        else:
            sys.stdout.write(text)
        return 0
    if args.command == "record":
        facts = read_json(args.host_facts) if args.host_facts else None
        record = build_record(args.run_dir, facts)
        missing = [k for k, v in record.items() if isinstance(v, dict) and v.get("status") == "missing"]
        text = json.dumps(record, indent=2, ensure_ascii=False) + "\n"
        output = args.run_dir / "provcred_record.json"
        if args.dry_run:
            sys.stdout.write(text)
        else:
            guard(output, args.force)
            write_text_atomic(output, text)
            logger.info("Wrote %s", output)
        logger.info("Missing items: %s", ", ".join(missing) or "none")
        return 0
    links = args.run_dir / "werk_links.csv"
    if not links.is_file():
        die(f"{links} not found; copy the run's werk_links.csv")
    table = subgraph_counts(links)
    merge = read_json(args.run_dir / "werk_links_merge.json")
    expected = merge["output"]["statuses"].get("linked", 0)
    if table[-1][1] != expected:
        die(f"{table[-1][1]} linked rows, but werk_links_merge.json says {expected}")
    output = args.run_dir / f"subgraph_counts_{args.date}.csv"
    if args.dry_run:
        for row in [COUNT_COLUMNS, *table]:
            print(",".join(str(v) for v in row))
        return 0
    guard(output, args.force)
    temp = output.with_name(output.name + ".tmp")
    try:
        with open(temp, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(COUNT_COLUMNS)
            writer.writerows(table)
        os.replace(temp, output)
    finally:
        temp.unlink(missing_ok=True)
    logger.info("Wrote %s: %d linked rows, check total %d from werk_links_merge.json", output, table[-1][1], expected)
    return 0


if __name__ == "__main__":
    sys.exit(main())
