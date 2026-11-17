# Purpose:      Tests for scripts/py/count_werk_linking_types.py, with a small parquet and
#               N-Triples file written per test.
# Usage:        .venv/bin/python -m pytest scripts/tests/test_count_werk_linking_types.py -q
# Dependencies: pytest, pyarrow

from __future__ import annotations

import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "py"))
import count_werk_linking_types as counts

GND = "https://d-nb.info/standards/elementset/gnd#"
TYPE = "<http://www.w3.org/1999/02/22-rdf-syntax-ns#type>"
NT = (
    f"<https://d-nb.info/gnd/1> {TYPE} <{GND}Work> .\n"
    f"<https://d-nb.info/gnd/2> {TYPE} <{GND}MusicalWork> .\n"
    f"<https://d-nb.info/gnd/3> {TYPE} <{GND}Work> .\n"
    f'<https://d-nb.info/gnd/1> <{GND}preferredNameForTheWork> "Faust" .\n'
)


def write_parquet(path: Path) -> None:
    pq.write_table(
        pa.table(
            {
                "hierarchy_type": ["htype_021", "htype_014", "htype_014", None, "htype_007 htype_020", "htype_099"],
                "dc_type": ["Monografie", "Heft", "Heft", "", "Band", "Noten"],
            }
        ),
        path,
    )


@pytest.fixture
def setup(tmp_path, monkeypatch):
    """Inputs and an output folder, with argv pointing at them."""
    parquet = tmp_path / "in.parquet"
    write_parquet(parquet)
    nt = tmp_path / "werk.nt"
    nt.write_text(NT, encoding="utf-8")
    output = tmp_path / "out"
    argv = ["counts", "--input", str(parquet), "--gnd-nt", str(nt), "--output-dir", str(output)]
    monkeypatch.setattr(sys, "argv", argv)
    return parquet, nt, output


@pytest.mark.parametrize(
    "htype, group",
    [
        ("htype_021", "work"),
        ("htype_013", "manuscript"),
        ("htype_022", "music"),
        ("htype_014", "not_linked"),
        ("htype_011", "work"),
        ("htype_099", "unassigned"),
        (None, "no_htype"),
        ("", "no_htype"),
        ("htype_007 htype_020", "work"),
        ("htype_014 htype_021", "work"),
    ],
)
def test_htype_group_assigns_group(htype, group):
    assert counts.htype_group(htype) == group


def test_main_writes_group_counts(setup):
    _, _, output = setup

    assert counts.main() == 0

    assert (output / counts.GROUPS_FILE).read_text(encoding="utf-8") == (
        "group,records\nnot_linked,2\nwork,2\nno_htype,1\nunassigned,1\n"
    )


def test_main_writes_gnd_type_counts_and_ignores_other_lines(setup):
    _, _, output = setup

    counts.main()

    assert (output / counts.GND_FILE).read_text(encoding="utf-8") == "gnd_type,lines\nWork,2\nMusicalWork,1\n"


def test_main_writes_htype_and_dc_type_values(setup):
    _, _, output = setup

    counts.main()

    lines = (output / counts.VALUES_FILE).read_text(encoding="utf-8").splitlines()
    assert "hierarchy_type,htype_014,2" in lines
    assert "dc_type,Heft,2" in lines


def test_main_existing_output_without_force_exits_1(setup, capsys):
    _, _, output = setup
    output.mkdir()
    (output / counts.GND_FILE).write_text("keep", encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        counts.main()

    assert exc.value.code == 1
    assert "--force" in capsys.readouterr().err
    assert (output / counts.GND_FILE).read_text(encoding="utf-8") == "keep"


def test_main_force_twice_gives_identical_output(setup, monkeypatch):
    _, _, output = setup
    monkeypatch.setattr(sys, "argv", [*sys.argv, "--force"])

    counts.main()
    first = (output / counts.VALUES_FILE).read_bytes()
    counts.main()

    assert (output / counts.VALUES_FILE).read_bytes() == first


def test_main_dry_run_writes_nothing(setup, monkeypatch):
    _, _, output = setup
    monkeypatch.setattr(sys, "argv", [*sys.argv, "--dry-run"])

    assert counts.main() == 0

    assert not output.exists()


def test_main_leaves_no_tmp_file(setup):
    _, _, output = setup

    counts.main()

    assert list(output.glob("*.tmp")) == []


def test_main_missing_input_exits_1(setup, tmp_path, monkeypatch, capsys):
    _, nt, output = setup
    argv = ["counts", "--input", str(tmp_path / "no.parquet"), "--gnd-nt", str(nt), "--output-dir", str(output)]
    monkeypatch.setattr(sys, "argv", argv)

    with pytest.raises(SystemExit) as exc:
        counts.main()

    assert exc.value.code == 1
    assert "not found" in capsys.readouterr().err


def test_main_parquet_without_column_exits_1(setup, capsys):
    parquet, _, _ = setup
    pq.write_table(pa.table({"title": ["Faust"]}), parquet)

    with pytest.raises(SystemExit) as exc:
        counts.main()

    assert exc.value.code == 1
    assert "hierarchy_type" in capsys.readouterr().err


def test_main_empty_inputs_write_headers_only(setup):
    parquet, nt, output = setup
    pq.write_table(pa.table({"hierarchy_type": pa.array([], pa.string()), "dc_type": pa.array([], pa.string())}), parquet)
    nt.write_text("", encoding="utf-8")

    assert counts.main() == 0

    assert (output / counts.GND_FILE).read_text(encoding="utf-8") == "gnd_type,lines\n"


def test_main_unknown_flag_exits_2(setup, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["counts", "--nope"])

    with pytest.raises(SystemExit) as exc:
        counts.main()

    assert exc.value.code == 2
