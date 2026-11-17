# Purpose:      Tests for scripts/py/count_function_word_spans.py, on a small step 1 JSONL written into tmp_path.
# Usage:        .venv/bin/python -m pytest scripts/tests/test_count_function_word_spans.py -q
# Dependencies: pytest, pyarrow, rapidfuzz

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "py"))
import count_function_word_spans as count


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(json.dumps(record, ensure_ascii=False) + "\n" for record in records)


def read_csv(path: Path) -> list[dict[str, str]]:
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


RECORDS = [
    {"obj_id": "A", "title_spans": ["Faust", "der"], "span_sources": ["isbd_title", "flair"]},
    {"obj_id": "B", "title_spans": ["Die", "Die Räuber"], "span_sources": ["isbd_title", "flair"]},
    {"obj_id": "C", "title_spans": ["und die", "der"], "span_sources": ["flair", "flair"]},
    {"obj_id": "D", "title_spans": ["ab", "Werke"], "span_sources": ["isbd_title", "isbd_other_title"]},
    {"obj_id": "E", "title_spans": [], "span_sources": []},
]


@pytest.fixture
def jsonl(tmp_path: Path) -> Path:
    path = tmp_path / "werk_spans.jsonl"
    write_jsonl(path, RECORDS)
    return path


@pytest.mark.parametrize(
    "span, expected",
    [("der", "der"), ("Und die", "und die"), ("Die Räuber", None), ("Faust", None), ("ab", None), ("Der, Faust", "der")],
)
def test_function_word_term_uses_the_search_term(span, expected):
    assert count.function_word_term(span) == expected


def test_main_counts_spans_and_records_by_source(jsonl, tmp_path):
    assert count.main(["--input", str(jsonl), "--date", "20261109"]) == 0

    rows = {r["span_source"]: r for r in read_csv(tmp_path / "function_word_spans_by_source_20261109.csv")}
    assert rows["flair"] == {"span_source": "flair", "spans": "4", "records": "3",
                             "function_word_spans": "3", "function_word_records": "2"}  # fmt: skip
    assert rows["isbd_title"]["function_word_spans"] == "1"
    assert rows["isbd_other_title"]["function_word_spans"] == "0"
    assert rows["all"]["spans"] == "8"
    assert rows["all"]["records"] == "4"
    assert rows["all"]["function_word_records"] == "3"
    assert list(rows)[-1] == "all"


def test_main_lists_terms_most_frequent_first(jsonl, tmp_path):
    count.main(["--input", str(jsonl), "--date", "20261109"])

    rows = read_csv(tmp_path / "function_word_spans_by_term_20261109.csv")
    assert [(r["span_source"], r["term"], r["spans"], r["records"]) for r in rows] == [
        ("flair", "der", "2", "2"),
        ("flair", "und die", "1", "1"),
        ("isbd_title", "die", "1", "1"),
    ]


def test_mismatched_spans_and_sources_stop(tmp_path, capsys):
    path = tmp_path / "bad.jsonl"
    write_jsonl(path, [{"obj_id": "X", "title_spans": ["der"], "span_sources": []}])

    with pytest.raises(SystemExit) as exc:
        count.main(["--input", str(path)])
    assert exc.value.code == 1
    assert "line 1" in capsys.readouterr().err


def test_existing_outputs_stop_unless_forced(jsonl, tmp_path, capsys):
    count.main(["--input", str(jsonl), "--date", "20261109"])

    with pytest.raises(SystemExit) as exc:
        count.main(["--input", str(jsonl), "--date", "20261109"])
    assert exc.value.code == 1
    assert "--force" in capsys.readouterr().err
    assert count.main(["--input", str(jsonl), "--date", "20261109", "--force"]) == 0


def test_missing_input_stops(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        count.main(["--input", str(tmp_path / "missing.jsonl")])
    assert exc.value.code == 1
    assert "not found" in capsys.readouterr().err


def test_dry_run_writes_nothing(jsonl, tmp_path, capsys):
    assert count.main(["--input", str(jsonl), "--date", "20261109", "--dry-run"]) == 0

    assert "would write" in capsys.readouterr().out
    assert not list(tmp_path.glob("function_word_spans_*.csv"))


def test_bad_date_exits_2(jsonl):
    with pytest.raises(SystemExit) as exc:
        count.main(["--input", str(jsonl), "--date", "2026-11-09"])
    assert exc.value.code == 2
