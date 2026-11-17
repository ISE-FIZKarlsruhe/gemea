# Purpose:      Tests for scripts/py/isbd_probe_goethe_faust.py, with a small rule script
#               and parquet written per test; the real bibner-werk rule is not needed.
# Usage:        .venv/bin/python -m pytest scripts/tests/test_isbd_probe_goethe_faust.py -q
# Dependencies: pytest, pyarrow, rapidfuzz

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "py"))
import isbd_probe_goethe_faust as probe

RULE = """
def detect_flags(title):
    return " . - " in title, " :" in title, " /" in title


def extract_spans(title, has_dot_dash, f_other_title, f_person):
    end = title.find(" :") if f_other_title else len(title)
    return [(0, end, "TITLE")]
"""

ROWS = [
    ("a", "Faust : eine Tragödie", ["Goethe"]),
    ("b", "Faust / von Goethe", []),
    ("c", "Werke", None),
    ("d", None, ["Goethe"]),
    ("e", "1", []),
]

EXPECTED_CSV = (
    "metric,value\n"
    "distinct_terms,2\n"
    "has_colon,1\n"
    "has_dot_dash,0\n"
    "has_slash,1\n"
    "no_search_term,1\n"
    "no_title,1\n"
    "records,5\n"
    "records_with_top10_terms,3\n"
    "terms_seen_once,1\n"
    "with_dc_creator,2\n"
)


def write_parquet(path: Path, rows: list[tuple]) -> None:
    table = pa.table(
        {
            "obj_id": pa.array([r[0] for r in rows], pa.string()),
            "title": pa.array([r[1] for r in rows], pa.string()),
            "dc_creator": pa.array([r[2] for r in rows], pa.list_(pa.string())),
        }
    )
    pq.write_table(table, path)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    """A parquet, a rule script and an output path, with argv pointing at them."""
    parquet = tmp_path / "items.parquet"
    write_parquet(parquet, ROWS)
    rule = tmp_path / "rule.py"
    rule.write_text(RULE, encoding="utf-8")
    output = tmp_path / "out" / "probe.csv"
    argv = ["probe", "--input", str(parquet), "--rule-script", str(rule), "--output", str(output)]
    monkeypatch.setattr(sys, "argv", argv)
    return parquet, rule, output


def test_main_writes_expected_csv(setup):
    _, _, output = setup

    assert probe.main() == 0

    assert output.read_text(encoding="utf-8") == EXPECTED_CSV


def test_main_logs_spans_of_probe_record(setup, monkeypatch, caplog):
    monkeypatch.setattr(sys, "argv", [*sys.argv, "--probe-obj-id", "a"])
    caplog.set_level(logging.INFO, logger="isbd_probe")

    probe.main()

    assert "term: 'faust'" in caplog.text


def test_main_empty_parquet_writes_zero_counts(setup):
    parquet, _, output = setup
    write_parquet(parquet, [])

    assert probe.main() == 0

    assert "records,0\n" in output.read_text(encoding="utf-8")


def test_main_non_ascii_path_with_spaces_works(setup, tmp_path, monkeypatch):
    parquet, rule, _ = setup
    output = tmp_path / "Bände und Werke" / "probe.csv"
    argv = ["probe", "--input", str(parquet), "--rule-script", str(rule), "--output", str(output)]
    monkeypatch.setattr(sys, "argv", argv)

    assert probe.main() == 0

    assert output.read_text(encoding="utf-8") == EXPECTED_CSV


def test_main_existing_output_without_force_exits_1(setup, capsys):
    _, _, output = setup
    output.parent.mkdir()
    output.write_text("keep", encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        probe.main()

    assert exc.value.code == 1
    assert "--force" in capsys.readouterr().err
    assert output.read_text(encoding="utf-8") == "keep"


def test_main_force_twice_gives_identical_output(setup, monkeypatch):
    _, _, output = setup
    monkeypatch.setattr(sys, "argv", [*sys.argv, "--force"])

    probe.main()
    first = output.read_bytes()
    probe.main()

    assert output.read_bytes() == first


def test_main_dry_run_writes_nothing(setup, monkeypatch, tmp_path):
    _, _, output = setup
    monkeypatch.setattr(sys, "argv", [*sys.argv, "--dry-run"])

    assert probe.main() == 0

    assert not output.parent.exists()


def test_main_leaves_no_tmp_file(setup):
    _, _, output = setup

    probe.main()

    assert list(output.parent.glob("*.tmp")) == []


def test_main_missing_input_exits_1(setup, tmp_path, monkeypatch, capsys):
    _, rule, output = setup
    argv = ["probe", "--input", str(tmp_path / "nope.parquet"), "--rule-script", str(rule), "--output", str(output)]
    monkeypatch.setattr(sys, "argv", argv)

    with pytest.raises(SystemExit) as exc:
        probe.main()

    assert exc.value.code == 1
    assert "not found" in capsys.readouterr().err


def test_main_missing_rule_script_exits_1(setup, tmp_path, monkeypatch, capsys):
    parquet, _, output = setup
    argv = ["probe", "--input", str(parquet), "--rule-script", str(tmp_path / "nope.py"), "--output", str(output)]
    monkeypatch.setattr(sys, "argv", argv)

    with pytest.raises(SystemExit) as exc:
        probe.main()

    assert exc.value.code == 1
    assert "not found" in capsys.readouterr().err


def test_main_rule_script_without_functions_exits_1(setup, capsys):
    _, rule, _ = setup
    rule.write_text("x = 1\n", encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        probe.main()

    assert exc.value.code == 1
    assert "detect_flags" in capsys.readouterr().err


def test_main_parquet_without_dc_creator_exits_1(setup, capsys):
    parquet, _, _ = setup
    pq.write_table(pa.table({"obj_id": ["a"], "title": ["Faust"]}), parquet)

    with pytest.raises(SystemExit) as exc:
        probe.main()

    assert exc.value.code == 1
    assert "dc_creator" in capsys.readouterr().err


def test_main_unknown_flag_exits_2(setup, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["probe", "--nope"])

    with pytest.raises(SystemExit) as exc:
        probe.main()

    assert exc.value.code == 2
