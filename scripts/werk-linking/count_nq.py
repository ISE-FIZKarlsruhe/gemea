#!/usr/bin/env python3
# Purpose:      Counts of N-Quads files for reporting (Werk links and their provenance,
#               write_werk_rdf.py outputs): triples, predicates, instances per class, distinct
#               subjects and objects per graph; for the link graphs also the Works shared by the
#               path graphs and the number of DDB records per Work.
# Usage:        .venv/bin/python scripts/werk-linking/count_nq.py --input <file.nq>... \
#                   --output <counts.csv> [--force] [--dry-run]
# Inputs:       --input  N-Quads files as write_werk_rdf.py writes them: one quad per line, graph IRI
#                        as the fourth term (read line by line).
# Outputs:      --output CSV: graph, measure, key, count. Measures: triples, predicate, class
#               (distinct instances), distinct_subjects, distinct_objects_iri, literal_objects,
#               and, over the link graphs: works_in_graphs (key: the graphs a Work is in),
#               records_per_work (key: a size bin; graph/work only), max_records_per_work.
#               Exit codes: 0 success, 1 failure, 2 usage error.
# Dependencies: standard library; load_nq_qlever.py beside this file (split_quad).
# Assumptions:  Memory holds sets of subjects, objects and Works per graph, bounded by the run's
#               links (D38: under 100,000 per graph), not by the corpus.

from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import NoReturn

from load_nq_qlever import split_quad

RDF_TYPE = "<http://www.w3.org/1999/02/22-rdf-syntax-ns#type>"
IS_MANIFESTATION_OF = "<https://ise-fizkarlsruhe.github.io/ddbkg/mocho#isManifestationOf>"
GRAPH_WORK = "<https://gemea.ise.fiz-karlsruhe.de/graph/work>"
ALL = "all"
COLUMNS = ["graph", "measure", "key", "count"]
BINS = [(1, 1, "1"), (2, 5, "2-5"), (6, 20, "6-20"), (21, 100, "21-100"), (101, 10**12, ">100")]

logger = logging.getLogger("count_nq")


def die(message: str) -> NoReturn:
    logger.error(message)
    sys.exit(1)


def split_triple(triple: str) -> tuple[str, str, str]:
    """Subject, predicate and object of a triple whose subject and predicate are IRIs."""
    subject, predicate, obj = triple.split(" ", 2)
    return subject, predicate, obj


def size_bin(n: int) -> str:
    return next(label for low, high, label in BINS if low <= n <= high)


class Tally:
    """Counts per graph, filled one quad at a time."""

    def __init__(self) -> None:
        self.triples: Counter[str] = Counter()
        self.predicates: dict[str, Counter[str]] = defaultdict(Counter)
        self.instances: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
        self.subjects: dict[str, set[str]] = defaultdict(set)
        self.objects: dict[str, set[str]] = defaultdict(set)
        self.literals: Counter[str] = Counter()
        self.records_per_work: dict[str, Counter[str]] = defaultdict(Counter)

    def add(self, triple: str, graph: str) -> None:
        subject, predicate, obj = split_triple(triple)
        self.triples[graph] += 1
        self.predicates[graph][predicate] += 1
        self.subjects[graph].add(subject)
        if obj.startswith("<"):
            self.objects[graph].add(obj)
        else:
            self.literals[graph] += 1
        if predicate == RDF_TYPE:
            self.instances[graph][obj].add(subject)
        if predicate == IS_MANIFESTATION_OF:
            self.records_per_work[graph][obj] += 1

    def rows(self) -> list[list[str | int]]:
        table: list[list[str | int]] = []
        for graph in sorted(self.triples):
            table.append([graph, "triples", "", self.triples[graph]])
            table += [[graph, "predicate", p, n] for p, n in sorted(self.predicates[graph].items())]
            table += [[graph, "class", c, len(s)] for c, s in sorted(self.instances[graph].items())]
            table.append([graph, "distinct_subjects", "", len(self.subjects[graph])])
            table.append([graph, "distinct_objects_iri", "", len(self.objects[graph])])
            table.append([graph, "literal_objects", "", self.literals[graph]])
        table.append([ALL, "triples", "", sum(self.triples.values())])
        table += self.work_rows()
        return table

    def work_rows(self) -> list[list[str | int]]:
        """Works shared by the path graphs, and records per Work in graph/work."""
        paths = sorted(g for g in self.records_per_work if g != GRAPH_WORK)
        graphs_of: dict[str, list[str]] = defaultdict(list)
        for graph in paths:
            for work in self.records_per_work[graph]:
                graphs_of[work].append(graph.rsplit("/", 1)[-1].rstrip(">"))
        shared = Counter(" + ".join(sorted(names)) for names in graphs_of.values())
        table: list[list[str | int]] = [[ALL, "works_in_graphs", key, n] for key, n in sorted(shared.items())]
        per_work = self.records_per_work.get(GRAPH_WORK)
        if per_work:
            bins = Counter(size_bin(n) for n in per_work.values())
            table += [[GRAPH_WORK, "records_per_work", label, bins[label]] for _, _, label in BINS if bins[label]]
            table.append([GRAPH_WORK, "max_records_per_work", per_work.most_common(1)[0][0], per_work.most_common(1)[0][1]])
        return table


def count(paths: Iterable[Path]) -> Tally:
    tally = Tally()
    for path in paths:
        with open(path, encoding="utf-8") as f:
            for number, line in enumerate(f, start=1):
                if not line.strip():
                    continue
                try:
                    triple, graph = split_quad(line)
                except ValueError as e:
                    die(f"{path}, line {number}: {e}")
                tally.add(triple, graph)
    return tally


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Counts of N-Quads files per graph, for reporting.")
    parser.add_argument("--input", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--force", action="store_true", help="overwrite the output")
    parser.add_argument("--dry-run", action="store_true", help="print the table; write nothing")
    args = parser.parse_args(argv)
    for path in args.input:
        if not path.is_file():
            parser.error(f"--input {path} is not a file")
    return args


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args(argv)
    if not args.dry_run:
        if args.output.exists() and not args.force:
            die(f"{args.output} exists; pass --force to overwrite")
        if not os.access(args.output.parent, os.W_OK):
            die(f"{args.output.parent} is not writable")
    table = count(args.input).rows()
    if args.dry_run:
        print(",".join(COLUMNS))
        for row in table:
            print(",".join(str(value) for value in row))
        return 0
    temp = args.output.with_name(args.output.name + ".tmp")
    try:
        with open(temp, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(COLUMNS)
            writer.writerows(table)
        os.replace(temp, args.output)
    finally:
        temp.unlink(missing_ok=True)
    logger.info("Wrote %s (%d rows)", args.output, len(table))
    return 0


if __name__ == "__main__":
    sys.exit(main())
