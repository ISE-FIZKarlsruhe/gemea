# Purpose:      Tests for scripts/py/extract_werk_spans.py, step 1 of Werk linking, with a
#               small parquet written per test, the real bibner-werk ISBD rule (skipped if the
#               sibling repo is missing), a fake Flair tagger and a faked git state.
# Usage:        .venv/bin/python -m pytest scripts/tests/test_extract_werk_spans.py -q
# Dependencies: pytest, pyarrow, rapidfuzz

from __future__ import annotations

import json
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "py"))
import extract_werk_spans as spans
import werk_link_rules

pytestmark = pytest.mark.skipif(not spans.DEFAULT_RULE_SCRIPT.is_file(), reason="bibner-werk rule not found")

FAUST_TITLE = "Goethe's sämmtliche Werke : in vierzig Bänden, 12. Faust, 2. Theil. - 1840. - 310 S. : Ill."
GOETHE = {"label": "Goethe, Johann Wolfgang von", "uri": "http://d-nb.info/gnd/118540238"}
AUTHOR = pa.list_(pa.struct([("label", pa.string()), ("uri", pa.string())]))
ROWS = [
    ("faust", FAUST_TITLE, "htype_007", "text", "ger", [GOETHE], []),
    ("heft", "Heft 3", "htype_014", "Heft", "ger", [], []),
    ("noten", "Lieder ohne Worte / Mendelssohn", None, "Noten", "ger", [], [{"label": "Mendelssohn (Komponist)", "uri": None}]),
    ("blank", None, "htype_021", "Monografie", "ger", [], []),
    ("none", "Ohne Typ", None, None, "ger", [], []),
    ("hamlet", "Hamlet", "htype_021", "Monografie", "ger", [], []),
]


def write_parquet(path: Path, rows=ROWS) -> None:
    columns = list(zip(*rows))
    pq.write_table(
        pa.table(
            {
                "obj_id": pa.array(columns[0], pa.string()),
                "title": pa.array(columns[1], pa.string()),
                "hierarchy_type": pa.array(columns[2], pa.string()),
                "dc_type": pa.array(columns[3], pa.string()),
                "lang": pa.array(columns[4], pa.string()),
                "dc_creator": pa.array(columns[5], AUTHOR),
                "dc_contributor": pa.array(columns[6], AUTHOR),
            }
        ),
        path,
        row_group_size=4,
    )


def fake_tag(texts):
    """WORK_OF_ART for the last word of each title, PERSON for 'Mendelssohn'."""
    result = []
    for text in texts:
        predictions = [("WORK_OF_ART", text.split()[-1], 0.9)]
        if "Mendelssohn" in text:
            predictions.append(("PERSON", "Mendelssohn", 0.9))
        result.append(predictions)
    return result


@pytest.fixture
def rule():
    return spans.load_rule(spans.DEFAULT_RULE_SCRIPT)


@pytest.fixture
def cli(tmp_path, monkeypatch):
    """Runs main() on a small parquet, with a fake tagger and a clean git state."""
    write_parquet(tmp_path / "in.parquet")
    calls = []

    def load_tagger(model, batch_size):
        calls.append(model)
        return fake_tag

    monkeypatch.setattr(spans, "load_tagger", load_tagger)
    monkeypatch.setattr(spans, "git_state", lambda: ("abc123", False))
    monkeypatch.setattr(spans, "model_caches", lambda: [{"path": "/m", "revision": "r1", "download_date": "2026-11-06"}])

    def run_main(*extra):
        argv = ["x", "--input", str(tmp_path / "in.parquet"), "--output-dir", str(tmp_path / "parts"),
                "--output", str(tmp_path / "werk_spans.jsonl"), "--chunk-size", "2"]  # fmt: skip
        monkeypatch.setattr(sys, "argv", [*argv, *extra])
        return spans.main()

    run_main.tagger_loads = calls
    return run_main


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


# --- Spans of one record ---


@pytest.mark.parametrize(
    "text, expected",
    [
        ("in vierzig Bänden, 12. Faust, 2. Theil", "Faust, 2. Theil"),
        ("Roman in drei Bänden", None),
        ("2. Aufl.", None),
        ("Werke, 2. Theil", None),
        ("Bd. 3. Die Räuber.", "Die Räuber"),
    ],
)
def test_volume_span_takes_the_title_after_the_first_volume_number(text, expected):
    assert spans.volume_span(text) == expected


def test_faust_record_gets_its_volume_title(rule):
    title_spans, persons = spans.rule_spans(FAUST_TITLE, rule)

    assert ("Faust, 2. Theil", "volume_rule") in title_spans
    assert title_spans[0] == ("Goethe's sämmtliche Werke", "isbd_title")
    assert persons == []


