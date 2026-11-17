"""Tests for count_nq.py."""

from __future__ import annotations

import csv
from pathlib import Path

import count_nq
import pytest

TYPE = count_nq.RDF_TYPE
MO = count_nq.IS_MANIFESTATION_OF
WORK = count_nq.GRAPH_WORK
RULE = "<https://gemea.ise.fiz-karlsruhe.de/graph/work/R/rule-span>"
MODEL = "<https://gemea.ise.fiz-karlsruhe.de/graph/work/R/model-span>"
MANIFESTATION = "<https://ise-fizkarlsruhe.github.io/ddbkg/mocho#Manifestation>"


def quads() -> list[str]:
    lines = []
    for graph, item, work in [(RULE, "a", "1"), (MODEL, "b", "1"), (RULE, "c", "2")]:
        for target in (graph, WORK):
            lines.append(f"<http://x/{item}> {TYPE} {MANIFESTATION} {target} .\n")
            lines.append(f"<http://x/{item}> {MO} <http://w/{work}> {target} .\n")
    lines.append(f'<http://w/1> <http://x/label> "a . b" {WORK} .\n')
    return lines


def rows(tmp_path: Path, lines: list[str]) -> dict[tuple[str, str, str], int]:
    source = tmp_path / "in.nq"
    source.write_text("".join(lines), encoding="utf-8")
    output = tmp_path / "out.csv"
    count_nq.main(["--input", str(source), "--output", str(output)])
    with open(output, newline="", encoding="utf-8") as f:
        return {(r["graph"], r["measure"], r["key"]): int(r["count"]) for r in csv.DictReader(f)}


def test_counts_triples_classes_and_literals_per_graph(tmp_path: Path) -> None:
    table = rows(tmp_path, quads())

    assert table[(WORK, "triples", "")] == 7
    assert table[(RULE, "triples", "")] == 4
    assert table[(WORK, "class", MANIFESTATION)] == 3
    assert table[(WORK, "predicate", MO)] == 3
    assert table[(WORK, "literal_objects", "")] == 1
    assert table[("all", "triples", "")] == 13


def test_works_shared_by_paths_and_records_per_work(tmp_path: Path) -> None:
    table = rows(tmp_path, quads())

    assert table[("all", "works_in_graphs", "model-span + rule-span")] == 1
    assert table[("all", "works_in_graphs", "rule-span")] == 1
    assert table[(WORK, "records_per_work", "1")] == 1
    assert table[(WORK, "records_per_work", "2-5")] == 1
    assert table[(WORK, "max_records_per_work", "<http://w/1>")] == 2


def test_default_graph_line_stops(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    source = tmp_path / "in.nq"
    source.write_text(f"<http://x/a> {TYPE} {MANIFESTATION} .\n", encoding="utf-8")

    with pytest.raises(SystemExit) as stop:
        count_nq.main(["--input", str(source), "--output", str(tmp_path / "out.csv")])

    assert stop.value.code == 1
    assert "line 1" in caplog.text


def test_dry_run_writes_nothing_and_existing_output_stops(tmp_path: Path) -> None:
    source = tmp_path / "in.nq"
    source.write_text("".join(quads()), encoding="utf-8")
    output = tmp_path / "out.csv"

    assert count_nq.main(["--input", str(source), "--output", str(output), "--dry-run"]) == 0
    assert not output.exists()
    count_nq.main(["--input", str(source), "--output", str(output)])
    with pytest.raises(SystemExit):
        count_nq.main(["--input", str(source), "--output", str(output)])
