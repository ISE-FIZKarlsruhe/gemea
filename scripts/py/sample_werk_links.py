#!/usr/bin/env python3
# Purpose:      Draw seeded random samples of werk_links.csv rows per status and write
#               them, with the DDB record's full title, authors and link and the GND Work's name,
#               creators and link, into one labelling workbook, one sheet per status.
# Usage:        .venv/bin/python scripts/py/sample_werk_links.py \
#                   [--sample linked=100 --sample author_conflict=50 --sample below_threshold=50] \
#                   [--seed 20261108] [--links data/processed/nerl-gnd/werk_links.csv] \
#                   [--meta data/out/s2/s2_meta_de_content.parquet] [--gnd-dir data/gnd/parquet] \
#                   [--output data/annotation/werk_links_precision_sample_<date>.xlsx] [--force] [--dry-run]
# Inputs:       --links  link_gnd_local.py output (link_gnd_local.OUTPUT_COLUMNS), read row by row.
#               --meta   DDB metadata parquet: obj_id, title, dc_creator, dc_contributor, dc_date, read in
#                        batches, keeping only the sampled records.
#               --gnd-dir gnd_work_names, gnd_work_creators and gnd_creator_names parquet tables.
# Outputs:      --output an .xlsx workbook: one sheet per --sample status (rows in sample order, with
#                   clickable DDB and GND links and a label drop-down), a "labels" sheet with the label
#                   meanings, and a "run" sheet with seed, sizes, population counts, the links CSV's
#                   SHA-256 and the script's commit.
#               Exit codes: 0 success, 1 failure, 2 usage error.
# Dependencies: openpyxl 3.1, pyarrow; link_gnd_local.py in the same folder.
# Assumptions:  Reservoir sampling with random.Random(seed) over the CSV in file order, so the same
#               file, seed and sizes give the same sample. Rows without a top Work (no gnd_uri) get
#               empty GND columns; gnd_search_url is a lobid-gnd search for the first search term,
#               restricted to Works, as a lookup aid only: it is not the linker's candidate list.
#               below_threshold and no_candidate sheets get a span_ok drop-down (y, n, na); no_candidate
#               also gets title_search_url, a lobid-gnd Work search on the DDB title up to its first
#               ISBD separator (" : ", " / ", " = "), since the linker found no candidate to compare.

from __future__ import annotations

import argparse
import csv
import logging
import os
import random
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import pyarrow.parquet as pq  # type: ignore[import-untyped]
from link_gnd_local import OUTPUT_COLUMNS, git_state, sha256
from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.worksheet.datavalidation import DataValidation
from werk_link_rules import die

logger = logging.getLogger("sample_werk_links")

DEFAULT_LINKS = Path("data/processed/nerl-gnd/werk_links.csv")
DEFAULT_META = Path("data/out/s2/s2_meta_de_content.parquet")
DEFAULT_GND_DIR = Path("data/gnd/parquet")
DEFAULT_SAMPLE = ("linked=100", "author_conflict=50", "below_threshold=50")
DEFAULT_SEED = 20261108
STATUSES = (
    "linked",
    "author_conflict",
    "author_unconfirmed",
    "below_threshold",
    "generic_title",
    "ambiguous",
    "no_candidate",
    "no_title",
)
DDB_ITEM = "https://www.deutsche-digitale-bibliothek.de/item/"
GND_PREFIX = "https://d-nb.info/gnd/"
LOBID_SEARCH = "https://lobid.org/gnd/search?"
META_COLUMNS = ["obj_id", "title", "dc_creator", "dc_contributor", "dc_date"]
BATCH_ROWS = 100_000