def test_flair_span_is_added_only_with_a_new_search_term():
    rule_list = [("Faust : eine Tragödie", "isbd_title")]
    flair = {"WORK_OF_ART": ["Faust", "Urfaust", "1"]}

    assert spans.add_flair_spans(rule_list, flair) == [*rule_list, ("Urfaust", "flair")]


def test_spans_of_only_function_words_are_dropped_from_every_source():
    found = [
        ("der", "isbd_title"),
        ("bey, Leipzig", "isbd_other_title"),
        ("Die", "volume_rule"),
        ("Und die", "flair"),
        ("Die Räuber", "flair"),
        ("Von der Freiheit", "isbd_title"),
    ]

    assert spans.drop_function_word_spans(found) == [("Die Räuber", "flair"), ("Von der Freiheit", "isbd_title")]


def test_record_with_only_function_word_spans_gets_no_title_span(rule):
    predictions = [("WORK_OF_ART", "und", 0.9)]

    line = spans.record_line({"obj_id": "X", "title": "Der"}, "work", rule, predictions)

    assert line["title_spans"] == []
    assert line["span_sources"] == []
    assert line["flair"]["WORK_OF_ART"] == ["und"]


def test_contributors_only_when_creators_are_missing(rule):
    contributor = [{"label": "Mendelssohn", "uri": None}]

    with_creator = spans.record_line({"obj_id": "a", "title": "X", "dc_creator": [GOETHE], "dc_contributor": contributor}, "work", rule, None)
    without = spans.record_line({"obj_id": "b", "title": "X", "dc_creator": [], "dc_contributor": contributor}, "work", rule, None)

    assert with_creator["ddb_contributors"] == []
    assert without["ddb_contributors"] == [{"label": "Mendelssohn", "uri": ""}]


def test_chunk_lines_keep_linked_groups_in_input_order(rule):
    rows = [dict(zip(spans.COLUMNS, row)) for row in ROWS]

    lines = spans.chunk_lines(rows, rule, fake_tag, 2)

    assert [(line["obj_id"], line["type_group"]) for line in lines] == [
        ("faust", "work"), ("noten", "music"), ("blank", "work"), ("hamlet", "work"),
    ]  # fmt: skip
    noten = lines[1]
    assert "Mendelssohn" in noten["persons"]
    assert noten["flair"]["PERSON"] == ["Mendelssohn"]
    assert lines[2]["title_spans"] == []
    assert len(lines[0]["title_spans"]) == len(lines[0]["span_sources"])


def test_chunk_lines_without_tagger_leave_flair_empty(rule):
    rows = [dict(zip(spans.COLUMNS, ROWS[5]))]

    assert spans.chunk_lines(rows, rule, None, 2)[0]["flair"] == {}


# --- CLI ---


def test_main_writes_parts_and_merge_joins_them(tmp_path, cli):
    assert cli() == 0
    assert cli("--merge") == 0

    lines = read_jsonl(tmp_path / "werk_spans.jsonl")
    assert [line["obj_id"] for line in lines] == ["faust", "noten", "blank", "hamlet"]
    assert "Faust, 2. Theil" in lines[0]["title_spans"]
    state = json.loads((tmp_path / "parts" / "run-shard0.json").read_text())
    assert state["commit"] == "abc123"
    assert state["chunks"] == 3


def test_two_shards_cover_every_chunk_once(tmp_path, cli):
    assert cli("--num-shards", "2", "--shard", "0") == 0
    with pytest.raises(SystemExit):
        cli("--num-shards", "2", "--merge")
    assert cli("--num-shards", "2", "--shard", "1") == 0
    assert cli("--num-shards", "2", "--merge") == 0

    assert len(read_jsonl(tmp_path / "werk_spans.jsonl")) == 4


def test_second_run_does_nothing_and_loads_no_model(cli):
    assert cli() == 0
    assert cli() == 0

    assert len(cli.tagger_loads) == 1


def test_interrupted_run_resumes_with_the_same_output(tmp_path, cli, monkeypatch):
    real = spans.chunk_lines
    calls = {"n": 0}

    def interrupt_second_chunk(*args):
        calls["n"] += 1
        if calls["n"] == 2:
            raise KeyboardInterrupt
        return real(*args)

    monkeypatch.setattr(spans, "chunk_lines", interrupt_second_chunk)
    assert cli() == 130
    assert not list((tmp_path / "parts").glob("*.tmp"))
    monkeypatch.setattr(spans, "chunk_lines", real)
    assert cli() == 0
    assert cli("--merge") == 0
    resumed = (tmp_path / "werk_spans.jsonl").read_text()

    assert cli("--restart") == 0
    assert cli("--merge") == 0
    assert (tmp_path / "werk_spans.jsonl").read_text() == resumed


