# Purpose:      The span side of a Werk linking run as PROV-CRED triples:
#               the source data, the extraction
#               agents, the NER model with its two published scores, and one span extraction and
#               one span set per span method (rule, Flair).
# Usage:        not run itself; imported by write_werk_rdf.py and linking.py.
# Inputs:       a RunFacts of run_files.py.
# Outputs:      ner_triples() returns triples of N-Quads terms for graph/prov. A triple whose
#               value the run lacks is left out; an individual whose IRI cannot be minted is left
#               out with every triple that names it.
# Dependencies: standard library; nquads.py, run_files.py.
# Assumptions:  The published scores are read from the model's training.log by hand and hold for
#               one Hugging Face revision only; a run on another
#               revision gets no evaluation triples.

from __future__ import annotations

from typing import Optional

from nquads import (
    DCTERMS,
    MLS,
    OWL,
    PROV,
    PROVCRED,
    RDF,
    RDFS,
    XSD,
    Triple,
    decimal,
    integer,
    iri,
    literal,
)
from run_files import RunFacts

MODEL_ID = "flair/ner-english-ontonotes-large"
MODEL_SLUG = "flair-ner-english-ontonotes-large"
MODEL_CARD = "https://huggingface.co/flair/ner-english-ontonotes-large"
BENCHMARK_DOMAIN = "English; OntoNotes genres, not stated on the model card"
DEPLOYMENT_DOMAIN = "German bibliographic titles, DDB"
CORPUS_NOTE = (
    "The corpus is OntoNotes and English per the model ID and model card; the log names neither language nor version."
)
# revision -> (name, value, comment) per published score
PUBLISHED_SCORES: dict[str, list[tuple[str, float, str]]] = {
    "4ffb3596f4359f0c8799ea15bbf5dbb3b0915a53": [
        (
            "card",
            0.9093,
            "Micro F1 over all 18 types on the test split of the training corpus (9,479 sentences), from the "
            "model's training.log at revision 4ffb3596, line 904; the model card gives 90.93. " + CORPUS_NOTE,
        ),
        (
            "work-of-art",
            0.7087,
            "F1 for WORK_OF_ART only (118 tp, 49 fp, 48 fn) on the test split of the training corpus (9,479 "
            "sentences), from the model's training.log at revision 4ffb3596, line 925; micro-averaging over this "
            "one type equals its F1. " + CORPUS_NOTE,
        ),
    ]
}

A = iri(RDF + "type")
NAMED_INDIVIDUAL = iri(OWL + "NamedIndividual")
ENTITY = iri(PROV + "Entity")
SOFTWARE_AGENT = iri(PROV + "SoftwareAgent")

# Optional, not `|`: the alias is evaluated at import, and the Mac venv runs Python 3.9.
MaybeTriple = tuple[Optional[str], str, Optional[str]]  # noqa: UP045


def urn(kind: str, *parts: str | None) -> str | None:
    """A urn:gemea: IRI term, or None when one of its parts is missing."""
    if any(part is None for part in parts):
        return None
    return iri("urn:gemea:" + ":".join([kind, *[str(part) for part in parts]]))


def kept(triples: list[MaybeTriple]) -> list[Triple]:
    """The triples whose subject and object are known."""
    return [(s, p, o) for s, p, o in triples if s is not None and o is not None]


def text(value: str | None) -> str | None:
    return literal(value) if value is not None else None


def checksum(value: str | None) -> str | None:
    return literal(f"sha256:{value}") if value is not None else None


def date_time(value: str | None) -> str | None:
    return literal(value, XSD + "dateTime") if value is not None else None


def date(value: str | None) -> str | None:
    return literal(value, XSD + "date") if value is not None else None


def source_data(facts: RunFacts) -> str | None:
    return urn("source-data", facts.input_name, facts.input_sha256)


def extractor(facts: RunFacts) -> str | None:
    return urn("software", "extract_werk_spans", facts.step1_commit)


def rule_script(facts: RunFacts) -> str | None:
    return urn("software", "t10_rule_baseline", facts.rule_sha256)


def flair_library(facts: RunFacts) -> str | None:
    return urn("software", "flair", facts.flair_version)


def model(facts: RunFacts) -> str | None:
    return urn("model", MODEL_SLUG, facts.model_revision)


def span_extraction(facts: RunFacts, method: str) -> str | None:
    return urn("span-extraction", facts.step1_id, method)


