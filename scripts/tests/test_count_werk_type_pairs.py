# Purpose:      Tests for scripts/py/count_werk_type_pairs.py, with a small parquet and
#               N-Triples file written per test.
# Usage:        .venv/bin/python -m pytest scripts/tests/test_count_werk_type_pairs.py -q
# Dependencies: pytest, pyarrow

from __future__ import annotations

import csv
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "py"))
import count_werk_type_pairs as pairs

GND = "https://d-nb.info/standards/elementset/gnd#"
TYPE = "<http://www.w3.org/1999/02/22-rdf-syntax-ns#type>"
NT = (
    f"<https://d-nb.info/gnd/1> {TYPE} <{GND}Work> .\n"
    f"<https://d-nb.info/gnd/2> {TYPE} <{GND}MusicalWork> .\n"
    f"<https://d-nb.info/gnd/3> {TYPE} <{GND}Work> .\n"
    f"<https://d-nb.info/gnd/3> {TYPE} <{GND}MusicalWork> .\n"
    f'<https://d-nb.info/gnd/1> <{GND}preferredNameForTheWork> "Faust" .\n'
    f"<https://d-nb.info/gnd/4> <{GND}firstAuthor> <https://d-nb.info/gnd/118540238> .\n"
)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    """Inputs and an output folder, with argv pointing at them."""
    parquet = tmp_path / "in.parquet"
    pq.write_table(
        pa.table(
            {
                "hierarchy_type": ["htype_021", "htype_021", "htype_022", None, "htype_021", "htype_013"],
                "dc_type": ["Monografie", "Monografie", "Noten", "Noten", "Noten", None],
            }
        ),
        parquet,
    )
    nt = tmp_path / "werk.nt"
    nt.write_text(NT, encoding="utf-8")
    output = tmp_path / "out"
    argv = ["pairs", "--input", str(parquet), "--gnd-nt", str(nt), "--output-dir", str(output)]
    monkeypatch.setattr(sys, "argv", argv)
    return parquet, nt, output


def read(path: Path) -> list[list[str]]:
    with open(path, encoding="utf-8") as f:
        return list(csv.reader(f))


def test_count_pairs_counts_each_combination(setup):
    parquet, _, _ = setup
    counted = pairs.count_pairs(parquet)
    assert counted[("htype_021", "Monografie")] == 2
    assert counted[("htype_021", "Noten")] == 1
    assert counted[("", "Noten")] == 1
    assert counted[("htype_013", "")] == 1
    assert sum(counted.values()) == 6


def test_count_pairs_small_batches_give_same_counts(setup, monkeypatch):
    parquet, _, _ = setup
    whole = pairs.count_pairs(parquet)
    monkeypatch.setattr(pairs, "BATCH_SIZE", 2)
    assert pairs.count_pairs(parquet) == whole


def test_count_type_sets_joins_classes_per_subject(setup):
    _, nt, _ = setup
    assert pairs.count_type_sets(nt) == {"Work": 1, "MusicalWork": 1, "MusicalWork+Work": 1}


def test_pair_rows_add_group_and_sort_by_count(setup):
    parquet, _, _ = setup
    rows = pairs.pair_rows(pairs.count_pairs(parquet))
    assert rows[0] == ("htype_021", "work", "Monografie", 2)
    assert ("", "no_htype", "Noten", 1) in rows
    assert ("htype_022", "music", "Noten", 1) in rows


@pytest.mark.parametrize(
    "htype, dc_type, group",
    [
        ("", "Noten", "music"),
        ("htype_021", "notated music", "music"),
        ("htype_021", "Musikhandschrift", "music"),
        ("htype_022", "", "music"),
        ("htype_006", "notated music", "not_linked"),
        ("htype_021", "Libretto: Oper", "libretto"),
        ("htype_021", "Libretto", "libretto"),
        ("htype_021", "Lied", "song"),
        ("htype_013", "Handschrift", "manuscript"),
        ("htype_020", "mehrbändiges Werk", "multivolume"),
        ("htype_007 htype_020", "Band", "multivolume"),
        ("htype_021", "Monografie", "work"),
        ("", "", "ignored"),
        ("", "Handschrift", "ignored"),
        ("htype_099", "", "unassigned"),
    ],
)
def test_type_group_puts_dc_type_before_htype(htype, dc_type, group):
    assert pairs.type_group(htype, dc_type) == group


def test_group_rows_sum_records_and_name_classes(setup):
    parquet, _, _ = setup
    rows = pairs.group_rows(pairs.count_pairs(parquet))
    assert rows == [
        ("music", "MusicalWork VersionOfAMusicalWork", 3),
        ("work", "Work", 2),
        ("manuscript", "Manuscript CollectiveManuscript Work", 1),
    ]


def test_main_writes_all_files(setup):
    _, _, output = setup
    assert pairs.main() == 0
    assert read(output / pairs.PAIRS_FILE)[0] == ["hierarchy_type", "htype_group", "dc_type", "records"]
    assert read(output / pairs.TYPE_SETS_FILE)[1:] == [["MusicalWork", "1"], ["MusicalWork+Work", "1"], ["Work", "1"]]
    assert read(output / pairs.GROUPS_FILE)[0] == ["group", "gnd_classes", "records"]


def test_dry_run_writes_nothing(setup, monkeypatch):
    _, _, output = setup
    monkeypatch.setattr(sys, "argv", [*sys.argv, "--dry-run"])
    assert pairs.main() == 0
    assert not output.exists()


def test_existing_output_needs_force(setup, monkeypatch):
    assert pairs.main() == 0
    with pytest.raises(SystemExit) as stop:
        pairs.main()
    assert stop.value.code == 1
    monkeypatch.setattr(sys, "argv", [*sys.argv, "--force"])
    assert pairs.main() == 0


def test_missing_input_stops(setup, monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv", ["pairs", "--input", str(tmp_path / "none.parquet")])
    with pytest.raises(SystemExit) as stop:
        pairs.main()
    assert stop.value.code == 1


def test_missing_column_stops(tmp_path):
    parquet = tmp_path / "bad.parquet"
    pq.write_table(pa.table({"hierarchy_type": ["htype_021"]}), parquet)
    with pytest.raises(SystemExit) as stop:
        pairs.count_pairs(parquet)
    assert stop.value.code == 1
