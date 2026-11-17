# Purpose:      The linking side of a Werk linking run:
#               the data quads of each link row, in its path graph
#               and in graph/work, and the PROV-CRED triples of the authority file, the linker, its
#               parameters and their values, the run, the run's output and the path graphs.
# Usage:        not run itself; imported by write_werk_rdf.py.
# Inputs:       rows of werk_links.csv (link_gnd_local.py), one at a time; a RunFacts of run_files.py.
# Outputs:      data_quads() yields (triple, graph term) and fills a GraphCounts;
#               linking_triples() returns triples for graph/prov.
# Dependencies: standard library; nquads.py, run_files.py, ner.py.
# Assumptions:  Only --decision baseline is written: a path is a span method (rule-span,
#               model-span). A link row has status "linked". Memory holds one set of GND IDs per
#               graph, bounded by the distinct Works of the run (D38 rerun: 9,330), not by its rows.

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field

import ner
from nquads import (
    DCTERMS,
    GEMEA,
    GRAPH_DDBEDM,
    GRAPH_WORK,
    MLS,
    MOCHO,
    PROV,
    PROVCRED,
    RDAC,
    RDFS,
    SKOS,
    VOID,
    XSD,
    Triple,
    decimal,
    integer,
    iri,
    literal,
)
from run_files import RunFacts

DDB_ITEM = "http://www.deutsche-digitale-bibliothek.de/item/"
GND = "https://d-nb.info/gnd/"
WORK = GEMEA + "work/"
OBJ_ID_RE = re.compile(r"[A-Z0-9]{32}")
GND_URI_RE = re.compile(re.escape(GND) + r"([0-9A-Za-z-]+)")
PATH_OF_SOURCE = {
    "isbd_title": "rule-span",
    "isbd_other_title": "rule-span",
    "volume_rule": "rule-span",
    "flair": "model-span",
}
SPAN_METHOD_OF_PATH = {"rule-span": "rule", "model-span": "flair"}
PATH_COMMENT = {
    "rule-span": "Links whose matched span came from a rule (ISBD title, ISBD other title, volume rule), "
    "made under --decision baseline: exact title, author confirmed, one Work.",
    "model-span": "Links whose matched span came from Flair, made under --decision baseline: exact title, "
    "author confirmed, one Work.",
}
GRAPH_WORK_COMMENT = (
    "All links and Works of the latest run; replaced by each run. Its provenance is that of the run's path graphs."
)
# name in the IRI -> (label, RunFacts field)
PARAMETERS = {
    "decision": ("--decision", "decision"),
    "min-term-length": ("MIN_TERM_LENGTH", "min_term_length"),
    "author-name-token-score": ("--name-threshold", "name_threshold"),
    "search-size": ("--search-size", "search_size"),
    "min-score": ("--min-score; only under --decision score", "min_score"),
}

A = ner.A
NAMED_INDIVIDUAL = ner.NAMED_INDIVIDUAL
ENTITY = ner.ENTITY
DATASET = iri(VOID + "Dataset")
MANIFESTATION = iri(MOCHO + "Manifestation")
IS_MANIFESTATION_OF = iri(MOCHO + "isManifestationOf")


class RowError(ValueError):
    """A link row the writer cannot turn into triples."""


@dataclass
class GraphCounts:
    """Link rows and triples per path, and the distinct triples of the run (the size of graph/work)."""

    rows: dict[str, int] = field(default_factory=dict)
    triples: dict[str, int] = field(default_factory=dict)
    distinct: int = 0

    @property
    def linked_rows(self) -> int:
        return sum(self.rows.values())


def path_of(source: str) -> str:
    """The path of a link from its matched_span_source."""
    if source not in PATH_OF_SOURCE:
        raise RowError(f"matched_span_source {source!r} is not one of {', '.join(PATH_OF_SOURCE)}")
    return PATH_OF_SOURCE[source]


def run_output(run_id: str) -> str:
    """The run's whole output: an entity, not a named graph."""
    return iri(f"{GRAPH_WORK}/{run_id}")


def path_graph(run_id: str, path: str) -> str:
    return iri(f"{GRAPH_WORK}/{run_id}/{path}")


