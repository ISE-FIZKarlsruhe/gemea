# Purpose:      Tests for scripts/py/flair_work_of_art_probe.py, with a fake tagger;
#               neither flair nor a model is needed.
# Usage:        .venv/bin/python -m pytest scripts/tests/test_flair_work_of_art_probe.py -q
# Dependencies: pytest, pyarrow

from __future__ import annotations

import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "py"))
import flair_work_of_art_probe as probe

ROWS = [("a", "Faust von Goethe"), ("b", "Ohne Treffer"), ("c", None), ("d", "   "), ("e", "Werke")]


def fake_tag(texts):
    return [[("WORK_OF_ART", "Faust", 0.91234)] if "Faust" in text else [] for text in texts]


@pytest.fixture
def setup(tmp_path, monkeypatch):
    """A parquet and an output path, with argv pointing at them and a fake tagger."""
    parquet = tmp_path / "items.parquet"
    pq.write_table(pa.table({"obj_id": [r[0] for r in ROWS], "title": [r[1] for r in ROWS]}), parquet)
    output = tmp_path / "out" / "probe.csv"
    argv = ["probe", "--input", str(parquet), "--output", str(output), "--include-obj-id", "a"]
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(probe, "load_tagger", lambda model, batch_size: fake_tag)
    return parquet, output


def test_read_titles_skips_empty_titles(setup):
    parquet, _ = setup

    titles = probe.read_titles(parquet)

    assert [obj_id for obj_id, _ in titles] == ["a", "b", "e"]


def test_choose_sample_puts_included_record_first():
    titles = [(str(i), "t") for i in range(50)]

    sample = probe.choose_sample(titles, 5, 1, "7")

    assert sample[0][0] == "7"
    assert len(sample) == 6


def test_choose_sample_same_seed_gives_same_sample():
    titles = [(str(i), "t") for i in range(50)]

    assert probe.choose_sample(titles, 5, 1, "") == probe.choose_sample(titles, 5, 1, "")


def test_choose_sample_size_above_population_returns_all():
    titles = [("a", "t"), ("b", "t")]

    assert len(probe.choose_sample(titles, 10, 1, "")) == 2


def test_predict_rows_marks_titles_without_spans():
    rows = probe.predict_rows([("a", "Faust"), ("b", "Nichts")], fake_tag)

    assert rows == [["a", "Faust", "WORK_OF_ART", "Faust", 0.9123], ["b", "Nichts", "", "", ""]]


def test_label_counts_names_titles_without_spans():
    counts = probe.label_counts([["a", "t", "WORK_OF_ART", "s", 1.0], ["b", "t", "", "", ""]])

    assert counts == {"WORK_OF_ART": 1, "(no span)": 1}


def test_main_writes_one_row_per_span_or_empty_title(setup):
    _, output = setup

    assert probe.main() == 0

    lines = output.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "obj_id,title,label,span,score"
    assert lines[1] == "a,Faust von Goethe,WORK_OF_ART,Faust,0.9123"
    assert len(lines) == 4


def test_main_existing_output_without_force_exits_1(setup, capsys):
    _, output = setup
    output.parent.mkdir()
    output.write_text("keep", encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        probe.main()

    assert exc.value.code == 1
    assert "--force" in capsys.readouterr().err
    assert output.read_text(encoding="utf-8") == "keep"


def test_main_force_twice_gives_identical_output(setup, monkeypatch):
    _, output = setup
    monkeypatch.setattr(sys, "argv", [*sys.argv, "--force"])

    probe.main()
    first = output.read_bytes()
    probe.main()

    assert output.read_bytes() == first


def test_main_dry_run_loads_no_model_and_writes_nothing(setup, monkeypatch):
    _, output = setup
    monkeypatch.setattr(sys, "argv", [*sys.argv, "--dry-run"])

    def fail(model, batch_size):
        raise AssertionError("model loaded")

    monkeypatch.setattr(probe, "load_tagger", fail)

    assert probe.main() == 0

    assert not output.parent.exists()


def test_main_leaves_no_tmp_file(setup):
    _, output = setup

    probe.main()

    assert list(output.parent.glob("*.tmp")) == []


def test_main_missing_input_exits_1(setup, tmp_path, monkeypatch, capsys):
    _, output = setup
    argv = ["probe", "--input", str(tmp_path / "nope.parquet"), "--output", str(output)]
    monkeypatch.setattr(sys, "argv", argv)

    with pytest.raises(SystemExit) as exc:
        probe.main()

    assert exc.value.code == 1
    assert "not found" in capsys.readouterr().err


def test_main_parquet_without_title_exits_1(setup, capsys):
    parquet, _ = setup
    pq.write_table(pa.table({"obj_id": ["a"]}), parquet)

    with pytest.raises(SystemExit) as exc:
        probe.main()

    assert exc.value.code == 1
    assert "title" in capsys.readouterr().err


def test_main_sample_size_zero_exits_2(setup, monkeypatch):
    monkeypatch.setattr(sys, "argv", [*sys.argv, "--sample-size", "0"])

    with pytest.raises(SystemExit) as exc:
        probe.main()

    assert exc.value.code == 2


def test_main_unknown_flag_exits_2(setup, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["probe", "--nope"])

    with pytest.raises(SystemExit) as exc:
        probe.main()

    assert exc.value.code == 2


def test_shard_sample_shards_cover_sample_once():
    sample = [(str(i), "t") for i in range(7)]

    shards = [probe.shard_sample(sample, s, 2) for s in range(2)]

    assert sorted(shards[0] + shards[1]) == sorted(sample)
    assert not set(shards[0]) & set(shards[1])


def test_predict_rows_uses_batch_size():
    seen = []

    def tag(texts):
        seen.append(len(texts))
        return [[] for _ in texts]

    probe.predict_rows([(str(i), "t") for i in range(5)], tag, batch_size=2)

    assert seen == [2, 2, 1]


def test_main_shard_writes_only_its_titles(setup, monkeypatch):
    _, output = setup
    monkeypatch.setattr(sys, "argv", [*sys.argv, "--num-shards", "2", "--shard", "1"])

    assert probe.main() == 0

    ids = {line.split(",")[0] for line in output.read_text(encoding="utf-8").splitlines()[1:]}
    assert "a" not in ids
    assert ids


def test_main_passes_batch_size_to_tagger(setup, monkeypatch):
    seen = []
    monkeypatch.setattr(probe, "load_tagger", lambda model, batch_size: seen.append(batch_size) or fake_tag)
    monkeypatch.setattr(sys, "argv", [*sys.argv, "--batch-size", "64"])

    probe.main()

    assert seen == [64]


@pytest.mark.parametrize("extra", [["--shard", "2", "--num-shards", "2"], ["--num-shards", "0"], ["--batch-size", "0"], ["--shard", "-1"]])
def test_main_bad_shard_or_batch_exits_2(setup, monkeypatch, extra):
    monkeypatch.setattr(sys, "argv", [*sys.argv, *extra])

    with pytest.raises(SystemExit) as exc:
        probe.main()

    assert exc.value.code == 2
