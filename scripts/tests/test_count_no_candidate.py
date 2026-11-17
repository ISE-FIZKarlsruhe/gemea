# Purpose:      Tests for scripts/py/count_no_candidate.py, on a small links CSV written into tmp_path.
# Usage:        .venv/bin/python -m pytest scripts/tests/test_count_no_candidate.py -q
# Dependencies: pytest, pyarrow, rapidfuzz

from __future__ import annotations

import csv
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "py"))
import count_no_candidate as nc
import link_gnd_local as local


def link_row(obj_id: str, status: str, search_terms: str, type_group: str = "work") -> dict[str, str]:
    row = dict.fromkeys(local.OUTPUT_COLUMNS, "")
    row.update(obj_id=obj_id, status=status, search_terms=search_terms, type_group=type_group)
    return row


@pytest.fixture
def links(tmp_path: Path) -> Path:
    folder = tmp_path / "rerun dir"
    folder.mkdir()
    path = folder / "werk_links.csv"
    rows = [
        link_row("A", "no_candidate", "einzelne ereignisse august 1848"),
        link_row("B", "no_candidate", "", type_group="manuscript"),
        link_row("C", "no_candidate", "tgl 21130 | lieder | görlitz | bericht | x"),
        link_row("D", "linked", "faust"),
    ]
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=local.OUTPUT_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return path


def read(path: Path) -> list[dict[str, str]]:
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


# --- Pure logic ---


@pytest.mark.parametrize("terms, expected", [
    ("", {"search_terms": "empty", "term_count": "0", "first_term_tokens": "0"}),
    ("faust", {"search_terms": "present", "term_count": "1", "first_term_tokens": "1"}),
    ("a b c d e | x | y | z", {"search_terms": "present", "term_count": "4+", "first_term_tokens": "4+"}),
])  # fmt: skip
def test_describe_bins_terms_and_tokens(terms, expected):
    result = nc.describe(link_row("X", "no_candidate", terms))

    assert {k: result[k] for k in expected} == expected


# --- Happy path ---


def test_main_counts_only_no_candidate_rows(links):
    assert nc.main(["--input", str(links), "--date", "20261109"]) == 0

    out = read(links.parent / "no_candidate_breakdown_20261109.csv")
    by_key = {(r["dimension"], r["value"]): r for r in out}
    assert by_key[("all", "no_candidate")]["records"] == "3"
    assert by_key[("search_terms", "empty")]["records"] == "1"
    assert by_key[("search_terms", "empty")]["percent"] == "33.33"
    assert by_key[("first_term_tokens", "4+")]["records"] == "1"
    assert by_key[("type_group", "manuscript")]["records"] == "1"
    assert ("type_group", "(none)") not in by_key


def test_main_dry_run_writes_nothing(links, capsys):
    assert nc.main(["--input", str(links), "--date", "20261109", "--dry-run"]) == 0

    assert "would write" in capsys.readouterr().out
    assert not (links.parent / "no_candidate_breakdown_20261109.csv").exists()


def test_main_refuses_to_overwrite_without_force(links, capsys):
    nc.main(["--input", str(links), "--date", "20261109"])

    with pytest.raises(SystemExit) as exc:
        nc.main(["--input", str(links), "--date", "20261109"])

    assert exc.value.code == 1
    assert "--force" in capsys.readouterr().err
    assert nc.main(["--input", str(links), "--date", "20261109", "--force"]) == 0


# --- Failure modes ---


def test_main_missing_input_exits_1(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        nc.main(["--input", str(tmp_path / "nope.csv")])

    assert exc.value.code == 1
    assert "not found" in capsys.readouterr().err


def test_main_links_with_other_columns_exit_1(links, capsys):
    links.write_text("obj_id,status\nA,no_candidate\n", encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        nc.main(["--input", str(links), "--date", "20261109"])

    assert exc.value.code == 1
    assert "not those of link_gnd_local.py" in capsys.readouterr().err
    assert not list(links.parent.glob("*.tmp"))


@pytest.mark.parametrize("extra", [["--date", "2026-11-09"], ["--bogus"]])
def test_main_bad_arguments_exit_2(links, extra):
    with pytest.raises(SystemExit) as exc:
        nc.main(["--input", str(links), *extra])

    assert exc.value.code == 2
