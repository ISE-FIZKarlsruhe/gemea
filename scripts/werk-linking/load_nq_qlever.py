#!/usr/bin/env python3
# Purpose:      Load N-Quads files into a running QLever server with SPARQL UPDATE (INSERT DATA in
#               batches), for the Werk links and their provenance (write_werk_rdf.py outputs), when
#               the index is not rebuilt. Counts each graph before and after.
# Usage:        On the QLever host:
#                   export QLEVER_ACCESS_TOKEN=...      # the server's -a value; never on the command line
#                   python3 load_nq_qlever.py --input gnd-work-links-<run-id>.nq prov-<run-id>.nq \
#                       [--container gemea_qlever] [--port 7019] [--batch-size 5000] \
#                       [--drop-graph <graph IRI>]... [--dry-run]
# Inputs:       --input  N-Quads files, one quad per line, graph as the fourth term (read line by line).
#               QLEVER_ACCESS_TOKEN  environment variable; passed into the container's environment.
# Outputs:      Triples in the server's graphs; a log with the count of each graph before and after.
#               Nothing is written to disk by this script. Exit codes: 0 success, 1 failure, 2 usage error.
# Dependencies: standard library; docker on PATH, curl inside the container (the server publishes
#               no port, so requests are sent from inside it with docker exec).
# Assumptions:  The server was started with -a <token>, and with --persist-updates if the triples
#               are to survive a restart. INSERT DATA of a triple that is already there changes
#               nothing, so a run that broke off is simply run again. --drop-graph removes a whole
#               graph first and cannot be undone without the persisted update file or a rebuild.
#               Lines must end in " <graph> ." as write_werk_rdf.py writes them; default-graph
#               triples stop the run. Not tested against QLever b7623f before its first use.

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import NoReturn

TOKEN_VARIABLE = "QLEVER_ACCESS_TOKEN"
COUNT_QUERY = "SELECT (COUNT(*) AS ?n) WHERE {{ GRAPH {graph} {{ ?s ?p ?o }} }}"

logger = logging.getLogger("load_nq_qlever")

# (kind, text) -> response body; kind is "query" or "update"
Sender = Callable[[str, str], str]


def die(message: str) -> NoReturn:
    logger.error(message)
    sys.exit(1)


def split_quad(line: str) -> tuple[str, str]:
    """An N-Quads line as (triple text, graph term); raises ValueError without a graph IRI."""
    body = line.rstrip("\n")
    if not body.endswith(" ."):
        raise ValueError("line does not end in ' .'")
    triple, _, graph = body[:-2].rpartition(" ")
    if not (graph.startswith("<") and graph.endswith(">")) or triple.count(" ") < 2:
        raise ValueError("no graph IRI as fourth term")
    return triple, graph


def batches(lines: Iterable[str], size: int) -> Iterator[tuple[str, list[str]]]:
    """Runs of at most `size` triples that share a graph, in file order."""
    current: str | None = None
    triples: list[str] = []
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            triple, graph = split_quad(line)
        except ValueError as e:
            raise ValueError(f"line {number}: {e}") from None
        if triples and (graph != current or len(triples) >= size):
            assert current is not None
            yield current, triples
            triples = []
        current = graph
        triples.append(triple)
    if triples and current is not None:
        yield current, triples


def insert_update(graph: str, triples: list[str]) -> str:
    """One INSERT DATA request for triples of one graph."""
    body = " .\n".join(triples)
    return f"INSERT DATA {{ GRAPH {graph} {{\n{body} .\n}} }}"


def graph_sizes(paths: list[Path]) -> Counter[str]:
    """Quads per graph over all input files; also checks every line."""
    sizes: Counter[str] = Counter()
    for path in paths:
        with open(path, encoding="utf-8") as f:
            try:
                for graph, triples in batches(f, 10_000):
                    sizes[graph] += len(triples)
            except ValueError as e:
                die(f"{path}, {e}; fix the file before loading")
    return sizes


