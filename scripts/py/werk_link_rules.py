#!/usr/bin/env python3
# Purpose:      The shared rules and helpers of Werk linking: title and name
#               normalisation, search terms, the author sources, the decision rules, the type
#               groups, the input and resume helpers, and the run identity. Imported by link_gnd_local.py,
#               link_gnd_bikidata.py, extract_werk_spans.py and the GND scripts; not run itself.
# Usage:        from werk_link_rules import normalize_title, search_term, decide_baseline, ...
# Inputs:       None.
# Outputs:      None.
# Dependencies: rapidfuzz >= 3.
# Assumptions:  Moved unchanged from link_gnd_bikidata.py (gemea 1224e2b) on 2026-11-08.
#               --min-score and --name-threshold defaults are starting values, not calibrated.

from __future__ import annotations

import itertools
import json
import logging
import os
import re
import resource
import shutil
import sys
import unicodedata
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NoReturn

from rapidfuzz.distance import JaroWinkler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("werk_link_rules")

GNDO = "https://d-nb.info/standards/elementset/gnd#"
# A Work's creator is any of these GND roles.
CREATOR_PREDICATES = (
    "firstAuthor", "author", "firstComposer", "composer", "firstArtist", "artist", "creator",
    "poet", "librettist", "accreditedAuthor", "accreditedComposer", "accreditedArtist",
)  # fmt: skip
# The GND classes searched per type group of step 1. The dump types each Work
# with its most specific class only, so the classes are listed, not inferred from Work.
GROUP_CLASSES = {
    "music": {"MusicalWork", "VersionOfAMusicalWork"},
    "libretto": {"MusicalWork", "Work"},
    "song": {"MusicalWork", "Work"},
    "manuscript": {"Manuscript", "CollectiveManuscript", "Work"},
    "multivolume": {"Work", "Collection"},
    "work": {"Work"},
}
# A truncated search that ends without a link gets its own status.
GENERIC_TITLE_STATUSES = {"no_candidate", "below_threshold", "author_unconfirmed", "ambiguous"}
GND_ID_RE = re.compile(r"^<?https?://d-nb\.info/gnd/([0-9A-Za-z-]+)>?$")

# Removed candidates never reach ranking; pass 1 names only move a candidate up or down.
AUTHOR_RANK = {"yes": 3, "pass1_yes": 2, "unknown": 1, "pass1_no": 0}
NAME_STOPWORDS = {
    "von", "v", "van", "der", "den", "dem", "de", "du", "zu", "zur", "zum", "herr", "herrn",
    "frau", "dr", "prof", "professor", "doktor", "graf", "freiherr",
}  # fmt: skip
UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue"})
# Historical spelling, after Jurish (2011), A.5: (pattern, replacement), applied in this order.
HISTORICAL_RULES = (
    (re.compile(r"th"), "t"),
    (re.compile(r"c(?![hk])"), "k"),
    (re.compile(r"y"), "i"),
    (re.compile(r"v(?=[bcdfghjklmnpqrstvwxz])"), "u"),
    (re.compile(r"ff"), "f"),
    (re.compile(r"tt"), "t"),
)

DEFAULT_MIN_SCORE = 0.6
DEFAULT_DECISION = "baseline"
DECISIONS = ("baseline", "score")
DEFAULT_NAME_THRESHOLD = 0.85
DEFAULT_SEARCH_SIZE = 500
DEFAULT_CHUNK_SIZE = 1000
MIN_TERM_LENGTH = 3
MAX_TIED_URIS = 5
DRY_RUN_RECORDS = 3
MIN_FREE_BYTES = 1024**3


@dataclass
class Work:
    uri: str
    names: list[str] = field(default_factory=list)
    # Author GND ID -> that person's names; empty if person records are not loaded.
    authors: dict[str, list[str]] = field(default_factory=dict)
    # GND classes, such as MusicalWork; empty if not fetched.
    classes: list[str] = field(default_factory=list)


@dataclass
class Candidate:
    uri: str
    name: str
    score: float
    author_match: str
    # The span equals one of the Work's names after normalisation.
    exact: bool = False


@dataclass
class TermResult:
    works: list[Work]
    truncated: bool


LookupFn = Callable[[str], TermResult]


def die(message: str) -> NoReturn:
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


# --- Text and name comparison ---


def normalize_tokens(text: str) -> list[str]:
    """Casefolded word tokens, with umlauts spelled out and historic th spelled t."""
    normalized = unicodedata.normalize("NFC", text).casefold().translate(UMLAUTS)
    return [token.replace("th", "t") for token in re.findall(r"\w+", normalized)]


def historical_spelling(token: str) -> str:
    """A casefolded token with the historical spelling rules (HISTORICAL_RULES) applied."""
    for pattern, replacement in HISTORICAL_RULES:
        token = pattern.sub(replacement, token)
    return token


