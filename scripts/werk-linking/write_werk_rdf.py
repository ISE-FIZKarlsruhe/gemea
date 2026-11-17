#!/usr/bin/env python3
# Purpose:      Write the RDF of one finished Werk linking run:
#               the links and Works as N-Quads in
#               graph/work and in one path graph per span method, and the run's PROV-CRED
#               provenance as N-Quads in graph/prov. Read-only on the run's files.
# Usage:        .venv/bin/python scripts/werk-linking/write_werk_rdf.py --run-dir <run folder> \
#                   [--out-dir <folder>] [--counts <path_graph_counts csv>] [--dry-run] [--force] [--allow-dirty] \
#                   [--precision <precision_by_path csv> --evaluator https://orcid.org/<id>]
# Inputs:       --run-dir  a run folder with provcred_record.json (collect_run_provenance.py record),
#                          werk_links_merge.json and werk_links.csv (read row by row).
#               --counts   optional CSV with columns graph, linked_rows, triples and rows rule-span,
#                          model-span, all; the writer's own counts must equal it.
#               --precision, --evaluator  optional, together: the CSV of precision_by_path.py and the
#                          evaluator's ORCID IRI; adds the hand check of each path graph (strict precision).
#               git        scripts/py/werk_link_rules.py at the run's commit, for MIN_TERM_LENGTH.
# Outputs:      in --out-dir (default: the run folder):
#                   gnd-work-links-<run-id>.nq  graph/work and graph/work/<run-id>/{rule-span, model-span}
#                   prov-<run-id>.nq            the run provenance, all in graph/prov
#                   werk-rdf-<run-id>.json      this writer's record: its commit and code SHA-256, input
#                                               and output checksums, counts per graph, gaps
#               Exit codes: 0 success, 1 failure, 2 usage error.
# Dependencies: standard library; nquads.py, run_files.py, ner.py, linking.py, handcheck.py beside this file; git.
# Assumptions:  Only --decision baseline runs are written. Not written: runs that reused Flair.
#               The hand check is written only with --precision and --evaluator.
#               A value the run's files lack gives no triple and an entry under "gaps"; a missing run
#               ID, commit or links checksum stops the writer. Outputs are written to .tmp files and
#               renamed, in the order of werk_links.csv, so the same run gives the same bytes.

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import resource
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import handcheck
import linking
import ner
from nquads import GRAPH_PROV, GRAPH_WORK, QuadFile, iri
from run_files import RunFacts, die, file_sha256, load_run

ROOT = Path(__file__).resolve().parents[2]
CODE_DIR = "scripts/werk-linking"
CODE_FILES = ["write_werk_rdf.py", "run_files.py", "ner.py", "linking.py", "handcheck.py", "nquads.py"]
INPUT_FILES = ["provcred_record.json", "werk_links_merge.json", "werk_links.csv"]
LINK_COLUMNS = {"obj_id", "status", "gnd_uri", "matched_span_source"}

logger = logging.getLogger("write_werk_rdf")


def git_state() -> tuple[str, bool]:
    """The gemea commit, and whether the writer's modules have uncommitted changes."""
    try:
        commit = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, check=True)
        status = subprocess.run(
            ["git", "-C", str(ROOT), "status", "--porcelain", "--", CODE_DIR], capture_output=True, text=True, check=True
        )
    except (OSError, subprocess.CalledProcessError) as e:
        die(f"cannot read the git state of {ROOT} ({e}); run from a gemea checkout")
    return commit.stdout.strip(), bool(status.stdout.strip())


def output_paths(out_dir: Path, run_id: str) -> dict[str, Path]:
    return {
        "data": out_dir / f"gnd-work-links-{run_id}.nq",
        "prov": out_dir / f"prov-{run_id}.nq",
        "record": out_dir / f"werk-rdf-{run_id}.json",
    }


