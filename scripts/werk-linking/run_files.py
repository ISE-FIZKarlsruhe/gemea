# Purpose:      Read and check the files of one Werk linking run for the RDF writer:
#               the facts the triples need, and the gaps. No RDF here.
# Usage:        not run itself; imported by write_werk_rdf.py.
# Inputs:       <run-dir>/provcred_record.json (collect_run_provenance.py record),
#               <run-dir>/werk_links_merge.json, <run-dir>/werk_spans/run-shard0.json if present,
#               and scripts/py/werk_link_rules.py at the run's step 2 commit, read through git.
# Outputs:      load_run() returns (RunFacts, gaps); it stops the program on a missing run ID,
#               commit or links checksum.
# Dependencies: standard library; git on PATH for MIN_TERM_LENGTH.
# Assumptions:  The record's items have the shape {"value", "source", "status"}. Times are ISO 8601
#               with an offset; they are written in UTC, cut to the second. A run that reused Flair
#               (flair_from) is refused until its provenance is modelled.

from __future__ import annotations

import hashlib
import json
import logging
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NoReturn

RUN_ID_RE = re.compile(r"\d{8}T\d{6}Z")
COMMIT_RE = re.compile(r"[0-9a-f]{40}")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
MIN_TERM_LENGTH_RE = re.compile(r"^MIN_TERM_LENGTH\s*=\s*(\d+)\s*$", re.MULTILINE)
RULES_FILE = "scripts/py/werk_link_rules.py"

logger = logging.getLogger("write_werk_rdf")


@dataclass(frozen=True)
class RunFacts:
    """What the triples of one run are filled with; None marks a value the run's files lack."""

    run_id: str
    linker_start: str | None
    linker_end: str | None
    step1_id: str
    step1_start: str | None
    step1_end: str | None
    step1_commit: str
    step2_commit: str
    links_sha256: str
    linked_rows: int
    input_name: str | None
    input_sha256: str | None
    rule_sha256: str | None
    flair_version: str | None
    model_revision: str | None
    model_download_date: str | None
    tables_sha256: str | None
    dump_date: str | None
    decision: str | None
    name_threshold: float | None
    search_size: int | None
    min_score: float | None
    min_term_length: int | None


def die(message: str) -> NoReturn:
    logger.error(message)
    sys.exit(1)


def read_json(path: Path) -> Any:
    if not path.is_file():
        die(f"{path} not found; the writer needs the run folder of collect_run_provenance.py record")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def found(record: dict[str, Any], key: str) -> Any:
    """The value of a record item, or None when the collector marked it missing."""
    entry = record.get(key)
    if not isinstance(entry, dict) or entry.get("status") != "found":
        return None
    return entry.get("value")


def utc_seconds(stamp: str) -> str:
    """An ISO 8601 time with offset as xsd:dateTime in UTC, cut to the second."""
    moment = datetime.fromisoformat(stamp).astimezone(timezone.utc)  # noqa: UP017 (the Mac venv runs Python 3.9)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def span_of_times(parts: dict[str, dict[str, Any]] | None) -> tuple[str | None, str | None]:
    """Earliest start and latest end over the parts or shards of one step."""
    if not parts:
        return None, None
    starts = [p["started_at"] for p in parts.values() if p.get("started_at")]
    ends = [p["ended_at"] for p in parts.values() if p.get("ended_at")]
    start = utc_seconds(min(starts, key=datetime.fromisoformat)) if starts else None
    end = utc_seconds(max(ends, key=datetime.fromisoformat)) if ends else None
    return start, end