SHEET_COLUMNS = [
    ("obj_id", 36), ("ddb_url", 18), ("title", 60), ("dc_creator", 30), ("dc_contributor", 30),
    ("dc_date", 10), ("type_group", 12), ("search_terms", 30), ("matched_span", 30),
    ("matched_span_source", 16), ("matched_name", 30), ("score", 7), ("author_match", 12),
    ("author_source", 14), ("candidate_count", 10), ("truncated", 9), ("gnd_url", 18),
    ("gnd_preferred_name", 30), ("gnd_creators", 40), ("gnd_search_url", 18), ("label", 18), ("note", 40),
]  # fmt: skip
# Extra columns per sheet, inserted before "label".
EXTRA_COLUMNS: dict[str, list[tuple[str, int]]] = {
    "below_threshold": [("span_ok", 8)],
    "no_candidate": [("title_search_url", 18), ("span_ok", 8)],
}
SPAN_OK_VALUES = ("y", "n", "na")
LINK_COLUMNS = ("ddb_url", "gnd_url", "gnd_search_url", "title_search_url")
# ISBD separators after the title proper: other title information, statement of responsibility, parallel title.
TITLE_PROPER_ENDS = (" : ", " / ", " = ")

# Labels per sheet; other statuses get GENERIC_LABELS.
LABELS: dict[str, list[tuple[str, str]]] = {
    "linked": [
        ("correct", "the GND Work is the record's Work"),
        ("wrong_work", "another Work with the same or a similar title"),
        ("related", "a version, part, collection or translation of the record's Work, not the Work itself"),
        ("not_a_work_title", "the matched span is not the title of a Work"),
        ("unsure", "cannot be decided from the record and GND"),
    ],
    "author_conflict": [
        ("correct_rejection", "the record's Work is not a GND Work with this title; no link is right"),
        ("author_role", "dc_creator names a translator, editor, publisher or other non-creator"),
        ("contributor_used", "dc_creator is empty and dc_contributor is not the creator"),
        ("name_form", "the creator is right, but the name form missed the threshold"),
        ("corporate_name", "a corporate creator whose name differs only slightly"),
        ("wrong_span", "the search term is not the title of a Work"),
        ("other", "another cause; say which in the note"),
        ("unsure", "cannot be decided from the record and GND"),
    ],
    "below_threshold": [
        ("not_in_gnd", "the record's Work is not in GND; no link is right"),
        ("subtitle_or_volume", "the span carries a subtitle, volume or part beyond the Work name"),
        ("spelling_variant", "a historical spelling or OCR form the rules do not cover"),
        ("gnd_name_qualifier", "the GND name carries a qualifier, such as a year or form"),
        ("wrong_span", "the search term is not the title of a Work"),
        ("other", "another cause; say which in the note"),
        ("unsure", "cannot be decided from the record and GND"),
    ],
    # Rule (a): not_in_gnd wins, the other labels count recoverable links only.
    "no_candidate": [
        ("not_in_gnd", "the record's Work is not in GND; no link is right"),
        ("no_span", "step 1 gave no search term, and the Work is in GND"),
        ("wrong_span", "the search term is not the title of a Work, and the Work is in GND"),
        ("subtitle_or_volume", "the span carries extra words that no Work name holds"),
        ("spelling_variant", "a historical spelling or OCR form; the Work is in GND under another form"),
        ("tokenisation", "a token split wrongly, such as Beethoven's to s or Imp. to imp"),
        ("uniform_title", "the transcribed title differs from the GND uniform title"),
        ("other", "another cause; say which in the note"),
        ("unsure", "cannot be decided from the record and GND"),
    ],
}
GENERIC_LABELS = [
    ("right_outcome", "the status is right for this record"),
    ("wrong_outcome", "the record should have had another status; say which in the note"),
    ("unsure", "cannot be decided from the record and GND"),
]


# --- Sampling ---


def parse_sample(values: list[str]) -> dict[str, int]:
    """status=n pairs, in the order given; raises ValueError on a bad pair."""
    sizes: dict[str, int] = {}
    for value in values:
        status, _, size = value.partition("=")
        if status not in STATUSES or not size.isdigit() or int(size) < 1:
            raise ValueError(f"--sample {value}: expected <status>=<n> with a status of {', '.join(STATUSES)}")
        sizes[status] = int(size)
    return sizes