def normalize_title(text: str) -> list[str]:
    """Title tokens for comparison: normalize_tokens, then the historical spelling rules."""
    return [historical_spelling(token) for token in normalize_tokens(text)]


def title_score(span: str, name: str) -> float:
    """Dice overlap of the normalized tokens of a title span and a Work name."""
    span_tokens = set(normalize_title(span))
    name_tokens = set(normalize_title(name))
    if not span_tokens or not name_tokens:
        return 0.0
    return 2 * len(span_tokens & name_tokens) / (len(span_tokens) + len(name_tokens))


def split_name(name: str) -> tuple[str | None, list[str]]:
    """Surname and given-name tokens, without particles and titulature."""
    surname_part, given_part = name.split(",", 1) if "," in name else (name, "")
    surname_tokens = [t for t in normalize_tokens(surname_part) if t not in NAME_STOPWORDS]
    given = [t for t in normalize_tokens(given_part) if t not in NAME_STOPWORDS]
    if not surname_tokens:
        return None, []
    return surname_tokens[-1], surname_tokens[:-1] + given


def token_similarity(left: str, right: str) -> float:
    """Jaro-Winkler similarity, with an initial matching a name that starts with it."""
    if (len(left) == 1 and right.startswith(left)) or (len(right) == 1 and left.startswith(right)):
        return 1.0
    return float(JaroWinkler.similarity(left, right))


def names_match(left: str, right: str, threshold: float) -> bool:
    """True if the surnames and each given name of the shorter name pass the threshold."""
    left_surname, left_given = split_name(left)
    right_surname, right_given = split_name(right)
    if left_surname is None or right_surname is None:
        return False
    if token_similarity(left_surname, right_surname) < threshold:
        return False
    shorter, longer = sorted((left_given, right_given), key=len)
    return all(max(token_similarity(token, other) for other in longer) >= threshold for token in shorter)


def any_name_matches(literals: Iterable[str], names: Iterable[str], threshold: float) -> bool:
    names = list(names)
    return any(names_match(literal, name, threshold) for literal in literals for name in names)


def gnd_id(uri: str) -> str | None:
    match = GND_ID_RE.match(uri.strip())
    return match.group(1) if match else None


