# Purpose:      Tests for scripts/py/link_gnd_local.py, step 2 of Werk linking on the local
#               GND Parquet tables (tiny tables written into tmp_path; no real data).
# Usage:        .venv/bin/python -m pytest scripts/tests/test_link_gnd_local.py -q
# Dependencies: pytest, pyarrow, rapidfuzz

from __future__ import annotations

import csv
import hashlib
import json
import logging
import sys
import unicodedata
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "py"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import link_gnd_bikidata as bikidata
import link_gnd_local as local
from test_link_gnd_bikidata import FakeBackend

BASE = "https://d-nb.info/gnd/"
FAUST_1 = "4099197-0"
FAUST_2 = "4099198-2"
HOTZENPLOTZ = "4697574-3"
GLOCKE = "3000000-3"
TEIL = "3000001-1"
KONZERT = "1000000-1"
GOETHE = "118540238"
PHILHARMONIKER = "2000000-2"

# Work ID -> names; the Hotzenplotz name is stored decomposed (NFD), as in the dump.
NAMES = {
    FAUST_1: ["Faust, 1", "Faust. 1. Teil"],
    FAUST_2: ["Faust, 2", "Faust. 2. Teil"],
    HOTZENPLOTZ: [unicodedata.normalize("NFD", "Der Räuber Hotzenplotz")],
    GLOCKE: ["Die Glocke", "Das Lied"],
    TEIL: ["Zweiter Teil"],
    KONZERT: ["Konzert"],
}
CLASSES = {KONZERT: ["MusicalWork"]}  # all others: Work
CREATORS = {FAUST_1: [GOETHE], FAUST_2: [GOETHE], KONZERT: [PHILHARMONIKER]}
CREATOR_NAMES = {
    GOETHE: ("person", ["Goethe, Johann Wolfgang von", "Goethe, J. W. von"]),
    PHILHARMONIKER: ("corporate", ["Berliner Philharmoniker"]),
}
FAUST_RECORD = {
    "obj_id": "NEL6FUWBOR4IGNBIWEITW63XM7TWO46L",
    "type_group": "work",
    "title_spans": ["Goethe's sämmtliche Werke", "in vierzig Bänden, 12. Faust, 2. Theil", "Faust, 2. Theil"],
    "span_sources": ["isbd_title", "isbd_other_title", "volume_rule"],
    "persons": ["Goethe"],
    "ddb_creators": [{"label": "Goethe, Johann Wolfgang von", "uri": f"http://d-nb.info/gnd/{GOETHE}"}],
}


def file_sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_tables(gnd_dir, names=NAMES, classes=CLASSES, creators=CREATORS, creator_names=CREATOR_NAMES):
    """The four tables and a manifest.json with their SHA-256, as gnd_jsonld_to_parquet.py writes them."""
    gnd_dir.mkdir(parents=True, exist_ok=True)
    rows = {
        "gnd_work_names.parquet": [
            {"gnd_id": w, "kind": "preferred" if i == 0 else "variant", "name": n}
            for w, ns in names.items() for i, n in enumerate(ns)
        ],
        "gnd_work_classes.parquet": [{"gnd_id": w, "class": c} for w in names for c in classes.get(w, ["Work"])],
        "gnd_work_creators.parquet": [
            {"gnd_id": w, "role": "firstAuthor", "creator_id": c} for w, cs in creators.items() for c in cs
        ],
        "gnd_creator_names.parquet": [
            {"gnd_id": c, "entity": entity, "kind": "preferred", "name": n}
            for c, (entity, ns) in creator_names.items() for n in ns
        ],
    }  # fmt: skip
    columns = {
        "gnd_work_names.parquet": ["gnd_id", "kind", "name"],
        "gnd_work_classes.parquet": ["gnd_id", "class"],
        "gnd_work_creators.parquet": ["gnd_id", "role", "creator_id"],
        "gnd_creator_names.parquet": ["gnd_id", "entity", "kind", "name"],
    }
    tables = {}
    for name, table_rows in rows.items():
        schema = pa.schema([(c, pa.string()) for c in columns[name]])
        pq.write_table(pa.Table.from_pylist(table_rows, schema=schema), gnd_dir / name)
        tables[name] = {"rows": len(table_rows), "sha256": file_sha256(gnd_dir / name)}
    dumps = {"werk": {"date": "2026-10-13", "sha256": "w"}, "person": {"date": "2026-10-13", "sha256": "p"}}
    (gnd_dir / "manifest.json").write_text(json.dumps({"dumps": dumps, "tables": tables}), encoding="utf-8")
    return gnd_dir


