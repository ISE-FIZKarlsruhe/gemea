# Purpose:      Tests for scripts/py/werk_links_precision.py, on small labelled workbooks written into
#               tmp_path.
# Usage:        .venv/bin/python -m pytest scripts/tests/test_werk_links_precision.py -q
# Dependencies: pytest, openpyxl

from __future__ import annotations

import csv
import math
import sys
from pathlib import Path

import pytest
from openpyxl import Workbook

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "py"))
import werk_links_precision as precision


def workbook(path: Path, rows: list[tuple[str | None, str, str | None]], sheet_name: str = "linked",
             notes: dict[str, str] | None = None, span_ok: dict[str, str | None] | None = None) -> Path:  # fmt: skip
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.title = sheet_name
    header = ["obj_id", "title", "type_group", "search_terms", "label", "note", None]
    if span_ok is not None:
        header[-1] = "span_ok"
    sheet.append(header)
    for obj_id, group, label in rows:
        note = (notes or {}).get(obj_id or "")
        ok = (span_ok or {}).get(obj_id or "")
        sheet.append([obj_id, f"Title {obj_id}" if obj_id else None, group, "faust", label, note, ok])
    book.save(path)
    return path


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    path = tmp_path / "data dir"
    path.mkdir()
    return path


def labelled(folder: Path) -> Path:
    rows = [(f"W{i}", "work", "correct") for i in range(6)]
    rows += [("W6", "work", "wrong_work"), ("M0", "music", "related"), ("M1", "music", "correct")]
    rows += [("U0", "work", "unsure"), (None, "", None), (None, "", None)]
    return workbook(folder / "labels.xlsx", rows)


def argv(folder: Path, book: Path, *extra: str) -> list[str]:
    return ["--workbook", str(book), "--out-dir", str(folder), "--date", "20261108", *extra]


def read_csv(path: Path) -> list[dict[str, str]]:
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


# --- Pure logic ---


def test_wilson_matches_a_known_interval():
    low, high = precision.wilson(91, 99)

    assert (round(low, 3), round(high, 3)) == (0.849, 0.958)


def test_wilson_stays_within_zero_and_one_at_the_edges():
    assert precision.wilson(10, 10)[1] == 1.0
    assert precision.wilson(0, 10)[0] == 0.0


def test_wilson_of_an_empty_group_is_nan():
    assert all(math.isnan(value) for value in precision.wilson(0, 0))


# --- Happy path ---


def test_main_writes_strict_and_lenient_precision_leaving_unsure_out(folder):
    assert precision.main(argv(folder, labelled(folder))) == 0

    rows = {(r["definition"], r["type_group"]): r for r in read_csv(folder / "werk_links_precision_20261108.csv")}
    strict, lenient = rows[("strict", "all")], rows[("lenient", "all")]
    assert (strict["hits"], strict["n"], strict["excluded"], strict["precision"]) == ("7", "9", "1", "0.7778")
    assert (lenient["hits"], lenient["precision"]) == ("8", "0.8889")
    assert rows[("lenient", "music")]["precision"] == "1.0000"


def test_main_counts_labels_per_type_group_and_in_total(folder):
    precision.main(argv(folder, labelled(folder)))

    rows = read_csv(folder / "werk_links_precision_labels_20261108.csv")
    counts = {(r["type_group"], r["label"]): int(r["records"]) for r in rows}
    assert counts[("all", "correct")] == 7
    assert counts[("work", "unsure")] == 1
    assert sum(v for (g, _), v in counts.items() if g == "all") == 10


def test_main_dry_run_writes_nothing(folder, capsys):
    assert precision.main(argv(folder, labelled(folder), "--dry-run")) == 0

    assert "10 labelled rows" in capsys.readouterr().out
    assert not list(folder.glob("*.csv"))


def test_main_refuses_to_overwrite_without_force(folder, capsys):
    book = labelled(folder)
    precision.main(argv(folder, book))

    with pytest.raises(SystemExit) as exc:
        precision.main(argv(folder, book))

    assert exc.value.code == 1
    assert "already exist" in capsys.readouterr().err
    assert precision.main(argv(folder, book, "--force")) == 0