def preflight(args: argparse.Namespace) -> None:
    """Stop before any work if an input is missing or the output folder cannot be written."""
    for name in INPUT_FILES:
        if not (args.run_dir / name).is_file():
            die(f"{args.run_dir / name} not found; the run folder needs {', '.join(INPUT_FILES)}")
    if args.counts and not args.counts.is_file():
        die(f"--counts {args.counts} not found")
    if not args.out_dir.is_dir():
        die(f"--out-dir {args.out_dir} is not a folder; create it first")
    if not args.dry_run and not os.access(args.out_dir, os.W_OK):
        die(f"{args.out_dir} is not writable; pass another --out-dir")


def link_rows(links: Path) -> Iterator[dict[str, str]]:
    """The rows of werk_links.csv, one at a time."""
    with open(links, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        missing = LINK_COLUMNS - set(reader.fieldnames or [])
        if missing:
            die(f"{links} has no column {', '.join(sorted(missing))}; pass a link_gnd_local.py output")
        yield from reader


def write_data(links: Path, facts: RunFacts, target: QuadFile | None) -> linking.GraphCounts:
    """Stream the link rows into the data file (or nowhere, on a dry run) and count them."""
    counts = linking.GraphCounts()
    try:
        for triple, graph in linking.data_quads(link_rows(links), facts.run_id, counts):
            if target is not None:
                target.write(triple, graph)
    except linking.RowError as e:
        die(f"{links}, {e}; nothing written")
    if counts.linked_rows != facts.linked_rows:
        die(f"{counts.linked_rows} link rows in {links}, but werk_links_merge.json says {facts.linked_rows}")
    return counts


def check_counts(path: Path, counts: linking.GraphCounts) -> None:
    """Stop if the collector's counts differ from the writer's own."""
    with open(path, newline="", encoding="utf-8") as f:
        theirs = {row["graph"]: row for row in csv.DictReader(f)}
    ours = {name: (counts.rows[name], counts.triples[name]) for name in counts.rows}
    ours["all"] = (counts.linked_rows, counts.distinct)
    for name, (rows, triples) in ours.items():
        row = theirs.get(name)
        if row is None or (int(row["linked_rows"]), int(row["triples"])) != (rows, triples):
            die(f"{path}: {name} is {row}, the writer counts {rows} rows and {triples} triples; nothing more written")


def read_handcheck(args: argparse.Namespace) -> list[handcheck.PathPrecision]:
    """The strict precision rows of --precision, or none without it."""
    if not args.precision:
        return []
    try:
        return handcheck.read_precision(args.precision)
    except (ValueError, KeyError) as e:
        die(f"--precision {args.precision}: {e}")


def prov_triples(
    facts: RunFacts, counts: linking.GraphCounts, checks: list[handcheck.PathPrecision], evaluator: str | None
) -> list[tuple[str, str, str]]:
    triples = ner.ner_triples(facts) + linking.linking_triples(facts, counts)
    if checks and evaluator:
        written = [check for check in checks if check.path in counts.rows]
        triples += handcheck.handcheck_triples(written, facts.run_id, evaluator)
    return triples


def graph_sizes(facts: RunFacts, counts: linking.GraphCounts, prov_count: int) -> dict[str, int]:
    sizes = {GRAPH_WORK: counts.distinct}
    for path in sorted(counts.triples):
        sizes[f"{GRAPH_WORK}/{facts.run_id}/{path}"] = counts.triples[path]
    sizes[GRAPH_PROV] = prov_count
    return sizes


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Write the links and the PROV-CRED provenance of one Werk linking run as N-Quads.")
    parser.add_argument("--run-dir", type=Path, required=True, help="run folder with provcred_record.json")
    parser.add_argument("--out-dir", type=Path, help="where to write (default: the run folder)")
    parser.add_argument("--counts", type=Path, help="counts CSV to check the writer's own counts against")
    parser.add_argument("--precision", type=Path, help="precision_by_path.py CSV; adds the hand check")
    parser.add_argument("--evaluator", help="ORCID IRI of who labelled the sample, https://orcid.org/<id>")
    parser.add_argument("--dry-run", action="store_true", help="read everything, print counts and gaps; write nothing")
    parser.add_argument("--force", action="store_true", help="overwrite existing outputs")
    parser.add_argument("--allow-dirty", action="store_true", help="run despite uncommitted changes; recorded")
    args = parser.parse_args(argv)
    if not args.run_dir.is_dir():
        parser.error(f"--run-dir {args.run_dir} is not a folder")
    if bool(args.precision) != bool(args.evaluator):
        parser.error("--precision and --evaluator go together")
    if args.evaluator and not handcheck.ORCID_RE.fullmatch(args.evaluator):
        parser.error("--evaluator must be an ORCID IRI, https://orcid.org/0000-0000-0000-0000")
    if args.precision and not args.precision.is_file():
        parser.error(f"--precision {args.precision} is not a file")
    args.out_dir = args.out_dir or args.run_dir
    return args


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args(argv)
    preflight(args)
    commit, dirty = git_state()
    if dirty and not args.allow_dirty:
        die(f"{CODE_DIR} has uncommitted changes; commit them, or pass --allow-dirty")

    facts, gaps = load_run(args.run_dir, ROOT)
    checks = read_handcheck(args)
    if facts.decision != "baseline":
        die(f"--decision {facts.decision} is not written yet; only baseline runs have path graphs")
    links = args.run_dir / "werk_links.csv"
    logger.info("Hashing %s (%.0f MB)", links, links.stat().st_size / 1e6)
    if file_sha256(links) != facts.links_sha256:
        die(f"{links} does not have the SHA-256 of werk_links_merge.json ({facts.links_sha256}); copy the run's own file")
    paths = output_paths(args.out_dir, facts.run_id)
    if not args.dry_run:
        existing = [str(p) for p in paths.values() if p.exists()]
        if existing and not args.force:
            die(f"{', '.join(existing)} exist; pass --force to overwrite")

    if args.dry_run:
        counts = write_data(links, facts, None)
        prov_count = len(prov_triples(facts, counts, checks, args.evaluator))
    else:
        with QuadFile(paths["data"]) as data:
            counts = write_data(links, facts, data)
            if args.counts:
                check_counts(args.counts, counts)
        with QuadFile(paths["prov"]) as prov:
            for triple in prov_triples(facts, counts, checks, args.evaluator):
                prov.write(triple, iri(GRAPH_PROV))
        prov_count = prov.count
    if args.dry_run and args.counts:
        check_counts(args.counts, counts)

    sizes = graph_sizes(facts, counts, prov_count)
    for graph, size in sizes.items():
        logger.info("%s: %d triples", graph, size)
    logger.info("Link rows: %s", ", ".join(f"{path} {n}" for path, n in sorted(counts.rows.items())))
    logger.info("Gaps: %s", ", ".join(gaps) or "none")
    # ru_maxrss is bytes on macOS and kilobytes on Linux
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1 << 20 if sys.platform == "darwin" else 1 << 10)
    logger.info("Peak memory: %.0f MB", peak)
    if args.dry_run:
        logger.info("Dry run: nothing written")
        return 0

    record: dict[str, Any] = {
        "written_by": {
            "script": f"{CODE_DIR}/write_werk_rdf.py",
            "commit": commit,
            "uncommitted_changes": dirty,
            "sha256": {f"{CODE_DIR}/{name}": file_sha256(ROOT / CODE_DIR / name) for name in CODE_FILES},
        },
        "run_id": facts.run_id,
        "step1_id": facts.step1_id,
        "inputs": {name: file_sha256(args.run_dir / name) for name in INPUT_FILES},
        "hand_check": {
            "precision_csv": {args.precision.name: file_sha256(args.precision)},
            "evaluator": args.evaluator,
            "paths": {check.path: {"hits": check.hits, "n": check.n} for check in checks},
        }
        if args.precision
        else None,
        "outputs": {
            paths["data"].name: {"sha256": data.sha256, "quads": data.count},
            paths["prov"].name: {"sha256": prov.sha256, "quads": prov.count},
        },
        "link_rows": dict(sorted(counts.rows.items())),
        "triples_per_graph": sizes,
        "gaps": gaps,
    }
    temp = paths["record"].with_name(paths["record"].name + ".tmp")
    try:
        temp.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        os.replace(temp, paths["record"])
    finally:
        temp.unlink(missing_ok=True)
    logger.info("Wrote %s", ", ".join(str(p) for p in paths.values()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
