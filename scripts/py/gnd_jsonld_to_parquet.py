#!/usr/bin/env python3
# Purpose:      Convert the GND Werk, person and corporate body JSON-LD dumps to the Parquet tables
#               of local GND retrieval, in two passes:
#               Works, then the persons and corporate bodies who are creators of those Works.
# Usage:        nice -n 10 python scripts/py/gnd_jsonld_to_parquet.py \
#                   [--werk PATH] [--person PATH] [--koerperschaft PATH] [--out-dir DIR] \
#                   [--limit N] [--force] [--dry-run]
#               Resumes by default: a finished pass whose dump is unchanged is skipped, so an
#               interrupted run continues with the pass it broke off in (from that pass's start).
# Inputs:       --werk   expanded JSON-LD, one top-level array of arrays of nodes
#                        (default data/gnd/authorities-gnd-werk_lds-20261013.jsonld)
#               --person same layout (default data/gnd/authorities-gnd-person_lds-20261013.jsonld)
#               --koerperschaft same layout (default data/gnd/authorities-gnd-koerperschaft_lds-20261013.jsonld)
#               The dump date is read from the file name (-YYYYMMDD.jsonld).
# Outputs:      In --out-dir (default data/gnd/parquet/):
#                 gnd_work_names.parquet    gnd_id, kind (preferred|variant), name
#                 gnd_work_classes.parquet  gnd_id, class (local name after gnd#)
#                 gnd_work_creators.parquet gnd_id, role, creator_id (the creator roles of werk_link_rules.py)
#                 gnd_creator_names.parquet gnd_id, entity (person|corporate), kind, name (only
#                                           creators of the Works)
#                 manifest.json             dumps (path, URL, date, size, SHA-256), tables (rows,
#                                           SHA-256), passes, converter commit
#               In --csv-dir (default data/processed/nerl-gnd/):
#                 gnd_unresolved_creators_<YYYYMMDD>.csv: creator IDs per role, with a person or a
#                 corporate record, with names, without any record, and how many unresolved ones are
#                 a dnb:deprecatedUri of a person or corporate body.
#               Exit codes: 0 success, 1 failure, 2 usage error, 130 interrupted.
# Dependencies: ijson >= 3 (yajl2_c backend for speed), pyarrow; werk_link_rules.py (rapidfuzz).
# Assumptions:  Only nodes whose @id starts
#               with https://d-nb.info/gnd/ are read; the reified blank nodes repeat their
#               relations. Names are kept as in the dump (NFD included); normalisation is the
#               linker's job. Unresolved creators are counted, never resolved.
#               A pass cannot resume mid-file: an interrupted pass restarts from its beginning.
#               Memory: the creator ID set of pass 1 is held in memory; it is bounded by the
#               number of GND creators, not by the size of the person dump.
#               Pass 2 reads the person dump, then the corporate body dump, into one table.

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
from collections.abc import Iterator
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import ijson  # type: ignore[import-untyped]
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
from werk_link_rules import CREATOR_PREDICATES, GNDO, die, gnd_id, log_peak_memory, write_atomic

logger = logging.getLogger("gnd_jsonld_to_parquet")

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WERK = ROOT / "data/gnd/authorities-gnd-werk_lds-20261013.jsonld"
DEFAULT_PERSON = ROOT / "data/gnd/authorities-gnd-person_lds-20261013.jsonld"
DEFAULT_KOERPERSCHAFT = ROOT / "data/gnd/authorities-gnd-koerperschaft_lds-20261013.jsonld"
DEFAULT_WERK_URL = "https://data.dnb.de/opendata/authorities-gnd-werk_lds.jsonld.gz"
DEFAULT_PERSON_URL = "https://data.dnb.de/opendata/authorities-gnd-person_lds.jsonld.gz"
DEFAULT_KOERPERSCHAFT_URL = "https://data.dnb.de/opendata/authorities-gnd-koerperschaft_lds.jsonld.gz"
DEFAULT_OUT_DIR = ROOT / "data/gnd/parquet"
DEFAULT_CSV_DIR = ROOT / "data/processed/nerl-gnd"
CODE_FILES = ("scripts/py/gnd_jsonld_to_parquet.py", "scripts/py/werk_link_rules.py")