def draw(path: Path, sizes: dict[str, int], seed: int) -> tuple[dict[str, list[dict[str, str]]], Counter[str]]:
    """Reservoir samples per status, and each status's row count in the file."""
    rng = random.Random(seed)
    samples: dict[str, list[dict[str, str]]] = {status: [] for status in sizes}
    seen: Counter[str] = Counter()
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != OUTPUT_COLUMNS:
            die(f"{path} has columns {reader.fieldnames}, not those of link_gnd_local.py")
        for row in reader:
            status = row["status"]
            seen[status] += 1
            if status not in sizes:
                continue
            reservoir = samples[status]
            if len(reservoir) < sizes[status]:
                reservoir.append(row)
            else:
                slot = rng.randrange(seen[status])
                if slot < sizes[status]:
                    reservoir[slot] = row
    return samples, seen


# --- Enrichment ---


def agents(values: list[dict[str, Any]] | None) -> str:
    """'label <uri>' per agent, joined by '; '."""
    parts = []
    for agent in values or []:
        label, uri = agent.get("label") or "", agent.get("uri") or ""
        parts.append(f"{label} <{uri}>" if uri else label)
    return "; ".join(parts)


def load_meta(path: Path, wanted: set[str]) -> dict[str, dict[str, str]]:
    """Title, authors and date of the wanted records, read batch by batch."""
    found: dict[str, dict[str, str]] = {}
    for batch in pq.ParquetFile(path).iter_batches(batch_size=BATCH_ROWS, columns=META_COLUMNS):
        for record in batch.to_pylist():
            if record["obj_id"] in wanted:
                found[record["obj_id"]] = {
                    "title": record["title"] or "",
                    "dc_creator": agents(record["dc_creator"]),
                    "dc_contributor": agents(record["dc_contributor"]),
                    "dc_date": record["dc_date"] or "",
                }
    return found


def gnd_id(uri: str) -> str:
    return uri.removeprefix(GND_PREFIX) if uri.startswith(GND_PREFIX) else ""


def read_rows(path: Path, ids: set[str]) -> list[dict[str, Any]]:
    if not ids:
        return []
    return pq.read_table(path, filters=[("gnd_id", "in", sorted(ids))]).to_pylist()  # type: ignore[no-any-return]


def load_gnd(gnd_dir: Path, work_ids: set[str]) -> dict[str, dict[str, str]]:
    """Per Work: its preferred name, and its creators as 'role: preferred name (id)'."""
    names = {r["gnd_id"]: r["name"] for r in read_rows(gnd_dir / "gnd_work_names.parquet", work_ids)
             if r["kind"] == "preferred"}  # fmt: skip
    creators = read_rows(gnd_dir / "gnd_work_creators.parquet", work_ids)
    creator_ids = {r["creator_id"] for r in creators}
    creator_names = {r["gnd_id"]: r["name"] for r in read_rows(gnd_dir / "gnd_creator_names.parquet", creator_ids)
                     if r["kind"] == "preferred"}  # fmt: skip
    by_work: dict[str, list[str]] = defaultdict(list)
    for r in sorted(creators, key=lambda r: (r["gnd_id"], r["role"], r["creator_id"])):
        name = creator_names.get(r["creator_id"], "")
        by_work[r["gnd_id"]].append(f"{r['role']}: {name} ({r['creator_id']})")
    return {w: {"gnd_preferred_name": names.get(w, ""), "gnd_creators": "; ".join(by_work[w])} for w in work_ids}


def search_url(search_terms: str) -> str:
    term = search_terms.split(" | ")[0].strip()
    return LOBID_SEARCH + urlencode({"q": term, "filter": "type:Work"}) if term else ""


def title_proper(title: str) -> str:
    """The DDB title up to the first ISBD separator; the whole title if it has none."""
    cut = min((i for i in (title.find(end) for end in TITLE_PROPER_ENDS) if i >= 0), default=len(title))
    return title[:cut].strip()