def link_triples(obj_id: str, gnd_id: str) -> list[Triple]:
    item = iri(DDB_ITEM + obj_id)
    return [(item, A, MANIFESTATION), (item, IS_MANIFESTATION_OF, iri(WORK + gnd_id))]


def work_triples(gnd_id: str) -> list[Triple]:
    work = iri(WORK + gnd_id)
    return [
        (work, A, iri(MOCHO + "Work")),
        (work, A, iri(RDAC + "C10001")),
        (work, iri(SKOS + "exactMatch"), iri(GND + gnd_id)),
    ]


def data_quads(rows: Iterable[dict[str, str]], run_id: str, counts: GraphCounts) -> Iterator[tuple[Triple, str]]:
    """The quads of every link row, in row order: its path graph first, then graph/work."""
    graph_work = iri(GRAPH_WORK)
    seen: dict[str, set[str]] = {graph_work: set()}
    for number, row in enumerate(rows, start=2):  # line 1 of the CSV is the header
        if row["status"] != "linked":
            continue
        try:
            path = path_of(row["matched_span_source"])
            match = GND_URI_RE.fullmatch(row["gnd_uri"])
            if not OBJ_ID_RE.fullmatch(row["obj_id"]) or match is None:
                raise RowError(f"obj_id {row['obj_id']!r} or gnd_uri {row['gnd_uri']!r} is malformed")
        except RowError as e:
            raise RowError(f"line {number}: {e}") from None
        gnd_id = match.group(1)
        graph = path_graph(run_id, path)
        counts.rows[path] = counts.rows.get(path, 0) + 1
        for target in (graph, graph_work):
            works = seen.setdefault(target, set())
            triples = link_triples(row["obj_id"], gnd_id)
            if gnd_id not in works:
                works.add(gnd_id)
                triples += work_triples(gnd_id)
            for triple in triples:
                yield triple, target
            if target == graph_work:
                counts.distinct += len(triples)
            else:
                counts.triples[path] = counts.triples.get(path, 0) + len(triples)


def authority_file(facts: RunFacts) -> str | None:
    return ner.urn("authority-file", "gnd", facts.tables_sha256)


def linker(facts: RunFacts) -> str | None:
    return ner.urn("software", "link_gnd_local", facts.step2_commit)


def parameter(name: str) -> str:
    return iri(f"urn:gemea:param:link_gnd_local:{name}")


def parameter_value(value: object) -> str:
    """A setting as a literal: text plain, whole numbers xsd:integer, the rest xsd:decimal."""
    if isinstance(value, str):
        return literal(value)
    if isinstance(value, int):
        return integer(value)
    return decimal(float(str(value)))


def parameter_triples(facts: RunFacts) -> list[ner.MaybeTriple]:
    """The linker's five parameters, and the value each one had in this run."""
    run = ner.urn("linking-run", facts.run_id)
    triples: list[ner.MaybeTriple] = []
    for name, (label, attribute) in PARAMETERS.items():
        triples += [
            (linker(facts), iri(MLS + "hasHyperParameter"), parameter(name)),
            (parameter(name), A, NAMED_INDIVIDUAL),
            (parameter(name), A, iri(MLS + "HyperParameter")),
            (parameter(name), iri(RDFS + "label"), literal(label)),
        ]
        value = getattr(facts, attribute)
        if value is None:
            continue
        setting = iri(f"urn:gemea:param-value:link_gnd_local:{facts.run_id}:{name}")
        triples += [
            (run, iri(MLS + "hasInput"), setting),
            (setting, A, NAMED_INDIVIDUAL),
            (setting, A, iri(MLS + "HyperParameterSetting")),
            (setting, iri(MLS + "specifiedBy"), parameter(name)),
            (setting, iri(MLS + "hasValue"), parameter_value(value)),
        ]
    return triples