GND_PREFIX = "https://d-nb.info/gnd/"
DNB = "https://d-nb.info/standards/elementset/dnb#"
DEPRECATED_URI = f"{DNB}deprecatedUri"
WORK_NAMES = {"preferred": f"{GNDO}preferredNameForTheWork", "variant": f"{GNDO}variantNameForTheWork"}
PERSON_NAMES = {"preferred": f"{GNDO}preferredNameForThePerson", "variant": f"{GNDO}variantNameForThePerson"}
CORPORATE_NAMES = {
    "preferred": f"{GNDO}preferredNameForTheCorporateBody",
    "variant": f"{GNDO}variantNameForTheCorporateBody",
}
# (entity, dump argument) in the order pass 2 reads them.
AGENT_DUMPS = (("person", "person", PERSON_NAMES), ("corporate", "koerperschaft", CORPORATE_NAMES))

NAME_SCHEMA = pa.schema([("gnd_id", pa.string()), ("kind", pa.string()), ("name", pa.string())])
CLASS_SCHEMA = pa.schema([("gnd_id", pa.string()), ("class", pa.string())])
CREATOR_SCHEMA = pa.schema([("gnd_id", pa.string()), ("role", pa.string()), ("creator_id", pa.string())])
CREATOR_NAME_SCHEMA = pa.schema(
    [("gnd_id", pa.string()), ("entity", pa.string()), ("kind", pa.string()), ("name", pa.string())]
)
WORK_TABLES = {
    "names": ("gnd_work_names.parquet", NAME_SCHEMA),
    "classes": ("gnd_work_classes.parquet", CLASS_SCHEMA),
    "creators": ("gnd_work_creators.parquet", CREATOR_SCHEMA),
}
CREATOR_NAMES_TABLE = "gnd_creator_names.parquet"
CSV_COLUMNS = [
    "role", "creator_ids", "with_person_record", "with_corporate_record", "with_names", "unresolved",
    "unresolved_as_deprecated_uri",
]  # fmt: skip

DATE_RE = re.compile(r"-(\d{8})\.jsonld$")
BATCH_ROWS = 100_000
PROGRESS_BYTES = 1024**3
DRY_RUN_NODES = 5
MIN_FREE_BYTES = 2 * 1024**3
DEFAULT_MAX_MEMORY_MB = 1024


# --- Reading nodes ---


def literals(node: dict[str, Any], key: str) -> list[str]:
    return [v["@value"] for v in node.get(key) or [] if isinstance(v, dict) and "@value" in v]


def references(node: dict[str, Any], key: str) -> list[str]:
    return [v["@id"] for v in node.get(key) or [] if isinstance(v, dict) and "@id" in v]


def node_id(node: dict[str, Any]) -> str | None:
    """The bare GND ID of a d-nb.info/gnd/ node; None for blank nodes."""
    uri = node.get("@id") or ""
    return gnd_id(uri) if uri.startswith(GND_PREFIX) else None


def work_rows(node: dict[str, Any]) -> tuple[list[tuple[str, str, str]], list[tuple[str, str]], list[tuple[str, str, str]]]:
    """Name, class and creator rows of one Work node; empty for blank nodes."""
    work = node_id(node)
    if work is None:
        return [], [], []
    names = [(work, kind, name) for kind, key in WORK_NAMES.items() for name in literals(node, key)]
    classes = [(work, uri[len(GNDO) :]) for uri in node.get("@type") or [] if uri.startswith(GNDO)]
    creators = []
    for role in CREATOR_PREDICATES:
        for uri in references(node, f"{GNDO}{role}"):
            person = gnd_id(uri)
            if person:
                creators.append((work, role, person))
    return names, classes, creators