def span_set(facts: RunFacts, method: str) -> str | None:
    return urn("span-set", facts.step1_id, method)


def agent_triples(facts: RunFacts) -> list[MaybeTriple]:
    triples: list[MaybeTriple] = []
    for agent in (extractor(facts), rule_script(facts), flair_library(facts)):
        triples += [(agent, A, NAMED_INDIVIDUAL), (agent, A, SOFTWARE_AGENT)]
    triples += [
        (extractor(facts), iri(DCTERMS + "hasVersion"), text(facts.step1_commit)),
        (rule_script(facts), iri(DCTERMS + "identifier"), checksum(facts.rule_sha256)),
        (flair_library(facts), iri(DCTERMS + "hasVersion"), text(facts.flair_version)),
    ]
    return triples


def model_triples(facts: RunFacts) -> list[MaybeTriple]:
    subject = model(facts)
    triples: list[MaybeTriple] = [
        (subject, A, NAMED_INDIVIDUAL),
        (subject, A, iri(PROVCRED + "Model")),
        (subject, iri(PROVCRED + "modelIdentifier"), literal(MODEL_ID)),
        (subject, iri(PROVCRED + "modelVersion"), text(facts.model_revision)),
        (subject, iri(PROVCRED + "snapshotDate"), date(facts.model_download_date)),
        (subject, iri(PROVCRED + "taskType"), iri(PROVCRED + "NamedEntityRecognition")),
        (subject, iri(PROVCRED + "benchmarkDomain"), literal(BENCHMARK_DOMAIN)),
        (subject, iri(PROVCRED + "deploymentDomain"), literal(DEPLOYMENT_DOMAIN)),
        (subject, iri(PROVCRED + "modelCard"), iri(MODEL_CARD)),
    ]
    if facts.model_revision is None:
        return triples
    log = iri(f"{MODEL_CARD}/resolve/{facts.model_revision}/training.log")
    for name, value, comment in PUBLISHED_SCORES.get(facts.model_revision, []):
        score = urn("evaluation", MODEL_SLUG, facts.model_revision, name)
        triples += [
            (subject, iri(PROVCRED + "publishedEvaluation"), score),
            (score, A, NAMED_INDIVIDUAL),
            (score, A, iri(MLS + "ModelEvaluation")),
            (score, iri(MLS + "specifiedBy"), iri(PROVCRED + "MicroF1")),
            (score, iri(MLS + "hasValue"), decimal(value)),
            (score, iri(DCTERMS + "language"), literal("en")),
            (score, iri(DCTERMS + "source"), log),
            (score, iri(RDFS + "comment"), literal(comment, lang="en")),
        ]
    return triples


def extraction_triples(facts: RunFacts) -> list[MaybeTriple]:
    """One activity and one span set per span method, both at refinement order 0."""
    agents = {
        "rule": ("DeterministicEnrichment", [extractor(facts), rule_script(facts)]),
        "flair": ("ModelEnrichment", [model(facts), flair_library(facts), extractor(facts)]),
    }
    triples: list[MaybeTriple] = []
    for method, (enrichment, associated) in agents.items():
        activity = span_extraction(facts, method)
        spans = span_set(facts, method)
        triples += [
            (activity, A, NAMED_INDIVIDUAL),
            (activity, A, iri(PROVCRED + enrichment)),
            (activity, iri(PROVCRED + "refinementOrder"), integer(0)),
            (activity, iri(PROV + "startedAtTime"), date_time(facts.step1_start)),
            (activity, iri(PROV + "endedAtTime"), date_time(facts.step1_end)),
            (activity, iri(PROV + "used"), source_data(facts)),
            (spans, A, NAMED_INDIVIDUAL),
            (spans, A, ENTITY),
            (spans, iri(PROV + "wasGeneratedBy"), activity),
        ]
        triples += [(activity, iri(PROV + "wasAssociatedWith"), agent) for agent in associated]
    return triples


def ner_triples(facts: RunFacts) -> list[Triple]:
    """Every triple of the span side, for graph/prov."""
    source = source_data(facts)
    triples: list[MaybeTriple] = [
        (source, A, NAMED_INDIVIDUAL),
        (source, A, ENTITY),
        (source, iri(DCTERMS + "identifier"), checksum(facts.input_sha256)),
    ]
    triples += agent_triples(facts)
    if model(facts) is not None:
        triples += model_triples(facts)
    triples += extraction_triples(facts)
    return kept(triples)