def docker_sender(container: str, port: int) -> Sender:
    """Send a query or an update to the server from inside its container; the body goes over stdin."""

    def send(kind: str, text: str) -> str:
        if kind == "update":
            headers = '-H "Content-Type: application/sparql-update" -H "Authorization: Bearer $QLEVER_ACCESS_TOKEN"'
        else:
            headers = '-H "Content-Type: application/sparql-query" -H "Accept: text/tab-separated-values"'
        # fixed strings and an int only; the request body and the token never enter the command line
        command = f"curl -sS --fail-with-body -X POST http://localhost:{port} {headers} --data-binary @-"
        result = subprocess.run(
            ["docker", "exec", "-i", "-e", TOKEN_VARIABLE, container, "sh", "-c", command],
            input=text.encode("utf-8"),
            capture_output=True,
            check=False,  # the exit code is reported below, with curl's answer
        )
        if result.returncode != 0:
            answer = (result.stdout + result.stderr).decode("utf-8", "replace").strip()[:600]
            die(f"QLever refused the {kind} (curl exit {result.returncode}): {answer}")
        return result.stdout.decode("utf-8")

    return send


def count_graph(send: Sender, graph: str) -> int:
    answer = send("query", COUNT_QUERY.format(graph=graph)).split()
    if len(answer) != 2 or not answer[1].isdigit():
        die(f"unexpected answer to the count of {graph}: {answer}")
    return int(answer[1])


def load(paths: list[Path], send: Sender, batch_size: int, drop: list[str]) -> dict[str, tuple[int, int]]:
    """Drop, insert and count; returns each graph's count before and after."""
    graphs = sorted(graph_sizes(paths))
    before = {graph: count_graph(send, graph) for graph in graphs}
    for graph in drop:
        logger.info("Dropping %s (%d triples)", graph, count_graph(send, graph))
        send("update", f"DROP SILENT GRAPH {graph}")
    for path in paths:
        sent = 0
        with open(path, encoding="utf-8") as f:
            for graph, triples in batches(f, batch_size):
                send("update", insert_update(graph, triples))
                sent += len(triples)
        logger.info("%s: %d quads sent", path, sent)
    return {graph: (before[graph], count_graph(send, graph)) for graph in graphs}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Load N-Quads files into a running QLever server with INSERT DATA.")
    parser.add_argument("--input", type=Path, nargs="+", required=True, help="N-Quads files")
    parser.add_argument("--container", default="gemea_qlever")
    parser.add_argument("--port", type=int, default=7019, help="the server's port inside the container")
    parser.add_argument("--batch-size", type=int, default=5000, help="triples per request")
    parser.add_argument("--drop-graph", action="append", default=[], metavar="IRI", help="graph to empty first")
    parser.add_argument("--dry-run", action="store_true", help="check the files and print the plan; send nothing")
    args = parser.parse_args(argv)
    if args.batch_size < 1:
        parser.error("--batch-size must be at least 1")
    for path in args.input:
        if not path.is_file():
            parser.error(f"--input {path} is not a file")
    args.drop_graph = [g if g.startswith("<") else f"<{g}>" for g in args.drop_graph]
    return args


def main(argv: list[str] | None = None, send: Sender | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args(argv)
    sizes = graph_sizes(args.input)
    for graph, size in sorted(sizes.items()):
        logger.info("To load into %s: %d quads", graph, size)
    for graph in args.drop_graph:
        logger.info("To drop first: %s", graph)
    if args.dry_run:
        logger.info("Dry run: nothing sent")
        return 0
    if send is None:
        if not os.environ.get(TOKEN_VARIABLE):
            die(f"{TOKEN_VARIABLE} is not set; export the server's access token first")
        send = docker_sender(args.container, args.port)
    counts = load(args.input, send, args.batch_size, args.drop_graph)
    failed = False
    for graph, (before, after) in counts.items():
        logger.info("%s: %d before, %d after, %d in the files", graph, before, after, sizes[graph])
        # a graph that was empty or dropped must now hold exactly the files' quads; others at least as many as before
        expected_exact = before == 0 or graph in args.drop_graph
        if (expected_exact and after != sizes[graph]) or after < sizes[graph]:
            failed = True
            logger.error("%s holds %d triples, expected %s%d", graph, after, "" if expected_exact else "at least ", sizes[graph])
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