@pytest.fixture
def index(tmp_path):
    return local.load_index(write_tables(tmp_path / "gnd"))


def lookup_for(index, search_size=500):
    return lambda term: index.fetch(term, search_size)


def link(record, index, decision="baseline"):
    return local.link_record(record, lookup_for(index), 0.6, 0.85, decision)


def ids(result):
    return [w.uri.removeprefix(BASE) for w in result.works]


# --- Index and candidates ---


def test_candidates_need_every_token_in_one_name(index):
    assert ids(index.fetch("das lied", 500)) == [GLOCKE]
    assert ids(index.fetch("lied glocke", 500)) == []


@pytest.mark.parametrize(
    "span, expected",
    [
        ("Faust, 2. Theil", [FAUST_1, FAUST_2]),
        ("Der Raeuber Hotzenplotz", [HOTZENPLOTZ]),  # umlaut spelled out; GND name in NFD
        ("Die Raeuber Hotzenplotz", []),  # every token must be in the name
        ("Der Räuber Hotzenplotz", [HOTZENPLOTZ]),
        ("Zweiter Theil", [TEIL]),  # historical spelling
    ],
)
def test_candidates_match_spelling_variants_and_decomposed_names(index, span, expected):
    assert ids(index.fetch(bikidata.search_term(span), 500)) == expected


def test_candidates_are_truncated_only_above_search_size(index):
    assert index.fetch("faust", 2).truncated is False
    assert index.fetch("faust", 1).truncated is True
    assert len(index.fetch("faust", 1).works) == 2  # all candidates kept


def test_candidates_carry_creator_names_and_entities(index):
    (konzert,) = index.fetch("konzert", 500).works

    assert konzert.authors == {PHILHARMONIKER: ["Berliner Philharmoniker"]}
    assert konzert.entities == {PHILHARMONIKER: "corporate"}
    assert konzert.classes == ["MusicalWork"]


def test_term_without_tokens_finds_nothing(index):
    assert index.fetch("", 500).works == []


@pytest.mark.parametrize("group, expected", [("work", "linked"), ("music", "no_candidate"), (None, "linked")])
def test_class_filter_follows_the_type_group(index, group, expected):
    record = {**FAUST_RECORD, "type_group": group}

    assert link(record, index)["status"] == expected


def test_music_group_finds_a_musical_work(index):
    record = {"obj_id": "m", "type_group": "music", "title_spans": ["Konzert"],
              "ddb_creators": [{"label": "Berliner Philharmoniker"}]}  # fmt: skip

    assert link(record, index)["gnd_uri"] == BASE + KONZERT


# --- Author rule ---


def work(authors, entities):
    return local.LocalWork(uri=BASE + "x", names=["X"], authors=authors, entities=entities)


GOETHE_WORK = work({GOETHE: CREATOR_NAMES[GOETHE][1]}, {GOETHE: "person"})
CORPORATE_WORK = work({PHILHARMONIKER: ["Berliner Philharmoniker"]}, {PHILHARMONIKER: "corporate"})