def iter_nodes(path: Path, limit: int | None = None) -> Iterator[dict[str, Any]]:
    """Stream the node objects of a dump, logging progress every GB read."""
    size = path.stat().st_size
    next_report = PROGRESS_BYTES
    with open(path, "rb") as f:
        for count, node in enumerate(ijson.items(f, "item.item"), start=1):
            yield node
            if limit and count >= limit:
                return
            if f.tell() >= next_report:
                logger.info("%s: %.1f of %.1f GB read, %d nodes", path.name, f.tell() / 1024**3, size / 1024**3, count)
                next_report += PROGRESS_BYTES


# --- Writing tables ---


class TableWriter:
    """Parquet rows written in batches to <name>.tmp, renamed to <name> on close."""

    def __init__(self, path: Path, schema: pa.Schema) -> None:
        self.path = path
        self.temp = path.with_name(path.name + ".tmp")
        self.schema = schema
        self.writer = pq.ParquetWriter(self.temp, schema)
        self.batch: list[tuple[str, ...]] = []
        self.rows = 0

    def add(self, rows: list[Any]) -> None:
        self.batch.extend(rows)
        if len(self.batch) >= BATCH_ROWS:
            self.flush()

    def flush(self) -> None:
        if self.batch:
            columns = list(zip(*self.batch))
            self.writer.write_table(pa.table(columns, schema=self.schema))
            self.rows += len(self.batch)
            self.batch = []

    def close(self) -> None:
        self.flush()
        self.writer.close()
        os.replace(self.temp, self.path)

    def abort(self) -> None:
        self.writer.close()
        self.temp.unlink(missing_ok=True)


