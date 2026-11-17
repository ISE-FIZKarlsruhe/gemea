"""Tests for write_werk_rdf.py: the command, end to end on a made-up run folder."""

from __future__ import annotations

import json
from pathlib import Path

import ner
import pytest
import run_files
import run_folder as rf
import write_werk_rdf as wwr
from nquads import GRAPH_PROV, GRAPH_WORK, RDFS

rdflib = pytest.importorskip("rdflib")

TEMPLATE = Path(__file__).parent / "fixtures" / "expected_prov_template.trig"
VALUES = {
    "RULE_SPAN_TRIPLES": str(rf.RULE_SPAN_TRIPLES),
    "MODEL_SPAN_TRIPLES": str(rf.MODEL_SPAN_TRIPLES),
    "DISTINCT_TRIPLES": str(rf.DISTINCT_TRIPLES),
    "INPUT_SHA256": rf.INPUT_SHA256,
    "TABLES_SHA256": rf.TABLES_SHA256,
    "RULE_SHA256": rf.RULE_SHA256,
    "HF_REVISION": rf.HF_REVISION,
    "FLAIR_VERSION": "0.15.1",
    "DOWNLOAD_DATE": "2026-11-06",
    "DUMP_DATE": "2026-10-13",
    "STEP1_START": "2026-11-09T14:44:27Z",
    "STEP1_END": "2026-11-09T21:34:07Z",
    "RUN_START": "2026-11-10T09:02:05Z",
    "LAST_PART_END": "2026-11-10T09:58:53Z",
    "STEP1_ID": rf.STEP1_ID,
    "RUN_ID": rf.RUN_ID,
    "CARD_F1": "0.9093",
    "WOA_F1": "0.7087",
    '"MIN_TERM_LENGTH"': "__LABEL__",  # the parameter's label, not a placeholder
    "COMMIT": rf.COMMIT,
    "__LABEL__": '"MIN_TERM_LENGTH"',
}


@pytest.fixture(autouse=True)
def fixed_git(monkeypatch: pytest.MonkeyPatch) -> None:
    """The tests do not depend on the checkout's git state or history."""
    monkeypatch.setattr(run_files, "min_term_length", lambda repo, commit: 3)
    monkeypatch.setattr(wwr, "git_state", lambda: ("9" * 40, False))


def expected_prov() -> set[tuple[object, object, object]]:
    """The template's graph:prov filled with the run folder's values, plus the two sourced comments."""
    text = TEMPLATE.read_text(encoding="utf-8")
    for placeholder, value in VALUES.items():
        text = text.replace(placeholder, value)
    dataset = rdflib.Dataset()
    dataset.parse(data=text, format="trig")
    triples = set(dataset.graph(rdflib.URIRef(GRAPH_PROV)))
    for name, _, comment in ner.PUBLISHED_SCORES[rf.HF_REVISION]:
        score = rdflib.URIRef(f"urn:gemea:evaluation:{ner.MODEL_SLUG}:{rf.HF_REVISION}:{name}")
        triples.add((score, rdflib.URIRef(RDFS + "comment"), rdflib.Literal(comment, lang="en")))
    return triples


def parse_nquads(path: Path) -> rdflib.Dataset:
    dataset = rdflib.Dataset()
    dataset.parse(path, format="nquads")
    return dataset


def test_main_writes_provenance_equal_to_the_adr_trig(tmp_path: Path) -> None:
    run = rf.make_run(tmp_path)

    code = wwr.main(["--run-dir", str(run)])

    assert code == 0
    written = set(parse_nquads(run / f"prov-{rf.RUN_ID}.nq").graph(rdflib.URIRef(GRAPH_PROV)))
    expected = expected_prov()
    assert written - expected == set()
    assert expected - written == set()


def test_main_writes_three_data_graphs_with_the_counted_sizes(tmp_path: Path) -> None:
    run = rf.make_run(tmp_path)

    wwr.main(["--run-dir", str(run)])

    data = parse_nquads(run / f"gnd-work-links-{rf.RUN_ID}.nq")
    sizes = {str(g.identifier): len(g) for g in data.graphs() if len(g)}
    assert sizes == {
        GRAPH_WORK: rf.DISTINCT_TRIPLES,
        f"{GRAPH_WORK}/{rf.RUN_ID}/rule-span": rf.RULE_SPAN_TRIPLES,
        f"{GRAPH_WORK}/{rf.RUN_ID}/model-span": rf.MODEL_SPAN_TRIPLES,
    }
    record = json.loads((run / f"werk-rdf-{rf.RUN_ID}.json").read_text(encoding="utf-8"))
    assert record["triples_per_graph"][GRAPH_WORK] == rf.DISTINCT_TRIPLES
    assert record["gaps"] == []
    assert record["link_rows"] == {"model-span": 1, "rule-span": 3}


def test_main_second_run_gives_identical_bytes(tmp_path: Path) -> None:
    run = rf.make_run(tmp_path)
    names = [f"gnd-work-links-{rf.RUN_ID}.nq", f"prov-{rf.RUN_ID}.nq"]

    wwr.main(["--run-dir", str(run)])
    first = [(run / name).read_bytes() for name in names]
    wwr.main(["--run-dir", str(run), "--force"])

    assert [(run / name).read_bytes() for name in names] == first