@pytest.mark.parametrize(
    "creator, gnd_work, expected",
    [
        ({"uri": f"https://d-nb.info/gnd/{GOETHE}"}, GOETHE_WORK, "yes"),
        ({"uri": "http://d-nb.info/gnd/999"}, GOETHE_WORK, "no"),
        ({"label": "Göthe, Johann Wolfgang"}, GOETHE_WORK, "yes"),  # person: Jaro-Winkler 0.85
        ({"label": "Schiller, Friedrich"}, GOETHE_WORK, "no"),
        ({"label": "Berliner Philharmoniker"}, CORPORATE_WORK, "yes"),
        ({"label": "berliner  PHILHARMONIKER"}, CORPORATE_WORK, "yes"),  # equal tokens
        ({"label": "Philharmoniker"}, CORPORATE_WORK, "no"),  # corporate: exact only
        ({"label": "Goethe, Johann Wolfgang von"}, CORPORATE_WORK, "no"),  # person against corporate-only
    ],
)
def test_author_rule(creator, gnd_work, expected):
    record = {"obj_id": "a", "ddb_creators": [creator]}

    assert local.author_check(gnd_work, record, 0.85) == expected


def test_author_rule_pass1_name_against_corporate_is_exact():
    assert local.author_check(CORPORATE_WORK, {"persons": ["Philharmoniker"]}, 0.85) == "pass1_no"
    assert local.author_check(CORPORATE_WORK, {"persons": ["Berliner Philharmoniker"]}, 0.85) == "pass1_yes"


def test_author_rule_creator_without_names_is_unknown():
    nameless = work({"123": []}, {"123": ""})

    assert local.author_check(nameless, {"ddb_creators": [{"label": "Goethe"}]}, 0.85) == "unknown"


# --- Matched span ---


def test_matched_span_and_source_name_the_span_of_the_top_work(index):
    row = link(FAUST_RECORD, index)

    assert row["gnd_uri"] == BASE + FAUST_2
    assert row["matched_span"] == "Faust, 2. Theil"
    assert row["matched_span_source"] == "volume_rule"


def test_matched_span_source_is_empty_without_span_sources(index):
    record = {k: v for k, v in FAUST_RECORD.items() if k != "span_sources"}

    row = link(record, index)

    assert row["matched_span"] == "Faust, 2. Theil"
    assert row["matched_span_source"] == ""


def test_no_top_work_leaves_matched_span_empty(index):
    row = link({"obj_id": "x", "title_spans": ["Unbekannt"], "span_sources": ["isbd_title"]}, index)

    assert row["status"] == "no_candidate"
    assert row["matched_span"] == ""


def test_span_sources_of_another_length_raise():
    with pytest.raises(ValueError, match="2 title_spans but 1 span_sources"):
        local.span_pairs({"title_spans": ["a", "b"], "span_sources": ["isbd_title"]})


# --- Same values as link_gnd_bikidata.link_record on the shared columns ---

SHARED_RECORDS = [
    FAUST_RECORD,
    {"obj_id": "literal", "title_spans": ["Faust, 1"], "ddb_creators": [{"label": "Goethe, J. W."}]},
    {"obj_id": "other-id", "title_spans": ["Faust, 1"], "ddb_creators": [{"uri": "http://d-nb.info/gnd/1"}]},
    {"obj_id": "pass1", "title_spans": ["Faust, 2"], "persons": ["Goethe"]},
    {"obj_id": "no-author", "title_spans": ["Faust"]},
    {"obj_id": "contributor", "title_spans": ["Faust, 1"], "ddb_contributors": [{"label": "Goethe (Hrsg.)"}]},
    {"obj_id": "music", "type_group": "music", "title_spans": ["Faust, 2"]},
    {"obj_id": "no-title", "title_spans": []},
]


@pytest.mark.parametrize("decision", ["baseline", "score"])
@pytest.mark.parametrize("record", SHARED_RECORDS, ids=lambda r: r["obj_id"][:12])
def test_shared_columns_equal_the_bikidata_linker(tmp_path, record, decision):
    faust = {FAUST_1: NAMES[FAUST_1], FAUST_2: NAMES[FAUST_2]}
    goethe = {GOETHE: CREATOR_NAMES[GOETHE]}
    index = local.load_index(write_tables(tmp_path / "gnd", names=faust, classes={}, creator_names=goethe))
    backend = FakeBackend(names=faust, authors=CREATORS, persons={GOETHE: CREATOR_NAMES[GOETHE][1]})
    bikidata_record = {k: v for k, v in record.items() if k != "span_sources"}

    expected = bikidata.link_record(bikidata_record, bikidata.make_lookup(backend, 500), 0.6, 0.85, decision)
    actual = link(record, index, decision)

    assert {c: actual[c] for c in bikidata.OUTPUT_COLUMNS} == expected