def test_changed_settings_stop_with_a_hint(cli, capsys):
    assert cli() == 0

    with pytest.raises(SystemExit) as exc:
        cli("--batch-size", "8")

    assert exc.value.code == 1
    assert "--restart" in capsys.readouterr().err


def test_uncommitted_changes_stop_unless_allowed(cli, monkeypatch, capsys):
    monkeypatch.setattr(spans, "git_state", lambda: ("abc123", True))

    with pytest.raises(SystemExit) as exc:
        cli()
    assert exc.value.code == 1
    assert "uncommitted changes" in capsys.readouterr().err

    assert cli("--allow-dirty") == 0


def test_changed_rule_script_stops(tmp_path, cli, capsys):
    copy = tmp_path / "rule.py"
    copy.write_text(spans.DEFAULT_RULE_SCRIPT.read_text(encoding="utf-8") + "\n# edited\n", encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        cli("--rule-script", str(copy))

    assert exc.value.code == 1
    assert "050820b" in capsys.readouterr().err


def test_missing_column_stops(tmp_path, cli, capsys):
    pq.write_table(pa.table({"obj_id": ["a"], "title": ["b"]}), tmp_path / "in.parquet")

    with pytest.raises(SystemExit) as exc:
        cli()

    assert exc.value.code == 1
    assert "no column" in capsys.readouterr().err


def test_dry_run_writes_nothing_and_loads_no_model(tmp_path, cli, capsys):
    assert cli("--dry-run", "--limit", "6") == 0

    assert not (tmp_path / "parts").exists()
    assert cli.tagger_loads == []
    assert '"obj_id": "faust"' in capsys.readouterr().out


@pytest.mark.parametrize("flags", [["--shard", "1"], ["--chunk-size", "0"], ["--no-such-flag"]])
def test_bad_arguments_exit_2(cli, flags):
    with pytest.raises(SystemExit) as exc:
        cli(*flags)

    assert exc.value.code == 2


# --- Run identity and provenance ---


def run_id_of(tmp_path):
    return json.loads((tmp_path / "parts" / "run-id.json").read_text(encoding="utf-8"))


def test_shards_share_one_run_id_and_a_rerun_keeps_its_start(tmp_path, cli):
    assert cli("--num-shards", "2", "--shard", "0") == 0
    first = run_id_of(tmp_path)
    assert cli("--num-shards", "2", "--shard", "1") == 0
    assert cli("--num-shards", "2", "--shard", "0") == 0

    assert run_id_of(tmp_path) == first
    assert werk_link_rules.RUN_ID_RE.match(first["run_id"])


def test_given_run_id_is_recorded_and_another_on_resume_stops(tmp_path, cli, capsys):
    assert cli("--run-id", "20261110T090205Z") == 0
    assert run_id_of(tmp_path)["run_id"] == "20261110T090205Z"

    with pytest.raises(SystemExit) as exc:
        cli("--run-id", "20261111T000000Z")

    assert exc.value.code == 1
    assert "20261110T090205Z" in capsys.readouterr().err


def test_malformed_run_id_exits_2(cli):
    with pytest.raises(SystemExit) as exc:
        cli("--run-id", "2026-11-10")

    assert exc.value.code == 2


def test_settings_record_input_sha256_model_revision_and_versions(tmp_path, cli):
    assert cli() == 0

    state = json.loads((tmp_path / "parts" / "run-shard0.json").read_text(encoding="utf-8"))
    assert state["input_sha256"] == spans.sha256(tmp_path / "in.parquet")
    assert state["model_caches"][0]["revision"] == "r1"
    assert set(state["versions"]) == {"flair", "torch"}


def test_settings_without_flair_record_no_model(tmp_path, cli):
    assert cli("--no-flair") == 0

    state = json.loads((tmp_path / "parts" / "run-shard0.json").read_text(encoding="utf-8"))
    assert state["model_caches"] == []
    assert state["versions"] == {}


def test_run_identity_reads_the_file_another_process_created_first(tmp_path):
    (tmp_path / "run-id.json").write_text(json.dumps({"run_id": "20261110T090205Z", "started_at": "x"}), encoding="utf-8")

    assert spans.run_identity(tmp_path, None) == {"run_id": "20261110T090205Z", "started_at": "x"}
    assert not list(tmp_path.glob("*.tmp"))

