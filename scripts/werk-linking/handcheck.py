# Purpose:      The hand check of a Werk linking run as PROV-CRED triples:
#               per path graph with a labelled sample, a
#               provcred:HumanEnrichment activity, the sample, the evaluator and the measured
#               strict precision with its sample size.
# Usage:        not run itself; imported by write_werk_rdf.py.
# Inputs:       the CSV of precision_by_path.py; the evaluator's IRI; the run ID.
# Outputs:      read_precision() returns the strict rows per path; handcheck_triples() returns
#               triples for graph/prov.
# Dependencies: standard library; nquads.py, ner.py, linking.py.
# Assumptions:  Only the strict definition is written. provcred:measuredEvaluation
#               and provcred:sampleSize are proposed terms, not yet in provcred.ttl.
#               A path without usable sampled links (n = 0) gets no triples.

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

import linking
import ner
from nquads import (
    DCTERMS,
    MLS,
    PROV,
    PROVCRED,
    RDFS,
    XSD,
    Triple,
    integer,
    iri,
    literal,
)

DEFINITION = "strict"
ORCID_RE = re.compile(r"https://orcid\.org/\d{4}-\d{4}-\d{4}-\d{3}[\dX]")
SHA256_RE = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class PathPrecision:
    path: str
    hits: int
    n: int
    excluded: int
    sample_sha256: str


def read_precision(path: Path) -> list[PathPrecision]:
    """The strict rows of the path graphs, in file order; raises ValueError on a malformed file."""
    rows: list[PathPrecision] = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        missing = {"path", "definition", "hits", "n", "excluded", "sample_sha256"} - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"no column {', '.join(sorted(missing))}; pass a precision_by_path.py output")
        for row in reader:
            if row["definition"] != DEFINITION or row["path"] not in linking.SPAN_METHOD_OF_PATH:
                continue
            if not SHA256_RE.fullmatch(row["sample_sha256"]):
                raise ValueError(f"{row['path']}: sample_sha256 {row['sample_sha256']!r} is not a SHA-256")
            hits, n = int(row["hits"]), int(row["n"])
            if not 0 <= hits <= n:
                raise ValueError(f"{row['path']}: {hits} hits of {n}")
            rows.append(PathPrecision(row["path"], hits, n, int(row["excluded"]), row["sample_sha256"]))
    return rows


def comment(row: PathPrecision) -> str:
    left_out = f"; {row.excluded} labelled unsure left out" if row.excluded else ""
    return (
        f"Strict precision of the links of this path graph: {row.hits} of {row.n} hand-checked links are the "
        f"record's Work{left_out}. The links were sampled at random from all links, not per path."
    )


def handcheck_triples(rows: list[PathPrecision], run_id: str, evaluator: str) -> list[Triple]:
    """The hand check of each path graph that has usable sampled links."""
    person = iri(evaluator)
    run = ner.urn("linking-run", run_id)
    triples: list[ner.MaybeTriple] = []
    for row in rows:
        if row.n == 0:
            continue
        graph = linking.path_graph(run_id, row.path)
        check = ner.urn("precision-check", row.sample_sha256)
        sample = ner.urn("sample", row.sample_sha256)
        score = ner.urn("evaluation", run_id, row.path)
        triples += [
            (check, ner.A, ner.NAMED_INDIVIDUAL),
            (check, ner.A, iri(PROVCRED + "HumanEnrichment")),
            (check, iri(PROVCRED + "refinementOrder"), integer(2)),
            (check, iri(PROV + "wasInformedBy"), run),
            (check, iri(PROV + "used"), graph),
            (check, iri(PROV + "used"), sample),
            (check, iri(PROV + "wasAssociatedWith"), person),
            (sample, ner.A, ner.NAMED_INDIVIDUAL),
            (sample, ner.A, ner.ENTITY),
            (sample, iri(DCTERMS + "identifier"), ner.checksum(row.sample_sha256)),
            (score, ner.A, ner.NAMED_INDIVIDUAL),
            (score, ner.A, iri(MLS + "ModelEvaluation")),
            (score, ner.A, ner.ENTITY),
            (score, iri(MLS + "specifiedBy"), iri(PROVCRED + "Precision")),
            (score, iri(MLS + "hasValue"), literal(f"{row.hits / row.n:.4f}", XSD + "decimal")),
            (score, iri(PROVCRED + "sampleSize"), literal(str(row.n), XSD + "nonNegativeInteger")),
            (score, iri(RDFS + "comment"), literal(comment(row), lang="en")),
            (score, iri(PROV + "wasGeneratedBy"), check),
            (score, iri(PROV + "wasAttributedTo"), person),
            (graph, iri(PROVCRED + "measuredEvaluation"), score),
        ]
    if triples:
        triples += [(person, ner.A, iri(PROV + "Agent")), (person, ner.A, iri(PROV + "Person"))]
    return ner.kept(triples)