# --- Through main() ---


def write_jsonl(path, records):
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8")


def read_csv(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def faust_records(count):
    return [{"obj_id": f"id{i}", "title_spans": ["Faust, 2. Theil"]} for i in range(count)]


@pytest.fixture
def cli(tmp_path, monkeypatch):
    """Runs main() on tiny tables, with a clean git state unless cli.dirty is set; returns the exit code."""
    gnd = write_tables(tmp_path / "gnd")
    state = {"dirty": False}
    monkeypatch.setattr(local, "git_state", lambda: ("abc123", state["dirty"]))

    def run_main(*extra):
        argv = ["x", "--input", str(tmp_path / "spans.jsonl"), "--output", str(tmp_path / "links.csv"),
                "--gnd-dir", str(gnd)]  # fmt: skip
        monkeypatch.setattr(sys, "argv", [*argv, *extra])
        return local.main()

    run_main.state = state
    run_main.gnd = gnd
    return run_main


def test_main_writes_one_row_per_record_and_the_side_outputs(tmp_path, cli):
    write_jsonl(tmp_path / "spans.jsonl", [FAUST_RECORD, {"obj_id": "ünï", "title_spans": []}])

    assert cli() == 0

    rows = read_csv(tmp_path / "links.csv")
    assert list(rows[0]) == local.OUTPUT_COLUMNS
    assert [r["obj_id"] for r in rows] == [FAUST_RECORD["obj_id"], "ünï"]
    assert rows[0]["gnd_uri"] == BASE + FAUST_2
    assert rows[1]["status"] == "no_title"
    summary = read_csv(tmp_path / "links_status_by_group.csv")
    assert {"type_group": "work", "status": "linked", "records": "1", "high_confidence": "1"} in summary
    assert {"type_group": "all", "status": "no_title", "records": "1", "high_confidence": "0"} in summary
    timing = read_csv(tmp_path / "links_term_timing.csv")
    assert {r["term"]: r["records"] for r in timing} == {
        "goethe s sämmtliche werke": "1", "in vierzig bänden": "1", "faust": "1",
    }  # fmt: skip


def test_main_records_provenance_in_run_json(tmp_path, cli):
    write_jsonl(tmp_path / "spans.jsonl", faust_records(2))

    cli()

    state = json.loads((tmp_path / "links.csv.parts" / "run.json").read_text(encoding="utf-8"))
    assert state["backend"] == "local"
    assert state["script"]["commit"] == "abc123"
    assert state["gnd"]["tables"]["gnd_work_names.parquet"] == file_sha256(cli.gnd / "gnd_work_names.parquet")
    assert state["gnd"]["dumps"]["werk"]["date"] == "2026-10-13"
    assert state["input"]["records"] == 2
    assert state["output"]["rows"] == 2
    assert state["output"]["sha256"] == file_sha256(tmp_path / "links.csv")


def test_main_second_run_does_nothing(tmp_path, cli, caplog):
    caplog.set_level(logging.INFO)
    write_jsonl(tmp_path / "spans.jsonl", faust_records(3))
    cli()
    first = (tmp_path / "links.csv").read_text(encoding="utf-8")

    assert cli() == 0

    assert "Run already finished" in caplog.text
    assert (tmp_path / "links.csv").read_text(encoding="utf-8") == first


def test_main_restart_gives_identical_output(tmp_path, cli):
    write_jsonl(tmp_path / "spans.jsonl", faust_records(3))
    cli()
    first = (tmp_path / "links.csv").read_text(encoding="utf-8")

    assert cli("--restart") == 0

    assert (tmp_path / "links.csv").read_text(encoding="utf-8") == first


def test_main_interrupt_exits_130_and_rerun_matches_an_uninterrupted_run(tmp_path, cli, monkeypatch):
    # A different term per record, so that every record looks up the index.
    records = [{"obj_id": f"id{i}", "title_spans": [f"Faust{i}, 2"]} for i in range(6)]
    write_jsonl(tmp_path / "spans.jsonl", records)
    cli("--chunk-size", "2")
    expected = (tmp_path / "links.csv").read_text(encoding="utf-8")
    original = local.GndIndex.fetch
    calls = []

    def fetch_then_interrupt(self, term, search_size):
        calls.append(term)
        if len(calls) > 2:
            raise KeyboardInterrupt
        return original(self, term, search_size)

    monkeypatch.setattr(local.GndIndex, "fetch", fetch_then_interrupt)

    assert cli("--chunk-size", "2", "--restart") == 130

    parts = tmp_path / "links.csv.parts"
    assert not (tmp_path / "links.csv").exists()
    assert list(parts.glob("*.tmp")) == []
    assert (parts / "part-000000.csv").exists()
    assert not (parts / "part-000001.csv").exists()

    monkeypatch.setattr(local.GndIndex, "fetch", original)
    assert cli("--chunk-size", "2") == 0

    assert (tmp_path / "links.csv").read_text(encoding="utf-8") == expected
    timing = read_csv(tmp_path / "links_term_timing.csv")
    assert sorted(r["term"] for r in timing) == [f"faust{i}" for i in range(6)]
    assert all(r["records"] == "1" for r in timing)  # chunk 0 not counted twice


def test_main_keep_going_lists_failures_and_a_rerun_retries_them(tmp_path, cli, monkeypatch):
    write_jsonl(tmp_path / "spans.jsonl", [{"obj_id": f"id{i}", "title_spans": [f"Faust{i}"]} for i in range(3)])
    original = local.GndIndex.fetch
    monkeypatch.setattr(local.GndIndex, "fetch", lambda self, term, size: (_ for _ in ()).throw(RuntimeError("x")))

    assert cli("--chunk-size", "1", "--keep-going") == 1

    parts = tmp_path / "links.csv.parts"
    assert (parts / "failed.txt").read_text(encoding="utf-8").split() == ["0", "1", "2"]
    monkeypatch.setattr(local.GndIndex, "fetch", original)

    assert cli("--chunk-size", "1", "--keep-going") == 0

    assert len(read_csv(tmp_path / "links.csv")) == 3


def test_main_table_with_another_sha256_exits_1(tmp_path, cli, capsys):
    write_jsonl(tmp_path / "spans.jsonl", faust_records(1))
    manifest = json.loads((cli.gnd / "manifest.json").read_text(encoding="utf-8"))
    manifest["tables"]["gnd_work_classes.parquet"]["sha256"] = "0" * 64
    (cli.gnd / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        cli()

    assert exc.value.code == 1
    assert "gnd_work_classes.parquet has SHA-256" in capsys.readouterr().err


@pytest.mark.parametrize("missing", ["gnd_creator_names.parquet", "manifest.json"])
def test_main_missing_table_or_manifest_exits_1(tmp_path, cli, capsys, missing):
    write_jsonl(tmp_path / "spans.jsonl", faust_records(1))
    (cli.gnd / missing).unlink()

    with pytest.raises(SystemExit) as exc:
        cli()

    assert exc.value.code == 1
    assert "not found" in capsys.readouterr().err


def test_main_parts_folder_from_the_bikidata_linker_stops(tmp_path, cli, capsys):
    write_jsonl(tmp_path / "spans.jsonl", faust_records(1))
    parts = tmp_path / "links.csv.parts"
    parts.mkdir()
    bikidata_state = {"input_size": 1, "input_mtime": 1.0, "records": 1, "limit": None, "chunk_size": 1000,
                      "decision": "baseline", "min_score": 0.6, "name_threshold": 0.85, "search_size": 500}  # fmt: skip
    (parts / "run.json").write_text(json.dumps(bikidata_state), encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        cli()

    assert exc.value.code == 1
    assert "--restart" in capsys.readouterr().err


def test_main_other_tables_stop_a_resumed_run(tmp_path, cli, capsys):
    write_jsonl(tmp_path / "spans.jsonl", faust_records(1))
    cli()
    (tmp_path / "links.csv").unlink()
    write_tables(cli.gnd, names={FAUST_1: ["Faust, 1"]})

    with pytest.raises(SystemExit) as exc:
        cli()

    assert exc.value.code == 1
    assert "GND tables" in capsys.readouterr().err


def test_main_uncommitted_changes_stop_unless_allowed(tmp_path, cli, capsys):
    write_jsonl(tmp_path / "spans.jsonl", faust_records(1))
    cli.state["dirty"] = True

    with pytest.raises(SystemExit) as exc:
        cli()

    assert exc.value.code == 1
    assert "--allow-dirty" in capsys.readouterr().err
    assert cli("--allow-dirty") == 0
    state = json.loads((tmp_path / "links.csv.parts" / "run.json").read_text(encoding="utf-8"))
    assert state["script"]["uncommitted_changes"] is True


def test_main_span_sources_of_another_length_exit_1_before_any_work(tmp_path, cli, capsys):
    write_jsonl(tmp_path / "spans.jsonl", [{**FAUST_RECORD, "span_sources": ["isbd_title"]}])

    with pytest.raises(SystemExit) as exc:
        cli()

    assert exc.value.code == 1
    assert "span_sources" in capsys.readouterr().err
    assert not (tmp_path / "links.csv.parts").exists()


def test_main_dry_run_prints_candidates_and_writes_nothing(tmp_path, cli, capsys):
    write_jsonl(tmp_path / "spans.jsonl", [FAUST_RECORD])

    assert cli("--dry-run") == 0

    out = capsys.readouterr().out
    assert "faust\tfaust\t2\t2\tFalse" in out
    assert "volume_rule" in out
    assert not (tmp_path / "links.csv").exists()
    assert not (tmp_path / "links.csv.parts").exists()


def test_main_limit_processes_only_the_first_records(tmp_path, cli):
    write_jsonl(tmp_path / "spans.jsonl", faust_records(3))

    cli("--limit", "2")

    assert [r["obj_id"] for r in read_csv(tmp_path / "links.csv")] == ["id0", "id1"]


def test_main_refuses_to_replace_a_foreign_output(tmp_path, cli, capsys):
    write_jsonl(tmp_path / "spans.jsonl", faust_records(1))
    (tmp_path / "links.csv").write_text("keep me", encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        cli()

    assert exc.value.code == 1
    assert (tmp_path / "links.csv").read_text(encoding="utf-8") == "keep me"


def test_main_missing_input_exits_1(cli, capsys):
    with pytest.raises(SystemExit) as exc:
        cli()

    assert exc.value.code == 1
    assert "not found" in capsys.readouterr().err


@pytest.mark.parametrize(
    "flags", [["--no-such-flag"], ["--chunk-size", "0"], ["--min-score", "2"], ["--decision", "best"]]
)
def test_main_bad_arguments_exit_2(tmp_path, cli, flags):
    write_jsonl(tmp_path / "spans.jsonl", faust_records(1))

    with pytest.raises(SystemExit) as exc:
        cli(*flags)

    assert exc.value.code == 2


def test_main_records_the_given_run_id_and_stops_on_another(tmp_path, cli, capsys):
    write_jsonl(tmp_path / "spans.jsonl", faust_records(2))

    assert cli("--run-id", "20261110T090205Z", "--limit", "1") == 0
    identity = json.loads((tmp_path / "links.csv.parts" / "run-id.json").read_text(encoding="utf-8"))
    assert identity["run_id"] == "20261110T090205Z"
    assert "run_id" not in json.loads((tmp_path / "links.csv.parts" / "run.json").read_text(encoding="utf-8"))
    assert cli("--restart", "--run-id", "20261111T000000Z") == 0
    assert json.loads((tmp_path / "links.csv.parts" / "run-id.json").read_text(encoding="utf-8"))["run_id"] == "20261111T000000Z"

