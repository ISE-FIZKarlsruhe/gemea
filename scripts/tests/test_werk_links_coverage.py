# Purpose:      Tests for scripts/py/werk_links_coverage.py, on a small links CSV written into tmp_path.
# Usage:        .venv/bin/python -m pytest scripts/tests/test_werk_links_coverage.py -q
# Dependencies: pytest, pyarrow, rapidfuzz

from __future__ import annotations

import csv
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "py"))
import link_gnd_local as local
import werk_links_coverage as coverage


def link_row(obj_id: str, status: str, group: str = "work", **fields: str) -> dict[str, str]:
    row = dict.fromkeys(local.OUTPUT_COLUMNS, "")
    row.update(obj_id=obj_id, status=status, type_group=group, candidate_count="0", truncated="False")
    row.update(fields)
    return row


def write_csv(path: Path, columns: list[str], rows: list[dict[str, Any]]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def read(path: Path) -> list[dict[str, str]]:
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


@pytest.fixture
def links(tmp_path: Path) -> Path:
    folder = tmp_path / "nerl gnd"
    folder.mkdir()
    path = folder / "werk_links.csv"
    write_csv(path, local.OUTPUT_COLUMNS, [
        link_row("a1", "linked", candidate_count="1", author_source="dc_creator",
                 matched_span="Faust", matched_span_source="isbd_title"),
        link_row("a2", "linked", group="music", candidate_count="3", author_source="dc_creator",
                 matched_span="Die", matched_span_source="flair"),
        link_row("a3", "linked", candidate_count="1", author_source="dc_contributor",
                 matched_span="Faust", matched_span_source="isbd_title"),
        link_row("a4", "no_candidate"),
        link_row("a5", "generic_title", candidate_count="501", truncated="True"),
    ])  # fmt: skip
    return path


def argv(links: Path, *extra: str) -> list[str]:
    return ["--input", str(links), "--date", "20261108", *extra]


def output(links: Path, name: str) -> Path:
    return links.parent / f"werk_links_coverage_{name}_20261108.csv"


# --- Pure logic ---


@pytest.mark.parametrize(
    ("count", "label"),
    [("0", "0"), ("1", "1"), ("2", "2-10"), ("10", "2-10"), ("11", "11-100"), ("500", "101-500"), ("501", ">500")],
)
def test_candidate_bin_puts_bounds_in_the_lower_bin(count, label):
    assert coverage.candidate_bin(count) == label


@pytest.mark.parametrize(
    ("span", "expected"), [("Die", True), ("der und die", True), ("Die Räuber", False), ("", False)]
)
def test_function_words_only_needs_every_word_in_the_list(span, expected):
    assert coverage.function_words_only(span) is expected


# --- Happy path ---


def test_main_counts_status_per_type_group_with_totals(links):
    assert coverage.main(argv(links)) == 0

    rows = {(r["value"], r["status"]): r for r in read(output(links, "by_type_group"))}
    assert rows[("work", "linked")]["records"] == "2"
    assert rows[("work", "linked")]["percent"] == "50.0"
    assert rows[("work", "all")]["records"] == "4"
    assert rows[("all", "linked")]["records"] == "3"
    assert rows[("all", "linked")]["percent"] == "60.0"
    assert rows[("all", "all")]["records"] == "5"


def test_main_counts_empty_sources_as_none(links):
    coverage.main(argv(links))

    rows = {(r["value"], r["status"]): r["records"] for r in read(output(links, "by_author_source"))}
    assert rows[("(none)", "no_candidate")] == "1"
    assert rows[("dc_contributor", "linked")] == "1"
    spans = {(r["value"], r["status"]) for r in read(output(links, "by_matched_span_source"))}
    assert ("flair", "linked") in spans


def test_main_bins_candidates_and_counts_truncated(links):
    coverage.main(argv(links))

    bins = {(r["value"], r["status"]): r["records"] for r in read(output(links, "by_candidate_bin"))}
    assert bins[("1", "linked")] == "2"
    assert bins[(">500", "generic_title")] == "1"
    truncated = {(r["value"], r["status"]): r["records"] for r in read(output(links, "by_truncated"))}
    assert truncated[("True", "generic_title")] == "1"


def test_main_lists_linked_spans_most_frequent_first_with_the_function_word_flag(links):
    coverage.main(argv(links))

    rows = read(output(links, "linked_spans"))
    assert [(r["matched_span"], r["records"], r["function_words_only"]) for r in rows] == [
        ("Faust", "2", "False"),
        ("Die", "1", "True"),
    ]


def test_main_checks_the_type_groups_against_the_linker_summary(links):
    summary = local.side_outputs(links)[0]
    write_csv(summary, local.SUMMARY_COLUMNS, [
        {"type_group": "work", "status": "linked", "records": 2, "high_confidence": 2},
        {"type_group": "work", "status": "no_candidate", "records": 1, "high_confidence": 0},
        {"type_group": "work", "status": "generic_title", "records": 1, "high_confidence": 0},
        {"type_group": "music", "status": "linked", "records": 1, "high_confidence": 1},
        {"type_group": "all", "status": "linked", "records": 3, "high_confidence": 3},
    ])  # fmt: skip

    assert coverage.main(argv(links)) == 0


def test_main_dry_run_writes_nothing(links, capsys):
    assert coverage.main(argv(links, "--dry-run")) == 0

    assert "would write" in capsys.readouterr().out
    assert not output(links, "by_type_group").exists()


def test_main_refuses_to_overwrite_without_force(links, capsys):
    coverage.main(argv(links))

    with pytest.raises(SystemExit) as exc:
        coverage.main(argv(links))

    assert exc.value.code == 1
    assert "--force" in capsys.readouterr().err
    assert coverage.main(argv(links, "--force")) == 0


# --- Failure modes ---


def test_main_summary_that_disagrees_exits_1(links, capsys):
    summary = local.side_outputs(links)[0]
    write_csv(summary, local.SUMMARY_COLUMNS,
              [{"type_group": "work", "status": "linked", "records": 9, "high_confidence": 9}])  # fmt: skip

    with pytest.raises(SystemExit) as exc:
        coverage.main(argv(links))

    assert exc.value.code == 1
    assert "differ" in capsys.readouterr().err


def test_main_other_columns_exit_1(tmp_path, capsys):
    path = tmp_path / "werk_links.csv"
    path.write_text("obj_id,status\na1,linked\n", encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        coverage.main(argv(path))

    assert exc.value.code == 1
    assert "not those of link_gnd_local.py" in capsys.readouterr().err


def test_main_missing_input_exits_1(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        coverage.main(argv(tmp_path / "absent.csv"))

    assert exc.value.code == 1
    assert "not found" in capsys.readouterr().err


@pytest.mark.parametrize("extra", [["--date", "2026-11-08"], ["--bogus"]])
def test_main_bad_arguments_exit_2(links, extra):
    with pytest.raises(SystemExit) as exc:
        coverage.main(["--input", str(links), *extra])

    assert exc.value.code == 2