def run_triples(facts: RunFacts, counts: GraphCounts) -> list[ner.MaybeTriple]:
    """The linker activity and the run's whole output."""
    run = ner.urn("linking-run", facts.run_id)
    output = run_output(facts.run_id)
    graph_work = iri(GRAPH_WORK)
    triples: list[ner.MaybeTriple] = [
        (run, A, NAMED_INDIVIDUAL),
        (run, A, iri(PROVCRED + "HeuristicEnrichment")),
        (run, A, iri(MLS + "Run")),
        (run, iri(PROVCRED + "refinementOrder"), integer(1)),
        (run, iri(PROV + "startedAtTime"), ner.date_time(facts.linker_start)),
        (run, iri(PROV + "endedAtTime"), ner.date_time(facts.linker_end)),
        (run, iri(PROV + "used"), ner.span_set(facts, "rule")),
        (run, iri(PROV + "used"), ner.span_set(facts, "flair")),
        (run, iri(PROV + "used"), authority_file(facts)),
        (run, iri(PROV + "wasAssociatedWith"), linker(facts)),
        (run, iri(MLS + "executes"), linker(facts)),
        (run, iri(PROVCRED + "affectedPredicate"), IS_MANIFESTATION_OF),
        (run, iri(PROVCRED + "sourceGraph"), iri(GRAPH_DDBEDM)),
        (run, iri(PROVCRED + "targetGraph"), graph_work),
        (run, iri(PROVCRED + "triplesAdded"), literal(str(counts.distinct), XSD + "nonNegativeInteger")),
        (output, A, NAMED_INDIVIDUAL),
        (output, A, ENTITY),
        (output, A, DATASET),
        (output, iri(PROV + "specializationOf"), graph_work),
        (output, iri(PROV + "wasGeneratedBy"), run),
        (output, iri(VOID + "triples"), integer(counts.distinct)),
        (graph_work, A, NAMED_INDIVIDUAL),
        (graph_work, A, ENTITY),
        (graph_work, A, DATASET),
        (graph_work, iri(RDFS + "comment"), literal(GRAPH_WORK_COMMENT, lang="en")),
    ]
    for path in sorted(counts.rows):
        graph = path_graph(facts.run_id, path)
        triples += [(run, iri(PROVCRED + "targetGraph"), graph), (output, iri(VOID + "subset"), graph)]
    return triples


def path_graph_triples(facts: RunFacts, counts: GraphCounts) -> list[ner.MaybeTriple]:
    """Each path graph with its span set, its count and the agents behind its links (the shortcut)."""
    run = ner.urn("linking-run", facts.run_id)
    agents = {
        "rule-span": [linker(facts), ner.rule_script(facts), ner.extractor(facts)],
        "model-span": [linker(facts), ner.model(facts)],
    }
    triples: list[ner.MaybeTriple] = []
    for path in sorted(counts.rows):
        graph = path_graph(facts.run_id, path)
        triples += [
            (graph, A, NAMED_INDIVIDUAL),
            (graph, A, ENTITY),
            (graph, A, DATASET),
            (graph, iri(RDFS + "comment"), literal(PATH_COMMENT[path], lang="en")),
            (graph, iri(PROV + "wasGeneratedBy"), run),
            (graph, iri(PROV + "wasDerivedFrom"), ner.span_set(facts, SPAN_METHOD_OF_PATH[path])),
            (graph, iri(PROV + "wasDerivedFrom"), authority_file(facts)),
            (graph, iri(VOID + "triples"), integer(counts.triples[path])),
        ]
        triples += [(graph, iri(PROV + "wasAttributedTo"), agent) for agent in agents[path]]
    return triples


def linking_triples(facts: RunFacts, counts: GraphCounts) -> list[Triple]:
    """Every triple of the linking side, for graph/prov."""
    authority = authority_file(facts)
    triples: list[ner.MaybeTriple] = [
        (authority, A, NAMED_INDIVIDUAL),
        (authority, A, ENTITY),
        (authority, iri(DCTERMS + "identifier"), ner.checksum(facts.tables_sha256)),
        (authority, iri(DCTERMS + "date"), ner.date(facts.dump_date)),
        (linker(facts), A, NAMED_INDIVIDUAL),
        (linker(facts), A, ner.SOFTWARE_AGENT),
        (linker(facts), A, iri(MLS + "Implementation")),
        (linker(facts), iri(DCTERMS + "hasVersion"), literal(facts.step2_commit)),
    ]
    triples += parameter_triples(facts)
    triples += run_triples(facts, counts)
    triples += path_graph_triples(facts, counts)
    return ner.kept(triples)