def title_search_url(title: str) -> str:
    """A lobid-gnd Work search on the title proper, for rows where the linker found no candidate."""
    proper = title_proper(title)
    return LOBID_SEARCH + urlencode({"q": proper, "filter": "type:Work"}) if proper else ""


def sheet_columns(status: str) -> list[tuple[str, int]]:
    """SHEET_COLUMNS with the status's EXTRA_COLUMNS inserted before label."""
    at = [column for column, _ in SHEET_COLUMNS].index("label")
    return SHEET_COLUMNS[:at] + EXTRA_COLUMNS.get(status, []) + SHEET_COLUMNS[at:]


def sheet_row(row: dict[str, str], meta: dict[str, dict[str, str]], gnd: dict[str, dict[str, str]]) -> dict[str, Any]:
    work = gnd_id(row["gnd_uri"])
    out: dict[str, Any] = {column: row.get(column, "") for column, _ in SHEET_COLUMNS}
    out.update(meta.get(row["obj_id"], {}))
    out.update(gnd.get(work, {}))
    out["ddb_url"] = DDB_ITEM + row["obj_id"]
    out["gnd_url"] = row["gnd_uri"]
    out["gnd_search_url"] = search_url(row["search_terms"])
    out["title_search_url"] = title_search_url(out["title"])
    out["label"] = out["note"] = out["span_ok"] = ""
    return out


# --- Workbook ---


def add_dropdown(sheet: Any, columns: list[tuple[str, int]], column: str, values: list[str], rows: int) -> None:
    letter = sheet.cell(row=1, column=[c for c, _ in columns].index(column) + 1).column_letter
    validation = DataValidation(type="list", formula1=f'"{",".join(values)}"', allow_blank=True)
    validation.add(f"{letter}2:{letter}{rows + 1}")
    sheet.add_data_validation(validation)


def add_sheet(book: Workbook, status: str, rows: list[dict[str, Any]]) -> None:
    columns = sheet_columns(status)
    sheet = book.create_sheet(status)
    sheet.append([column for column, _ in columns])
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for row in rows:
        sheet.append([row[column] for column, _ in columns])
    for index, (column, width) in enumerate(columns, start=1):
        letter = sheet.cell(row=1, column=index).column_letter
        sheet.column_dimensions[letter].width = width
        if column in LINK_COLUMNS:
            for cell in sheet[letter][1:]:
                if cell.value:
                    cell.hyperlink = cell.value
                    cell.style = "Hyperlink"
    add_dropdown(sheet, columns, "label", [label for label, _ in LABELS.get(status, GENERIC_LABELS)], len(rows))
    if "span_ok" in dict(columns):
        add_dropdown(sheet, columns, "span_ok", list(SPAN_OK_VALUES), len(rows))
    sheet.freeze_panes = "B2"


def add_info(book: Workbook, sizes: dict[str, int], run: list[tuple[str, Any]]) -> None:
    labels = book.create_sheet("labels")
    labels.append(["sheet", "label", "meaning"])
    for status in sizes:
        for label, meaning in LABELS.get(status, GENERIC_LABELS):
            labels.append([status, label, meaning])
    info = book.create_sheet("run")
    for key, value in run:
        info.append([key, value])
    for sheet in (labels, info):
        for cell in sheet[1]:
            cell.font = Font(bold=True)
        sheet.column_dimensions["A"].width = 22
        sheet.column_dimensions["B"].width = 24
        sheet.column_dimensions["C"].width = 90


def save(book: Workbook, output: Path) -> None:
    temp = output.with_name(output.name + ".tmp")
    try:
        book.save(temp)
        os.replace(temp, output)
    finally:
        temp.unlink(missing_ok=True)


# --- Pre-flight and main ---