def test_main_error_sheet_writes_cause_counts_and_noted_rows(folder):
    rows = [(f"C{i}", "work", "correct_rejection") for i in range(3)] + [("C3", "music", "author_role")]
    book = workbook(folder / "conflicts.xlsx", rows, "author_conflict", {"C1": "wrong title span"})

    assert precision.main(argv(folder, book, "--sheet", "author_conflict")) == 0

    causes = read_csv(folder / "werk_links_causes_author_conflict_20261108.csv")
    counts = {(r["type_group"], r["label"]): int(r["records"]) for r in causes}
    assert counts == {("all", "correct_rejection"): 3, ("all", "author_role"): 1,
                      ("music", "author_role"): 1, ("work", "correct_rejection"): 3}  # fmt: skip
    notes = read_csv(folder / "werk_links_notes_author_conflict_20261108.csv")
    assert [(r["obj_id"], r["title"], r["note"]) for r in notes] == [("C1", "Title C1", "wrong title span")]
    assert not (folder / "werk_links_precision_20261108.csv").exists()
    assert not (folder / "werk_links_span_ok_author_conflict_20261108.csv").exists()


def test_main_error_sheet_counts_span_ok_per_label(folder):
    rows = [("B0", "work", "not_in_gnd"), ("B1", "work", "not_in_gnd"), ("B2", "work", "wrong_span"),
            ("B3", "work", "other")]  # fmt: skip
    span_ok: dict[str, str | None] = {"B0": "y", "B1": "n", "B2": "n", "B3": "na"}
    book = workbook(folder / "below.xlsx", rows, "below_threshold", span_ok=span_ok)

    assert precision.main(argv(folder, book, "--sheet", "below_threshold")) == 0

    spans = read_csv(folder / "werk_links_span_ok_below_threshold_20261108.csv")
    assert [(r["label"], r["span_ok"], r["records"]) for r in spans] == [
        ("not_in_gnd", "y", "1"), ("not_in_gnd", "n", "1"), ("wrong_span", "n", "1"), ("other", "na", "1"),
        ("all", "y", "1"), ("all", "n", "2"), ("all", "na", "1"),
    ]  # fmt: skip


@pytest.mark.parametrize("value", [None, "yes"])
def test_main_blank_or_unknown_span_ok_exits_1(folder, capsys, value):
    span_ok: dict[str, str | None] = {"B0": "y", "B1": value}
    book = workbook(folder / "below.xlsx", [("B0", "work", "not_in_gnd"), ("B1", "work", "not_in_gnd")],
                    "below_threshold", span_ok=span_ok)  # fmt: skip

    with pytest.raises(SystemExit) as exc:
        precision.main(argv(folder, book, "--sheet", "below_threshold"))

    assert exc.value.code == 1
    assert "row 3 (B1): span_ok" in capsys.readouterr().err


def test_main_error_sheet_rejects_a_label_of_the_linked_sheet(folder, capsys):
    book = workbook(folder / "conflicts.xlsx", [("C0", "work", "correct")], "author_conflict")

    with pytest.raises(SystemExit) as exc:
        precision.main(argv(folder, book, "--sheet", "author_conflict"))

    assert exc.value.code == 1
    assert "row 2 (C0): correct" in capsys.readouterr().err


# --- Failure modes ---


@pytest.mark.parametrize("label", [None, "corect"])
def test_main_blank_or_unknown_label_exits_1(folder, capsys, label):
    book = workbook(folder / "labels.xlsx", [("W0", "work", "correct"), ("W1", "work", label)])

    with pytest.raises(SystemExit) as exc:
        precision.main(argv(folder, book))

    assert exc.value.code == 1
    assert "row 3 (W1)" in capsys.readouterr().err
    assert not list(folder.glob("*.csv"))


def test_main_workbook_without_linked_sheet_exits_1(folder, capsys):
    book = Workbook()
    book.save(folder / "other.xlsx")

    with pytest.raises(SystemExit) as exc:
        precision.main(argv(folder, folder / "other.xlsx"))

    assert exc.value.code == 1
    assert "no linked sheet" in capsys.readouterr().err


@pytest.mark.parametrize("extra", [["--date", "2026-11-08"], ["--bogus"]])
def test_main_bad_arguments_exit_2(folder, extra):
    with pytest.raises(SystemExit) as exc:
        precision.main(argv(folder, labelled(folder), *extra))

    assert exc.value.code == 2
