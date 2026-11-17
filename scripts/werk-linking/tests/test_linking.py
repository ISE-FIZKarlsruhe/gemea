"""Tests for linking.py and ner.py."""

from __future__ import annotations

from pathlib import Path

import linking
import ner
import pytest
import run_files
import run_folder as rf
from nquads import GRAPH_WORK


@pytest.fixture()
def facts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> run_files.RunFacts:
    monkeypatch.setattr(run_files, "min_term_length", lambda repo, commit: 3)
    return run_files.load_run(rf.make_run(tmp_path), tmp_path)[0]


@pytest.mark.parametrize(
    "source, expected",
    [("isbd_title", "rule-span"), ("isbd_other_title", "rule-span"), ("volume_rule", "rule-span"), ("flair", "model-span")],
)
def test_path_of_known_source(source: str, expected: str) -> None:
    assert linking.path_of(source) == expected


@pytest.mark.parametrize("source", ["", "ocr", "Flair"])
def test_path_of_unknown_source_raises(source: str) -> None:
    with pytest.raises(linking.RowError):
        linking.path_of(source)


def test_data_quads_counts_per_path_and_distinct() -> None:
    counts = linking.GraphCounts()

    quads = list(linking.data_quads(rf.LINKS, rf.RUN_ID, counts))

    assert counts.rows == {"rule-span": 3, "model-span": 1}
    assert counts.triples == {"rule-span": rf.RULE_SPAN_TRIPLES, "model-span": rf.MODEL_SPAN_TRIPLES}
    assert counts.distinct == rf.DISTINCT_TRIPLES
    assert len(quads) == rf.RULE_SPAN_TRIPLES + rf.MODEL_SPAN_TRIPLES + rf.DISTINCT_TRIPLES
    assert len(set(quads)) == len(quads)


def test_data_quads_shared_work_is_in_both_path_graphs_and_once_in_graph_work() -> None:
    quads = list(linking.data_quads(rf.LINKS, rf.RUN_ID, linking.GraphCounts()))
    exact_match = "<http://www.w3.org/2004/02/skos/core#exactMatch>"

    graphs = [graph for (subject, predicate, _), graph in quads if predicate == exact_match and subject.endswith("/work/1>")]

    assert sorted(graphs) == sorted(
        [f"<{GRAPH_WORK}>", f"<{GRAPH_WORK}/{rf.RUN_ID}/rule-span>", f"<{GRAPH_WORK}/{rf.RUN_ID}/model-span>"]
    )


def test_data_quads_link_row_gives_item_and_work_iris() -> None:
    quads = list(linking.data_quads(rf.LINKS[:1], rf.RUN_ID, linking.GraphCounts()))

    assert quads[1] == (
        (
            f"<http://www.deutsche-digitale-bibliothek.de/item/{rf.OBJ_A}>",
            "<https://ise-fizkarlsruhe.github.io/ddbkg/mocho#isManifestationOf>",
            "<https://gemea.ise.fiz-karlsruhe.de/work/1>",
        ),
        f"<{GRAPH_WORK}/{rf.RUN_ID}/rule-span>",
    )


@pytest.mark.parametrize(
    "field, value",
    [("obj_id", "abc"), ("gnd_uri", "http://d-nb.info/gnd/1"), ("gnd_uri", ""), ("matched_span_source", "")],
)
def test_data_quads_malformed_row_raises_with_line_number(field: str, value: str) -> None:
    rows = [rf.LINKS[0], {**rf.LINKS[1], field: value}]

    with pytest.raises(linking.RowError, match="line 3"):
        list(linking.data_quads(rows, rf.RUN_ID, linking.GraphCounts()))


def test_parameter_value_types() -> None:
    assert linking.parameter_value("baseline") == '"baseline"'
    assert linking.parameter_value(500).endswith("#integer>")
    assert linking.parameter_value(0.85) == '"0.85"^^<http://www.w3.org/2001/XMLSchema#decimal>'


def test_ner_triples_missing_rule_sha256_drops_the_agent_everywhere(facts: run_files.RunFacts) -> None:
    without = run_files.RunFacts(**{**vars(facts), "rule_sha256": None})

    triples = ner.ner_triples(without)

    assert not any("t10_rule_baseline" in term for triple in triples for term in triple)
    assert len(triples) == len(ner.ner_triples(facts)) - 4  # two types, identifier, wasAssociatedWith


def test_ner_triples_other_model_revision_has_no_published_scores(facts: run_files.RunFacts) -> None:
    other = run_files.RunFacts(**{**vars(facts), "model_revision": "f" * 40})

    triples = ner.ner_triples(other)

    assert not any("publishedEvaluation" in predicate for _, predicate, _ in triples)
    assert any("modelVersion" in predicate for _, predicate, _ in triples)


def test_linking_triples_missing_setting_gives_no_value_but_keeps_the_parameter(facts: run_files.RunFacts) -> None:
    without = run_files.RunFacts(**{**vars(facts), "min_term_length": None})
    counts = linking.GraphCounts()
    list(linking.data_quads(rf.LINKS, rf.RUN_ID, counts))

    triples = linking.linking_triples(without, counts)

    assert not any("param-value:link_gnd_local" in s and "min-term-length" in s for s, _, _ in triples)
    assert any(s == "<urn:gemea:param:link_gnd_local:min-term-length>" for s, _, _ in triples)