def test_main_existing_output_stops_without_force(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    run = rf.make_run(tmp_path)
    wwr.main(["--run-dir", str(run)])

    with pytest.raises(SystemExit) as stop:
        wwr.main(["--run-dir", str(run)])

    assert stop.value.code == 1
    assert "--force" in caplog.text


def test_main_dry_run_writes_nothing(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    run = rf.make_run(tmp_path)
    before = sorted(p.name for p in run.iterdir())
    caplog.set_level("INFO")

    code = wwr.main(["--run-dir", str(run), "--dry-run"])

    assert code == 0
    assert sorted(p.name for p in run.iterdir()) == before
    assert f"{GRAPH_WORK}: {rf.DISTINCT_TRIPLES} triples" in caplog.text


def test_main_out_dir_keeps_the_run_folder_untouched(tmp_path: Path) -> None:
    run = rf.make_run(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    before = sorted(p.name for p in run.iterdir())

    wwr.main(["--run-dir", str(run), "--out-dir", str(out)])

    assert sorted(p.name for p in run.iterdir()) == before
    assert len(list(out.iterdir())) == 3


def test_main_missing_value_gives_gap_and_no_triple(tmp_path: Path) -> None:
    run = rf.make_run(tmp_path)
    rf.change_json(run / "provcred_record.json", lambda r: r["g_flair_torch_versions"].update(status="missing", value=None))

    wwr.main(["--run-dir", str(run)])

    record = json.loads((run / f"werk-rdf-{rf.RUN_ID}.json").read_text(encoding="utf-8"))
    assert record["gaps"] == ["flair_version"]
    assert "urn:gemea:software:flair" not in (run / f"prov-{rf.RUN_ID}.nq").read_text(encoding="utf-8")


def test_main_malformed_row_stops_and_leaves_no_output(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    links = [rf.LINKS[0], {**rf.LINKS[1], "matched_span_source": "ocr"}]
    run = rf.make_run(tmp_path, links)
    before = sorted(p.name for p in run.iterdir())

    with pytest.raises(SystemExit):
        wwr.main(["--run-dir", str(run)])

    assert "line 3" in caplog.text
    assert sorted(p.name for p in run.iterdir()) == before


def test_main_changed_links_file_stops(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    run = rf.make_run(tmp_path)
    with open(run / "werk_links.csv", "a", encoding="utf-8") as f:
        f.write("\n")

    with pytest.raises(SystemExit):
        wwr.main(["--run-dir", str(run)])

    assert "SHA-256 of werk_links_merge.json" in caplog.text


def test_main_linked_count_differs_from_merge_record_stops(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    run = rf.make_run(tmp_path)
    rf.change_json(run / "werk_links_merge.json", lambda m: m["output"]["statuses"].update(linked=5))

    with pytest.raises(SystemExit):
        wwr.main(["--run-dir", str(run)])

    assert "says 5" in caplog.text


def test_main_score_mode_run_stops(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    run = rf.make_run(tmp_path)
    rf.change_json(run / "werk_links_merge.json", lambda m: m["settings"].update(decision="score"))

    with pytest.raises(SystemExit):
        wwr.main(["--run-dir", str(run)])

    assert "only baseline" in caplog.text


def write_counts(path: Path, model_triples: int) -> None:
    path.write_text(
        "graph,linked_rows,triples\n"
        f"rule-span,3,{rf.RULE_SPAN_TRIPLES}\nmodel-span,1,{model_triples}\nall,4,{rf.DISTINCT_TRIPLES}\n",
        encoding="utf-8",
    )


def test_main_counts_file_that_agrees_passes(tmp_path: Path) -> None:
    run = rf.make_run(tmp_path)
    counts = tmp_path / "counts.csv"
    write_counts(counts, rf.MODEL_SPAN_TRIPLES)

    assert wwr.main(["--run-dir", str(run), "--counts", str(counts)]) == 0


def test_main_counts_file_that_differs_stops_without_provenance(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    run = rf.make_run(tmp_path)
    counts = tmp_path / "counts.csv"
    write_counts(counts, rf.MODEL_SPAN_TRIPLES + 1)

    with pytest.raises(SystemExit):
        wwr.main(["--run-dir", str(run), "--counts", str(counts)])

    assert "model-span" in caplog.text
    assert not list(run.glob("*.nq"))


def test_main_missing_input_stops(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    run = rf.make_run(tmp_path)
    (run / "provcred_record.json").unlink()

    with pytest.raises(SystemExit) as stop:
        wwr.main(["--run-dir", str(run)])

    assert stop.value.code == 1
    assert "provcred_record.json not found" in caplog.text


def test_main_uncommitted_changes_stop_unless_allowed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = rf.make_run(tmp_path)
    monkeypatch.setattr(wwr, "git_state", lambda: ("9" * 40, True))

    with pytest.raises(SystemExit):
        wwr.main(["--run-dir", str(run)])

    assert wwr.main(["--run-dir", str(run), "--allow-dirty"]) == 0


def test_main_run_dir_that_is_no_folder_is_a_usage_error(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as stop:
        wwr.main(["--run-dir", str(tmp_path / "missing")])

    assert stop.value.code == 2