def ddb_authors(record: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    """The record's DDB authors and their source: dc_creator, else dc_contributor."""
    for key, source in (("ddb_creators", "dc_creator"), ("ddb_contributors", "dc_contributor")):
        entries = [e for e in record.get(key) or [] if isinstance(e, dict) and (e.get("uri") or e.get("label"))]
        if entries:
            return entries, source
    return [], ""


def search_term(span: str) -> str | None:
    """Word tokens before the first ISBD or list delimiter, if long enough."""
    head = re.split(r"[,.:;/=]| - ", span, maxsplit=1)[0]
    tokens = re.findall(r"\w+", unicodedata.normalize("NFC", head).casefold())
    term = " ".join(tokens)
    return term if len(term) >= MIN_TERM_LENGTH else None


# --- Decision ---


def decide(candidates: list[Candidate], removed: int, min_score: float) -> tuple[str, Candidate | None, list[str]]:
    """Pick a status, the top candidate and the Works tied with it; author match ranks before title score."""
    if not candidates:
        return ("author_conflict" if removed else "no_candidate"), None, []
    ranked = sorted(candidates, key=lambda c: (AUTHOR_RANK[c.author_match], c.score, c.uri), reverse=True)
    above = [c for c in ranked if c.score >= min_score]
    if not above:
        return "below_threshold", max(ranked, key=lambda c: (c.score, c.uri)), []
    top = above[0]
    tied = sorted(c.uri for c in above if c.score == top.score and c.author_match == top.author_match)
    if len(tied) > 1:
        return "ambiguous", top, tied[:MAX_TIED_URIS]
    return "linked", top, []


def decide_baseline(candidates: list[Candidate], removed: int) -> tuple[str, Candidate | None, list[str]]:
    """Baseline: link only an exact title match with author_match yes, and only if one Work qualifies."""
    if not candidates:
        return ("author_conflict" if removed else "no_candidate"), None, []
    ranked = sorted(candidates, key=lambda c: (c.exact, AUTHOR_RANK[c.author_match], c.score, c.uri), reverse=True)
    qualifying = [c for c in ranked if c.exact and c.author_match == "yes"]
    if len(qualifying) == 1:
        return "linked", qualifying[0], []
    if qualifying:
        return "ambiguous", qualifying[0], sorted(c.uri for c in qualifying)[:MAX_TIED_URIS]
    return ("author_unconfirmed" if ranked[0].exact else "below_threshold"), ranked[0], []


def keep_classes(result: TermResult, allowed: set[str] | None) -> TermResult:
    """Only the Works of the GND classes a type group searches; all if no group."""
    if allowed is None:
        return result
    works = [w for w in result.works if allowed & set(w.classes)]
    return TermResult(works=works, truncated=result.truncated)


# --- Input ---


def read_records(input_path: Path) -> Iterator[dict[str, Any]]:
    """Stream records from the step 1 JSONL, failing on the first bad line."""
    with open(input_path, encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as e:
                die(f"{input_path} line {line_no} is not valid JSON ({e}); fix or regenerate the step 1 output")
            if not isinstance(record, dict) or not record.get("obj_id"):
                die(f"{input_path} line {line_no} has no obj_id; fix or regenerate the step 1 output")
            group = record.get("type_group")
            if group and group not in GROUP_CLASSES:
                die(
                    f"{input_path} line {line_no} has type_group {group!r}, which Werk linking does not link; "
                    "fix or regenerate the step 1 output"
                )
            yield record


def read_chunks(input_path: Path, chunk_size: int, limit: int | None) -> Iterator[list[dict[str, Any]]]:
    """Stream the records as lists of chunk_size; only one chunk is held in memory."""
    records = itertools.islice(read_records(input_path), limit)
    while True:
        chunk = list(itertools.islice(records, chunk_size))
        if not chunk:
            return
        yield chunk


# --- Pre-flight and resumable run state ---


def check_host(output_dir: Path, max_memory_mb: int) -> None:
    """Fail on too little disk; warn if the host is busy or short of memory."""
    free = shutil.disk_usage(output_dir).free
    if free < MIN_FREE_BYTES:
        die(f"only {free // 1024**2} MB free in {output_dir}; free some disk or choose another --output")
    load = os.getloadavg()[0]
    cores = os.cpu_count() or 1
    logger.info("Host: load %.1f on %d cores, %d GB free in %s", load, cores, free // 1024**3, output_dir)
    if load > cores / 2:
        logger.warning("Host is busy (load %.1f on %d cores); consider running later", load, cores)
    try:
        available_mb = os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") // 1024**2
    except (ValueError, OSError):
        return
    if available_mb < max_memory_mb:
        logger.warning("Only %d MB memory available, below the %d MB budget", available_mb, max_memory_mb)


def write_atomic(path: Path, text: str) -> None:
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(text, encoding="utf-8")
    os.replace(temp, path)


RUN_ID_FILE = "run-id.json"
RUN_ID_RE = re.compile(r"^\d{8}T\d{6}Z$")


def run_id_arg(text: str) -> str:
    """argparse type for --run-id: the run ID format, such as 20261110T090205Z."""
    import argparse

    if not RUN_ID_RE.match(text):
        raise argparse.ArgumentTypeError(f"{text!r} is not a run ID like 20261110T090205Z (UTC start)")
    return text


def run_identity(folder: Path, run_id: str | None) -> dict[str, str]:
    """The run ID and start of the run that owns folder, in run-id.json.

    Created once, by whichever process gets there first, then read by every other shard, part
    or resumed run; kept out of run.json so that the settings compared on resume stay equal.
    A given run_id must equal a recorded one.
    """
    path = folder / RUN_ID_FILE
    if not path.exists():
        now = datetime.now(timezone.utc)  # noqa: UP017 (3.9-compatible)
        identity = {"run_id": run_id or now.strftime("%Y%m%dT%H%M%SZ"), "started_at": now.isoformat()}
        temp = folder / f"{RUN_ID_FILE}.{os.getpid()}.tmp"
        temp.write_text(json.dumps(identity, indent=2), encoding="utf-8")
        try:
            os.link(temp, path)  # atomic, and fails if another process created it first
        except FileExistsError:
            pass
        finally:
            temp.unlink(missing_ok=True)
    recorded: dict[str, str] = json.loads(path.read_text(encoding="utf-8"))
    if run_id and recorded["run_id"] != run_id:
        die(f"{path} records run {recorded['run_id']}, not --run-id {run_id}; pass the same ID, or --restart")
    return recorded


def load_done_chunks(parts_dir: Path) -> set[int]:
    """Chunks recorded in the manifest whose part file exists. A line cut off by a crash is ignored."""
    manifest = parts_dir / "manifest.jsonl"
    done: set[int] = set()
    if not manifest.exists():
        return done
    with open(manifest, encoding="utf-8") as f:
        for line in f:
            try:
                chunk = json.loads(line)["chunk"]
            except (json.JSONDecodeError, KeyError):
                continue
            if (parts_dir / part_name(chunk)).exists():
                done.add(chunk)
    return done


def part_name(chunk: int) -> str:
    return f"part-{chunk:06d}.csv"


def log_peak_memory(max_memory_mb: int) -> None:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    peak_mb = peak // 1024**2 if sys.platform == "darwin" else peak // 1024
    logger.info("Peak memory: %d MB", peak_mb)
    if peak_mb > max_memory_mb:
        logger.warning("Peak memory is above the %d MB budget", max_memory_mb)