def preflight(args: argparse.Namespace) -> None:
    for flag, path in (("--links", args.links), ("--meta", args.meta)):
        if not path.is_file():
            die(f"{flag} {path} not found; pass its path")
    tables = [args.gnd_dir / f"{t}.parquet" for t in ("gnd_work_names", "gnd_work_creators", "gnd_creator_names")]
    missing = [t.name for t in tables if not t.is_file()]
    if missing:
        die(f"{', '.join(missing)} not in {args.gnd_dir}; copy the GND tables or pass --gnd-dir")
    if args.dry_run:
        return
    folder = args.output.resolve().parent
    if not folder.is_dir() or not os.access(folder, os.W_OK):
        die(f"{folder} is missing or not writable; create it or choose another --output")
    if args.output.exists() and not args.force:
        die(f"{args.output} already exists; it may hold labels. Move it, or pass --force")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sample werk_links.csv rows per status into a labelling workbook.")
    parser.add_argument("--sample", action="append", metavar="STATUS=N", help=f"default: {' '.join(DEFAULT_SAMPLE)}")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--links", type=Path, default=DEFAULT_LINKS)
    parser.add_argument("--meta", type=Path, default=DEFAULT_META)
    parser.add_argument("--gnd-dir", type=Path, default=DEFAULT_GND_DIR)
    parser.add_argument("--output", type=Path, help="default: data/annotation/werk_links_precision_sample_<today>.xlsx")
    parser.add_argument("--force", action="store_true", help="overwrite --output, and any labels in it")
    parser.add_argument("--dry-run", action="store_true", help="check the inputs and print the plan; write nothing")
    args = parser.parse_args(argv)
    try:
        args.sizes = parse_sample(args.sample or list(DEFAULT_SAMPLE))
    except ValueError as error:
        parser.error(str(error))
    if args.output is None:
        args.output = Path(f"data/annotation/werk_links_precision_sample_{date.today():%Y%m%d}.xlsx")
    return args


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args(argv)
    preflight(args)
    if args.dry_run:
        print(f"would sample\t{args.sizes} with seed {args.seed} from {args.links}")
        print(f"would write\t{args.output}")
        return 0

    samples, population = draw(args.links, args.sizes, args.seed)
    for status, size in args.sizes.items():
        if len(samples[status]) < size:
            logger.warning("%s has only %d rows; all are in the sample", status, len(samples[status]))
    rows = [row for sample in samples.values() for row in sample]
    meta = load_meta(args.meta, {row["obj_id"] for row in rows})
    missing = sorted({row["obj_id"] for row in rows} - set(meta))
    if missing:
        logger.warning("%d sampled records not in %s, e.g. %s; their DDB columns stay empty",
                       len(missing), args.meta, missing[0])  # fmt: skip
    gnd = load_gnd(args.gnd_dir, {gnd_id(row["gnd_uri"]) for row in rows} - {""})

    commit, dirty = git_state()
    run: list[tuple[str, Any]] = [
        ("key", "value"),
        ("script", "scripts/py/sample_werk_links.py"),
        ("commit", commit),
        # git_state checks the linker's files only, so this script's own checksum is recorded too.
        ("uncommitted_changes_in_linker", dirty),
        ("script_sha256", sha256(Path(__file__))),
        ("decision", "simple random sample per status, seeded"),
        ("seed", args.seed),
        ("links", str(args.links.resolve())),
        ("links_sha256", sha256(args.links)),
        ("meta", str(args.meta.resolve())),
        ("gnd_dir", str(args.gnd_dir.resolve())),
        ("created", datetime.now(timezone.utc).isoformat()),  # noqa: UP017 (3.9-compatible)
    ]
    run += [(f"sample_{s}", f"{len(samples[s])} of {population[s]}") for s in args.sizes]

    book = Workbook()
    book.remove(book.worksheets[0])
    for status in args.sizes:
        add_sheet(book, status, [sheet_row(row, meta, gnd) for row in samples[status]])
    add_info(book, args.sizes, run)
    save(book, args.output)
    logger.info("Wrote %s: %s", args.output, ", ".join(f"{s} {len(samples[s])}" for s in args.sizes))
    return 0


if __name__ == "__main__":
    sys.exit(main())
