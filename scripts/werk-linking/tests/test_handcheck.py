"""Tests for handcheck.py, precision_by_path.py and the writer's hand-check options."""

from __future__ import annotations

import csv
from pathlib import Path

import handcheck
import precision_by_path as pbp
import pytest
import run_files
import run_folder as rf
import write_werk_rdf as wwr
from nquads import GRAPH_PROV, GRAPH_WORK

openpyxl = pytest.importorskip("openpyxl")
rdflib = pytest.importorskip("rdflib")

ORCID = "https://orcid.org/0000-0002-1825-0097"  # ORCID's own example ID
PROVCRED = "https://prov-cred.org/ns/"
LABELS = {rf.OBJ_A: ("1", "correct"), rf.OBJ_B: ("1", "unsure"), rf.OBJ_C: ("2-X", "wrong_work"), rf.OBJ_E: ("3", "related")}


@pytest.fixture(autouse=True)
def fixed_git(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run_files, "min_term_length", lambda repo, commit: 3)
    monkeypatch.setattr(wwr, "git_state", lambda: ("9" * 40, False))


def write_workbook(path: Path, labels: dict[str, tuple[str, str]] = LABELS) -> Path:
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "linked"
    sheet.append(["obj_id", "gnd_url", "label"])
    for obj_id, (gnd, label) in labels.items():
        sheet.append([obj_id, f"https://d-nb.info/gnd/{gnd}", label])
    sheet.append([None, None, None])
    book.save(path)
    return path


def precision_csv(tmp_path: Path, run: Path) -> Path:
    pbp.main(["--workbook", str(write_workbook(tmp_path / "sample.xlsx")), "--run-dir", str(run), "--date", "20261116"])
    return run / "werk_links_precision_by_path_20261116.csv"


def test_precision_by_path_counts_per_path_and_definition(tmp_path: Path) -> None:
    run = rf.make_run(tmp_path)

    with open(precision_csv(tmp_path, run), newline="", encoding="utf-8") as f:
        rows = {(r["path"], r["definition"]): (r["hits"], r["n"], r["excluded"], r["precision"]) for r in csv.DictReader(f)}

    assert rows[("rule-span", "strict")] == ("1", "3", "0", "0.3333")
    assert rows[("rule-span", "lenient")] == ("2", "3", "0", "0.6667")
    assert rows[("model-span", "strict")] == ("0", "0", "1", "")
    assert rows[("all", "strict")] == ("1", "3", "1", "0.3333")


def test_precision_by_path_sampled_link_to_another_work_stops(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    run = rf.make_run(tmp_path)
    book = write_workbook(tmp_path / "sample.xlsx", {**LABELS, rf.OBJ_A: ("999", "correct")})

    with pytest.raises(SystemExit) as stop:
        pbp.main(["--workbook", str(book), "--run-dir", str(run)])

    assert stop.value.code == 1
    assert "labelled for GND 999" in caplog.text


def test_precision_by_path_record_missing_from_run_stops(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    run = rf.make_run(tmp_path)
    book = write_workbook(tmp_path / "sample.xlsx", {**LABELS, "Z" * 32: ("1", "correct")})

    with pytest.raises(SystemExit):
        pbp.main(["--workbook", str(book), "--run-dir", str(run)])

    assert "1 sampled records are not in" in caplog.text


def test_precision_by_path_unknown_label_stops(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    run = rf.make_run(tmp_path)
    book = write_workbook(tmp_path / "sample.xlsx", {**LABELS, rf.OBJ_A: ("1", "")})

    with pytest.raises(SystemExit):
        pbp.main(["--workbook", str(book), "--run-dir", str(run)])

    assert "is not one of" in caplog.text


def test_precision_by_path_dry_run_and_overwrite_guard(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run = rf.make_run(tmp_path)
    book = write_workbook(tmp_path / "sample.xlsx")
    args = ["--workbook", str(book), "--run-dir", str(run), "--date", "20261116"]

    assert pbp.main([*args, "--dry-run"]) == 0
    assert not list(run.glob("werk_links_precision_by_path_*"))
    assert "rule-span,strict,1,3" in capsys.readouterr().out
    assert pbp.main(args) == 0
    with pytest.raises(SystemExit):
        pbp.main(args)


def test_handcheck_triples_for_one_path() -> None:
    row = handcheck.PathPrecision("rule-span", 80, 85, 0, "e" * 64)

    triples = handcheck.handcheck_triples([row], rf.RUN_ID, ORCID)

    graph = f"<{GRAPH_WORK}/{rf.RUN_ID}/rule-span>"
    score = f"<urn:gemea:evaluation:{rf.RUN_ID}:rule-span>"
    assert (graph, f"<{PROVCRED}measuredEvaluation>", score) in triples
    assert (score, "<http://www.w3.org/ns/mls#hasValue>", '"0.9412"^^<http://www.w3.org/2001/XMLSchema#decimal>') in triples
    assert (score, f"<{PROVCRED}sampleSize>", '"85"^^<http://www.w3.org/2001/XMLSchema#nonNegativeInteger>') in triples
    assert (score, "<http://www.w3.org/ns/prov#wasAttributedTo>", f"<{ORCID}>") in triples
    assert (f"<urn:gemea:precision-check:{'e' * 64}>", f"<{PROVCRED}refinementOrder>", '"2"^^<http://www.w3.org/2001/XMLSchema#integer>') in triples
    assert len(triples) == 22


def test_handcheck_triples_path_without_usable_links_is_left_out() -> None:
    assert handcheck.handcheck_triples([handcheck.PathPrecision("model-span", 0, 0, 1, "e" * 64)], rf.RUN_ID, ORCID) == []


def test_main_with_precision_adds_the_hand_check_of_rule_span_only(tmp_path: Path) -> None:
    run = rf.make_run(tmp_path)
    precision = precision_csv(tmp_path, run)

    code = wwr.main(["--run-dir", str(run), "--precision", str(precision), "--evaluator", ORCID])

    assert code == 0
    dataset = rdflib.Dataset()
    dataset.parse(run / f"prov-{rf.RUN_ID}.nq", format="nquads")
    prov = dataset.graph(rdflib.URIRef(GRAPH_PROV))
    measured = list(prov.subject_objects(rdflib.URIRef(PROVCRED + "measuredEvaluation")))
    assert measured == [
        (rdflib.URIRef(f"{GRAPH_WORK}/{rf.RUN_ID}/rule-span"), rdflib.URIRef(f"urn:gemea:evaluation:{rf.RUN_ID}:rule-span"))
    ]


@pytest.mark.parametrize("extra", [["--evaluator", ORCID], ["--precision", "x.csv"], ["--precision", "x.csv", "--evaluator", "ann"]])
def test_main_precision_and_evaluator_must_come_together_and_be_an_orcid(tmp_path: Path, extra: list[str]) -> None:
    run = rf.make_run(tmp_path)

    with pytest.raises(SystemExit) as stop:
        wwr.main(["--run-dir", str(run), *extra])

    assert stop.value.code == 2