# --- Run state ---


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def git_state() -> tuple[str, bool]:
    """The gemea commit, and whether the converter or its imported module has uncommitted changes."""
    try:
        commit = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, check=True)
        status = subprocess.run(
            ["git", "-C", str(ROOT), "status", "--porcelain", "--", *CODE_FILES], capture_output=True, text=True, check=True
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown", True
    return commit.stdout.strip(), bool(status.stdout.strip())


def dump_date(path: Path) -> str:
    match = DATE_RE.search(path.name)
    if not match:
        die(f"{path.name} has no date in its name (…-YYYYMMDD.jsonld); rename it to the dump date")
    day = match.group(1)
    return f"{day[:4]}-{day[4:6]}-{day[6:]}"


def dump_entry(path: Path, url: str, with_sha: bool) -> dict[str, Any]:
    """What the manifest records of a dump; the SHA-256 is skipped for --limit trials."""
    stat = path.stat()
    return {
        "path": str(path),
        "url": url,
        "date": dump_date(path),
        "size": stat.st_size,
        "mtime": stat.st_mtime,
        "sha256": sha256(path) if with_sha else None,
    }


def same_dump(recorded: dict[str, Any] | None, path: Path) -> bool:
    if not recorded:
        return False
    stat = path.stat()
    return bool(recorded["path"] == str(path) and recorded["size"] == stat.st_size and recorded["mtime"] == stat.st_mtime)


def load_manifest(out_dir: Path) -> dict[str, Any]:
    path = out_dir / "manifest.json"
    if not path.exists():
        return {"dumps": {}, "tables": {}, "passes": {}}
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def save_manifest(out_dir: Path, manifest: dict[str, Any]) -> None:
    write_atomic(out_dir / "manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")


def table_entry(path: Path, rows: int) -> dict[str, Any]:
    return {"rows": rows, "sha256": sha256(path)}


def works_done(manifest: dict[str, Any], args: argparse.Namespace) -> bool:
    return (
        "works" in manifest["passes"]
        and same_dump(manifest["dumps"].get("werk"), args.werk)
        and all((args.out_dir / name).exists() for name, _ in WORK_TABLES.values())
    )


def creators_done(manifest: dict[str, Any], args: argparse.Namespace) -> bool:
    """Pass 2 is done if its dumps are unchanged and it read the creators table that pass 1 wrote last."""
    entry = manifest["passes"].get("creators")
    creators = manifest["tables"].get(WORK_TABLES["creators"][0])
    return bool(
        entry
        and creators
        and entry["creators_sha256"] == creators["sha256"]
        and all(same_dump(manifest["dumps"].get(key), getattr(args, key)) for _, key, _ in AGENT_DUMPS)
        and (args.out_dir / CREATOR_NAMES_TABLE).exists()
    )


# --- Pass 1: Works ---


def convert_works(args: argparse.Namespace, manifest: dict[str, Any]) -> None:
    writers = {key: TableWriter(args.out_dir / name, schema) for key, (name, schema) in WORK_TABLES.items()}
    nodes = works = 0
    try:
        for node in iter_nodes(args.werk, args.limit):
            nodes += 1
            names, classes, creators = work_rows(node)
            if names or classes or creators:
                works += 1
            writers["names"].add(names)
            writers["classes"].add(classes)
            writers["creators"].add(creators)
        for writer in writers.values():
            writer.close()
    except BaseException:
        for writer in writers.values():
            writer.abort()
        raise
    for writer in writers.values():
        manifest["tables"][writer.path.name] = table_entry(writer.path, writer.rows)
    manifest["dumps"]["werk"] = dump_entry(args.werk, args.werk_url, with_sha=args.limit is None)
    manifest["passes"]["works"] = {"finished_at": now(), "nodes": nodes, "works": works}
    manifest["passes"].pop("creators", None)
    save_manifest(args.out_dir, manifest)
    logger.info("Pass 1: %d nodes, %d Works; rows %s", nodes, works, {w.path.name: w.rows for w in writers.values()})


# --- Pass 2: creators ---


def load_creator_roles(path: Path) -> dict[str, set[str]]:
    """Creator ID -> the roles it holds in any Work."""
    roles: dict[str, set[str]] = {}
    for batch in pq.ParquetFile(path).iter_batches(columns=["role", "creator_id"]):
        for role, creator in zip(batch.column("role").to_pylist(), batch.column("creator_id").to_pylist()):
            roles.setdefault(creator, set()).add(role)
    return roles


def agent_rows(
    node: dict[str, Any], wanted: set[str], entity: str, name_keys: dict[str, str]
) -> tuple[list[tuple[str, str, str, str]], list[str]]:
    """Name rows of a wanted person or corporate body, and the wanted IDs this node lists as deprecated URIs."""
    agent = node_id(node)
    if agent is None:
        return [], []
    deprecated = [i for i in (gnd_id(uri) for uri in literals(node, DEPRECATED_URI)) if i in wanted]
    if agent not in wanted:
        return [], deprecated
    names = [(agent, entity, kind, name) for kind, key in name_keys.items() for name in literals(node, key)]
    return names, deprecated


def unresolved_counts(
    roles: dict[str, set[str]], found: dict[str, set[str]], named: set[str], deprecated: set[str]
) -> list[dict[str, Any]]:
    """One CSV row per role and one for all roles: creator IDs by the record found, with names, or unresolved.

    found maps an entity (person, corporate) to the creator IDs with a record in its dump.
    """
    role_names = [*CREATOR_PREDICATES, "all"]
    rows = {role: dict.fromkeys(CSV_COLUMNS[1:], 0) for role in role_names}
    for creator, creator_roles in roles.items():
        for role in [*creator_roles, "all"]:
            row = rows[role]
            row["creator_ids"] += 1
            row["with_person_record"] += creator in found["person"]
            row["with_corporate_record"] += creator in found["corporate"]
            row["with_names"] += creator in named
            if not any(creator in ids for ids in found.values()):
                row["unresolved"] += 1
                row["unresolved_as_deprecated_uri"] += creator in deprecated
    return [{"role": role, **rows[role]} for role in role_names]


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    temp = path.with_name(path.name + ".tmp")
    try:
        with open(temp, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def convert_creators(args: argparse.Namespace, manifest: dict[str, Any]) -> None:
    """Names of the creators of pass 1, from the person dump and then the corporate body dump."""
    creators_name = WORK_TABLES["creators"][0]
    roles = load_creator_roles(args.out_dir / creators_name)
    wanted = set(roles)
    logger.info("Pass 2: looking for %d creator IDs", len(wanted))
    found: dict[str, set[str]] = {entity: set() for entity, _, _ in AGENT_DUMPS}
    named: set[str] = set()
    deprecated: set[str] = set()
    nodes: dict[str, int] = {}
    writer = TableWriter(args.out_dir / CREATOR_NAMES_TABLE, CREATOR_NAME_SCHEMA)
    try:
        for entity, key, name_keys in AGENT_DUMPS:
            nodes[key] = 0
            for node in iter_nodes(getattr(args, key), args.limit):
                nodes[key] += 1
                agent = node_id(node)
                if agent is not None and agent in wanted:
                    found[entity].add(agent)
                names, old_ids = agent_rows(node, wanted, entity, name_keys)
                deprecated.update(old_ids)
                named.update(row[0] for row in names)
                writer.add(names)
        writer.close()
    except BaseException:
        writer.abort()
        raise
    counts = unresolved_counts(roles, found, named, deprecated)
    write_csv(args.unresolved_csv, counts)
    manifest["tables"][CREATOR_NAMES_TABLE] = table_entry(writer.path, writer.rows)
    for _, key, _ in AGENT_DUMPS:
        manifest["dumps"][key] = dump_entry(getattr(args, key), getattr(args, f"{key}_url"), with_sha=args.limit is None)
    manifest["passes"]["creators"] = {
        "finished_at": now(),
        "nodes": nodes,
        "creators_sha256": manifest["tables"][creators_name]["sha256"],
        "unresolved_csv": str(args.unresolved_csv),
    }
    save_manifest(args.out_dir, manifest)
    total = counts[-1]
    logger.info(
        "Pass 2: %s nodes, %d name rows; of %d creator IDs %d persons, %d corporate bodies, %d with names, "
        "%d unresolved (%d of them as deprecatedUri) -> %s",
        nodes, writer.rows, total["creator_ids"], total["with_person_record"], total["with_corporate_record"],
        total["with_names"], total["unresolved"], total["unresolved_as_deprecated_uri"], args.unresolved_csv,
    )  # fmt: skip


# --- Pre-flight and main ---


def now() -> str:
    return datetime.now(timezone.utc).isoformat()  # noqa: UP017 (3.9-compatible)


def check_dump(path: Path) -> None:
    """The dump exists, is named with its date, and ends with the closing brackets of a complete download."""
    if not path.is_file():
        die(f"{path} not found; download and unpack the GND dump, or pass its path")
    dump_date(path)
    with open(path, "rb") as f:
        f.seek(max(0, path.stat().st_size - 64))
        tail = f.read().decode("utf-8", errors="replace")
    if not re.search(r"\]\s*\]\s*$", tail):
        die(f"{path} does not end with '] ]'; the download is probably incomplete, download it again")


def preflight(args: argparse.Namespace) -> None:
    check_dump(args.werk)
    check_dump(args.person)
    check_dump(args.koerperschaft)
    if args.dry_run:
        return
    if args.limit and args.out_dir.resolve() == DEFAULT_OUT_DIR.resolve():
        die("--limit writes partial tables; pass another --out-dir for a trial run")
    for folder in (args.out_dir, args.unresolved_csv.parent):
        folder.mkdir(parents=True, exist_ok=True)
        if not os.access(folder, os.W_OK):
            die(f"{folder} is not writable; choose another folder")
    free = shutil.disk_usage(args.out_dir).free
    if free < MIN_FREE_BYTES:
        die(f"only {free // 1024**2} MB free in {args.out_dir}; free some disk first")


def check_overwrite(args: argparse.Namespace, manifest: dict[str, Any]) -> None:
    """Refuse to replace tables that this script's manifest does not record."""
    names = [name for name, _ in WORK_TABLES.values()] + [CREATOR_NAMES_TABLE]
    stray = [n for n in names if (args.out_dir / n).exists() and n not in manifest["tables"]]
    if stray:
        die(f"{', '.join(stray)} in {args.out_dir} are not from this script's run; move them, or pass --force")
    if args.unresolved_csv.exists() and "creators" not in manifest["passes"]:
        die(f"{args.unresolved_csv} exists; move it, or pass --force")


def gnd_nodes(path: Path, count: int) -> Iterator[dict[str, Any]]:
    """The first count d-nb.info/gnd/ nodes of a dump, past the blank nodes at its start."""
    found = 0
    for node in iter_nodes(path):
        if node_id(node) is not None:
            yield node
            found += 1
            if found >= count:
                return


def dry_run(args: argparse.Namespace) -> None:
    """Print the rows the first GND nodes of each dump would give; write nothing."""
    for node in gnd_nodes(args.werk, args.limit or DRY_RUN_NODES):
        names, classes, creators = work_rows(node)
        print(f"werk\t{node.get('@id')}\tnames={names}\tclasses={classes}\tcreators={creators}")
    for entity, key, name_keys in AGENT_DUMPS:
        for node in gnd_nodes(getattr(args, key), args.limit or DRY_RUN_NODES):
            print(f"{entity}\t{node.get('@id')}\tnames={literals(node, name_keys['preferred'])}"
                  f"\tdeprecated={literals(node, DEPRECATED_URI)}")  # fmt: skip


def handle_sigterm(_signum: int, _frame: object) -> None:
    raise KeyboardInterrupt


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert the GND Werk, person and corporate body dumps to Parquet.")
    parser.add_argument("--werk", type=Path, default=DEFAULT_WERK)
    parser.add_argument("--person", type=Path, default=DEFAULT_PERSON)
    parser.add_argument("--koerperschaft", type=Path, default=DEFAULT_KOERPERSCHAFT)
    parser.add_argument("--werk-url", default=DEFAULT_WERK_URL, help="download URL recorded in the manifest")
    parser.add_argument("--person-url", default=DEFAULT_PERSON_URL, help="download URL recorded in the manifest")
    parser.add_argument("--koerperschaft-url", default=DEFAULT_KOERPERSCHAFT_URL,
                        help="download URL recorded in the manifest")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--unresolved-csv", type=Path,
                        help="default: <csv dir>/gnd_unresolved_creators_<today>.csv, or in --out-dir with --limit")
    parser.add_argument("--limit", type=int, help="read only the first N nodes of each dump (trial run)")
    parser.add_argument("--max-memory-mb", type=int, default=DEFAULT_MAX_MEMORY_MB)
    parser.add_argument("--force", action="store_true", help="redo both passes and overwrite their outputs")
    parser.add_argument("--dry-run", action="store_true", help="print the rows of the first nodes; write nothing")
    args = parser.parse_args(argv)
    for name in ("limit", "max_memory_mb"):
        value = getattr(args, name)
        if value is not None and value < 1:
            parser.error(f"--{name.replace('_', '-')} must be at least 1")
    if args.unresolved_csv is None:
        csv_dir = args.out_dir if args.limit else DEFAULT_CSV_DIR
        args.unresolved_csv = csv_dir / f"gnd_unresolved_creators_{date.today():%Y%m%d}.csv"
    return args


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args(argv)
    preflight(args)
    if args.dry_run:
        dry_run(args)
        return 0

    manifest = {"dumps": {}, "tables": {}, "passes": {}} if args.force else load_manifest(args.out_dir)
    if not args.force:
        check_overwrite(args, manifest)
    commit, dirty = git_state()
    if dirty:
        logger.warning("Uncommitted changes in %s; the manifest records the commit as dirty", ", ".join(CODE_FILES))
    manifest["converter"] = {"script": CODE_FILES[0], "commit": commit, "dirty": dirty}
    manifest["limit"] = args.limit
    signal.signal(signal.SIGTERM, handle_sigterm)

    try:
        if works_done(manifest, args):
            logger.info("Pass 1 already done for %s; skipped", args.werk.name)
        else:
            convert_works(args, manifest)
        if creators_done(manifest, args):
            logger.info("Pass 2 already done for %s and %s; skipped", args.person.name, args.koerperschaft.name)
        else:
            convert_creators(args, manifest)
    except KeyboardInterrupt:
        logger.warning("Interrupted; no partial table was kept. Rerun the same command to resume.")
        return 130
    finally:
        log_peak_memory(args.max_memory_mb)
    logger.info("Done: %s", args.out_dir / "manifest.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