def tables_sha256(tables: dict[str, str]) -> str:
    """SHA-256 of one line '<table file> <sha256>' per table, sorted by file name."""
    text = "".join(f"{name} {tables[name]}\n" for name in sorted(tables))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def min_term_length(repo: Path, commit: str) -> int | None:
    """MIN_TERM_LENGTH of werk_link_rules.py at the run's commit; None if git cannot show it."""
    try:
        shown = subprocess.run(
            ["git", "-C", str(repo), "show", f"{commit}:{RULES_FILE}"], capture_output=True, text=True, check=True
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    match = MIN_TERM_LENGTH_RE.search(shown.stdout)
    return int(match.group(1)) if match else None


def model_cache(caches: list[dict[str, Any]] | None) -> tuple[str | None, str | None]:
    """Revision and download date of the one model cache copy that has a revision."""
    with_revision = [c for c in caches or [] if c.get("revision")]
    revisions = {c["revision"] for c in with_revision}
    if len(revisions) > 1:
        die(f"the record names {len(revisions)} model revisions ({', '.join(sorted(revisions))}); one run uses one model")
    if not with_revision:
        return None, None
    return with_revision[0]["revision"], with_revision[0].get("download_date")


def required(value: Any, pattern: re.Pattern[str], what: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        die(f"{what} is missing or malformed ({value!r}); the writer stops without it")
    return value


def load_run(run_dir: Path, repo: Path) -> tuple[RunFacts, list[str]]:
    """The facts of one run and the list of values its files lack."""
    record = read_json(run_dir / "provcred_record.json")
    merge = read_json(run_dir / "werk_links_merge.json")
    shard0 = run_dir / "werk_spans" / "run-shard0.json"
    if shard0.is_file() and read_json(shard0).get("flair_from"):
        die(f"{shard0} has flair_from: a run that reused Flair is not written yet")

    run_id = required(merge.get("run_id") or found(record, "a_linker_run_id"), RUN_ID_RE, "the linker run ID")
    step1_ids = found(record, "d_step1_id_candidates") or {}
    step1_id = required(
        step1_ids.get("run_id_json") or step1_ids.get("utc_start_of_earlier_shard"), RUN_ID_RE, "the step 1 run ID"
    )
    commits = found(record, "commits") or {}
    step1_commit = required(commits.get("step1"), COMMIT_RE, "the step 1 commit")
    step2_commit = required(commits.get("step2"), COMMIT_RE, "the step 2 commit")
    output = merge.get("output") or {}
    links_sha256 = required(output.get("sha256"), SHA256_RE, "the SHA-256 of werk_links.csv in werk_links_merge.json")
    linked_rows = (output.get("statuses") or {}).get("linked")
    if not isinstance(linked_rows, int):
        die("werk_links_merge.json has no output.statuses.linked; rerun merge_link_parts.py")

    linker_times = found(record, "b_linker_times") or {}
    linker_start, linker_end = span_of_times(linker_times.get("parts"))
    step1_start, step1_end = span_of_times(found(record, "c_step1_times"))
    source = found(record, "e_input_sha256") or {}
    revision, download_date = model_cache(found(record, "f_model_revision"))
    gnd = merge.get("gnd") or {}
    dump_dates = {d.get("date") for d in (gnd.get("dumps") or {}).values()}
    settings = merge.get("settings") or {}

    facts = RunFacts(
        run_id=run_id,
        linker_start=linker_start,
        linker_end=linker_end,
        step1_id=step1_id,
        step1_start=step1_start,
        step1_end=step1_end,
        step1_commit=step1_commit,
        step2_commit=step2_commit,
        links_sha256=links_sha256,
        linked_rows=linked_rows,
        input_name=Path(source["path"]).stem if source.get("path") else None,
        input_sha256=source.get("sha256"),
        rule_sha256=(found(record, "h_rule_script") or {}).get("rule_sha256"),
        flair_version=(found(record, "g_flair_torch_versions") or {}).get("flair"),
        model_revision=revision,
        model_download_date=download_date,
        tables_sha256=tables_sha256(gnd["tables"]) if gnd.get("tables") else None,
        dump_date=dump_dates.pop() if len(dump_dates) == 1 else None,
        decision=settings.get("decision"),
        name_threshold=settings.get("name_threshold"),
        search_size=settings.get("search_size"),
        min_score=settings.get("min_score"),
        min_term_length=min_term_length(repo, step2_commit),
    )
    gaps = [name for name, value in vars(facts).items() if value is None]
    return facts, gaps
