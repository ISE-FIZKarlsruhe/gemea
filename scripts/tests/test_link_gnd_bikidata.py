# Purpose:      Tests for scripts/py/link_gnd_bikidata.py, step 2 of Werk linking,
#               with GND behind bikidata (faked here; no database).
# Usage:        .venv/bin/python -m pytest scripts/tests/test_link_gnd_bikidata.py -q
# Dependencies: pytest, rapidfuzz

from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "py"))
import link_gnd_bikidata as link

FAUST_1 = "4099197-0"
FAUST_2 = "4099198-2"
GENERAL_FAUST = "4128140-8"
GOUNOD_FAUST = "300061099"
GOETHE = "118540238"
GOUNOD = "118541277"
GOETHE_NAMES = ["Goethe, Johann Wolfgang von", "Goethe, J. W. von", "Johann Wolfgang von Goethe"]
BASE = "https://d-nb.info/gnd/"

# Work names copied from gemea/data/gnd/nt/werk.nt.
WORK_NAMES = {
    FAUST_1: ["Faust, 1", "Faust. 1. Teil"],
    FAUST_2: ["Faust, 2", "Faust. 2. Teil"],
}
WORK_AUTHORS = {FAUST_1: [GOETHE], FAUST_2: [GOETHE]}
PERSON_NAMES = {GOETHE: GOETHE_NAMES}
FAUST_RECORD = {
    "obj_id": "NEL6FUWBOR4IGNBIWEITW63XM7TWO46L",
    "title_spans": ["Faust, 2. Theil"],
    "persons": ["Goethe"],
    "ddb_creators": [{"label": "Goethe, Johann Wolfgang von", "uri": f"http://d-nb.info/gnd/{GOETHE}"}],
}


def make_work(work_id, names, authors=None):
    return link.Work(uri=BASE + work_id, names=names, authors=authors or {})


def term_result(*works):
    return link.TermResult(works=list(works), truncated=False)


def link_one(record, *works, min_score=0.6, decision="score"):
    return link.link_record(record, lambda term: term_result(*works), min_score, 0.85, decision)


def link_baseline(record, *works):
    return link_one(record, *works, decision="baseline")


class FakeBackend(link.Backend):
    """Answers the script's queries from small dicts, counting them."""

    def __init__(self, names=None, authors=None, persons=None, fail_on=None, classes=None):
        self.names = WORK_NAMES if names is None else names
        self.classes = classes or {}
        self.authors = WORK_AUTHORS if authors is None else authors
        self.persons = PERSON_NAMES if persons is None else persons
        self.fail_on = fail_on
        self.fts_calls = []
        self.queries = []
        super().__init__(fts=self._fts, sparql=self._sparql)

    def _fts(self, query, prop, size):
        self.fts_calls.append((query, prop, size))
        if self.fail_on and self.fail_on in query:
            raise self.fail_exception
        return [(f"<{BASE}{work_id}>", 1.0) for work_id in sorted(self.names)][:size]

    fail_exception = RuntimeError("search failed")

    def _sparql(self, query):
        self.queries.append(query)
        keys = re.findall(rf"<{BASE}([^>]+)>", query)
        if "?s" in query:
            return {"results": {"bindings": [{"s": {"value": "x"}}]}}
        if "rdf-syntax-ns#type" in query:
            table = {k: [link.GNDO + c for c in self.classes.get(k, ["Work"])] for k in keys}
        elif "firstAuthor" in query:
            table = {k: [BASE + a for a in self.authors.get(k, [])] for k in keys}
        elif "ForThePerson" in query:
            table = {k: self.persons.get(k, []) for k in keys}
        else:
            table = {k: self.names.get(k, []) for k in keys}
        bindings = [
            {"key": {"value": BASE + key}, "value": {"value": value}} for key in keys for value in table[key]
        ]
        return {"results": {"bindings": bindings}}


# --- Title scoring and search terms ---


@pytest.mark.parametrize(
    "span, name, expected",
    [
        ("Faust, 2. Theil", "Faust. 2. Teil", 1.0),
        ("Faust, 2. Theil", "Faust, 2", 0.8),
        ("Die Räuber", "Die Raeuber", 1.0),
        ("Faust", "Hamlet", 0.0),
        ("", "Faust", 0.0),
    ],
)
def test_title_score_tolerates_punctuation_and_spelling(span, name, expected):
    assert link.title_score(span, name) == pytest.approx(expected)


@pytest.mark.parametrize(
    "span, expected",
    [
        ("Faust, 2. Theil", "faust"),
        ("Die Räuber : ein Schauspiel", "die räuber"),
        ('Hamlet (Der "Prinz") OR AND', 'hamlet der prinz or and'),
        ("Ab, cd", None),
        ("   ", None),
    ],
)
def test_search_term_keeps_only_word_tokens_before_the_first_delimiter(span, expected):
    assert link.search_term(span) == expected


@pytest.mark.parametrize(
    "term, expected",
    [("faust", "faust"), ("der theil", "der (theil OR teil)")],
)
def test_fts_query_adds_spelling_without_h(term, expected):
    assert link.fts_query(term) == expected


# --- Name matching ---


@pytest.mark.parametrize(
    "left, right",
    [
        ("Goethe", "Goethe, Johann Wolfgang von"),
        ("Johann Wolfgang von Goethe", "Goethe, Johann Wolfgang von"),
        ("J. W. v. Goethe", "Goethe, Johann Wolfgang von"),
        ("Göthe", "Goethe, Johann Wolfgang von"),
        ("Herrn Goethe", "Goethe, Johann Wolfgang von"),
        ("Schmidt, Hans", "Schmitt, Hans"),
    ],
)
def test_names_match_accepts_order_initials_and_spelling_variants(left, right):
    assert link.names_match(left, right, 0.85)


@pytest.mark.parametrize(
    "left, right",
    [
        ("Johann Müller", "Goethe, Johann Wolfgang von"),
        ("Goethe, Karl", "Goethe, Johann Wolfgang von"),
        ("von", "Goethe, Johann Wolfgang von"),
        ("", "Goethe, Johann Wolfgang von"),
    ],
)
def test_names_match_rejects_other_people(left, right):
    assert not link.names_match(left, right, 0.85)


# --- Author rule ---


@pytest.mark.parametrize(
    "creators, persons, authors, expected",
    [
        pytest.param([{"uri": f"http://d-nb.info/gnd/{GOETHE}"}], [], {GOETHE: []}, "yes", id="same-id-http"),
        pytest.param([{"uri": f"{BASE}{GOUNOD}"}], [], {GOETHE: []}, "no", id="different-id"),
        pytest.param([{"uri": f"{BASE}{GOUNOD}"}], [], {}, "unknown", id="id-but-work-without-author"),
        pytest.param([{"label": "Goethe"}], [], {GOETHE: GOETHE_NAMES}, "yes", id="literal-passes"),
        pytest.param([{"label": "Gounod"}], [], {GOETHE: GOETHE_NAMES}, "no", id="literal-fails"),
        pytest.param([{"label": "Goethe"}], [], {GOETHE: []}, "unknown", id="literal-no-person-names"),
        pytest.param([], ["Goethe"], {GOETHE: GOETHE_NAMES}, "pass1_yes", id="pass1-passes"),
        pytest.param([], ["Schlegel"], {GOETHE: GOETHE_NAMES}, "pass1_no", id="pass1-fails-without-removal"),
        pytest.param([], [], {GOETHE: GOETHE_NAMES}, "unknown", id="nothing-to-compare"),
    ],
)
def test_author_check_applies_the_rule(creators, persons, authors, expected):
    work = make_work(FAUST_2, ["Faust, 2"], authors)
    record = {"obj_id": "x", "ddb_creators": creators, "persons": persons}

    assert link.author_check(work, record, 0.85) == expected


# --- Linking a record ---


def test_link_record_faust_example_reaches_faust_2():
    works = [make_work(i, WORK_NAMES[i], {GOETHE: GOETHE_NAMES}) for i in (FAUST_1, FAUST_2)]

    row = link_one(FAUST_RECORD, *works)

    assert row["status"] == "linked"
    assert row["gnd_uri"] == BASE + FAUST_2
    assert row["matched_name"] == "Faust. 2. Teil"
    assert row["author_match"] == "yes"


def test_link_record_different_author_id_removes_the_candidate():
    goethe = make_work(FAUST_2, ["Faust"], {GOETHE: []})
    gounod = make_work(GOUNOD_FAUST, ["Faust"], {GOUNOD: []})
    record = {"obj_id": "x", "title_spans": ["Faust"], "ddb_creators": [{"uri": BASE + GOETHE}]}

    row = link_one(record, goethe, gounod)

    assert row["status"] == "linked"
    assert row["gnd_uri"] == BASE + FAUST_2
    assert row["candidate_count"] == 2


def test_link_record_all_candidates_removed_is_author_conflict():
    record = {"obj_id": "x", "title_spans": ["Faust"], "ddb_creators": [{"uri": BASE + GOUNOD}]}

    row = link_one(record, make_work(FAUST_2, ["Faust"], {GOETHE: []}))

    assert row["status"] == "author_conflict"
    assert row["gnd_uri"] == ""


def test_link_record_pass1_mismatch_does_not_remove_the_only_candidate():
    record = {"obj_id": "x", "title_spans": ["Hamlet"], "persons": ["Schlegel"]}
    work = make_work("4072578-9", ["Hamlet"], {"118613723": ["Shakespeare, William"]})

    row = link_one(record, work)

    assert row["status"] == "linked"
    assert row["author_match"] == "pass1_no"


def test_link_record_title_tie_without_authors_is_ambiguous():
    record = {"obj_id": "x", "title_spans": ["Faust"]}

    row = link_one(record, make_work(GENERAL_FAUST, ["Faust"]), make_work(GOUNOD_FAUST, ["Faust"]))

    assert row["status"] == "ambiguous"
    assert row["gnd_uri"] == ""
    assert row["tied_uris"].split() == sorted([BASE + GENERAL_FAUST, BASE + GOUNOD_FAUST])


def test_link_record_weak_title_match_is_below_threshold():
    record = {"obj_id": "x", "title_spans": ["Faust"]}

    row = link_one(record, make_work(FAUST_2, ["Faust und die Geschichte der deutschen Literatur"]))

    assert row["status"] == "below_threshold"
    assert row["gnd_uri"] == ""


def test_link_record_without_results_is_no_candidate():
    row = link_one({"obj_id": "x", "title_spans": ["Faust"]})

    assert row["status"] == "no_candidate"


@pytest.mark.parametrize("spans", [[], [""], ["   "], None])
def test_link_record_without_title_never_searches(spans):
    def lookup_fails(term):
        raise AssertionError("should not search")

    row = link.link_record({"obj_id": "x", "title_spans": spans}, lookup_fails, 0.6, 0.85)

    assert row["status"] == "no_title"


# --- Baseline decision ---


def test_baseline_links_exact_title_with_matching_author_id():
    works = [make_work(i, WORK_NAMES[i], {GOETHE: GOETHE_NAMES}) for i in (FAUST_1, FAUST_2)]

    row = link_baseline(FAUST_RECORD, *works)

    assert row["status"] == "linked"
    assert row["gnd_uri"] == BASE + FAUST_2


def test_baseline_exact_title_with_pass1_author_only_is_author_unconfirmed():
    record = {"obj_id": "x", "title_spans": ["Faust, 2. Theil"], "persons": ["Goethe"]}

    row = link_baseline(record, make_work(FAUST_2, WORK_NAMES[FAUST_2], {GOETHE: GOETHE_NAMES}))

    assert row["status"] == "author_unconfirmed"
    assert row["gnd_uri"] == ""
    assert row["author_match"] == "pass1_yes"


def test_baseline_title_not_exact_is_below_threshold_even_with_author():
    record = dict(FAUST_RECORD, title_spans=["Faust, 2. Theil, Akt 1"])

    row = link_baseline(record, make_work(FAUST_2, WORK_NAMES[FAUST_2], {GOETHE: GOETHE_NAMES}))

    assert row["status"] == "below_threshold"
    assert row["gnd_uri"] == ""


def test_baseline_two_qualifying_works_is_ambiguous():
    record = {"obj_id": "x", "title_spans": ["Faust"], "ddb_creators": [{"uri": BASE + GOETHE}]}
    first = make_work(FAUST_2, ["Faust"], {GOETHE: []})
    second = make_work(GENERAL_FAUST, ["Faust"], {GOETHE: []})

    row = link_baseline(record, first, second)

    assert row["status"] == "ambiguous"
    assert row["tied_uris"].split() == sorted([BASE + FAUST_2, BASE + GENERAL_FAUST])


def test_baseline_different_author_id_is_author_conflict():
    record = {"obj_id": "x", "title_spans": ["Faust"], "ddb_creators": [{"uri": BASE + GOUNOD}]}

    row = link_baseline(record, make_work(FAUST_2, ["Faust"], {GOETHE: []}))

    assert row["status"] == "author_conflict"


def test_baseline_exact_ignores_punctuation_and_spelling():
    record = dict(FAUST_RECORD, title_spans=["Faust. 2. Theil."])

    row = link_baseline(record, make_work(FAUST_2, ["Faust, 2. Teil"], {GOETHE: GOETHE_NAMES}))

    assert row["status"] == "linked"


def test_link_record_defaults_to_baseline():
    record = {"obj_id": "x", "title_spans": ["Faust"], "persons": ["Goethe"]}
    work = make_work(FAUST_2, ["Faust"], {GOETHE: GOETHE_NAMES})

    row = link.link_record(record, lambda term: term_result(work), 0.6, 0.85)

    assert row["status"] == "author_unconfirmed"


# --- Retrieval ---


def test_fetch_works_joins_names_and_authors():
    backend = FakeBackend()

    result = link.fetch_works(backend, "faust", 500)

    work = next(w for w in result.works if w.uri == BASE + FAUST_2)
    assert result.truncated is False
    assert sorted(work.names) == sorted(WORK_NAMES[FAUST_2])
    assert sorted(work.authors[GOETHE]) == sorted(GOETHE_NAMES)


def test_fetch_works_searches_with_bracketed_predicates():
    backend = FakeBackend()

    link.fetch_works(backend, "faust", 500)

    assert [call[1] for call in backend.fts_calls] == list(link.WORK_NAME_PREDICATES)
    assert all(call[1].startswith("<") and call[1].endswith(">") for call in backend.fts_calls)


def test_fetch_works_flags_a_search_that_reached_the_size_limit():
    result = link.fetch_works(FakeBackend(), "faust", 2)

    assert result.truncated is True


def test_fetch_pairs_ignores_iris_that_are_not_gnd():
    backend = FakeBackend()
    backend.sparql = lambda q: {
        "results": {"bindings": [{"key": {"value": "http://example.org/x"}, "value": {"value": "v"}}]}
    }

    assert list(link.fetch_pairs(backend, ["1"], "?key ?p ?value")) == []


def test_make_lookup_searches_each_term_once():
    backend = FakeBackend()
    lookup = link.make_lookup(backend, 500)

    lookup("faust")
    calls = len(backend.fts_calls)
    lookup("faust")

    assert len(backend.fts_calls) == calls


# --- CLI, through main() ---


def write_jsonl(path, records):
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8")


def read_csv(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def faust_records(count):
    return [{"obj_id": f"id{i}", "title_spans": ["Faust, 2. Theil"]} for i in range(count)]


@pytest.fixture
def cli(tmp_path, monkeypatch):
    """Runs main() with bikidata replaced by a fake; returns the exit code."""
    backend = FakeBackend()
    monkeypatch.setattr(link, "load_backend", lambda db_path: backend)

    def run_main(*extra):
        argv = ["x", "--input", str(tmp_path / "spans.jsonl"), "--output", str(tmp_path / "links.csv")]
        monkeypatch.setattr(sys, "argv", [*argv, *extra])
        return link.main()

    run_main.backend = backend
    return run_main


def test_main_writes_one_row_per_record_with_non_ascii_ids(tmp_path, cli):
    write_jsonl(tmp_path / "spans.jsonl", [FAUST_RECORD, {"obj_id": "ünï", "title_spans": []}])

    assert cli() == 0

    rows = read_csv(tmp_path / "links.csv")
    assert [r["obj_id"] for r in rows] == [FAUST_RECORD["obj_id"], "ünï"]
    assert rows[0]["gnd_uri"] == BASE + FAUST_2
    assert rows[1]["status"] == "no_title"


def test_main_empty_input_writes_only_the_header(tmp_path, cli):
    (tmp_path / "spans.jsonl").write_text("", encoding="utf-8")

    assert cli() == 0

    assert read_csv(tmp_path / "links.csv") == []


def test_main_missing_input_exits_1(cli, capsys):
    with pytest.raises(SystemExit) as exc:
        cli()

    assert exc.value.code == 1
    assert "not found" in capsys.readouterr().err


def test_main_malformed_line_exits_1_before_any_work(tmp_path, cli, capsys):
    (tmp_path / "spans.jsonl").write_text('{"obj_id": "a"}\nnot json\n', encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        cli()

    assert exc.value.code == 1
    assert "line 2" in capsys.readouterr().err
    assert cli.backend.fts_calls == []


def test_main_record_without_obj_id_exits_1(tmp_path, cli, capsys):
    (tmp_path / "spans.jsonl").write_text('{"title_spans": []}\n', encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        cli()

    assert exc.value.code == 1
    assert "no obj_id" in capsys.readouterr().err


@pytest.mark.parametrize("flags", [["--no-such-flag"], ["--chunk-size", "0"], ["--min-score", "2"]])
def test_main_bad_arguments_exit_2(tmp_path, cli, flags):
    write_jsonl(tmp_path / "spans.jsonl", [FAUST_RECORD])

    with pytest.raises(SystemExit) as exc:
        cli(*flags)

    assert exc.value.code == 2


def test_main_refuses_to_replace_a_foreign_output(tmp_path, cli, capsys):
    write_jsonl(tmp_path / "spans.jsonl", [FAUST_RECORD])
    (tmp_path / "links.csv").write_text("keep me", encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        cli()

    assert exc.value.code == 1
    assert "--restart" in capsys.readouterr().err
    assert (tmp_path / "links.csv").read_text(encoding="utf-8") == "keep me"


def test_main_second_run_does_nothing(tmp_path, cli):
    write_jsonl(tmp_path / "spans.jsonl", faust_records(3))
    cli()
    calls = len(cli.backend.fts_calls)

    assert cli() == 0

    assert len(cli.backend.fts_calls) == calls


def test_main_restart_gives_identical_output(tmp_path, cli):
    write_jsonl(tmp_path / "spans.jsonl", faust_records(3))
    cli()
    first = (tmp_path / "links.csv").read_text(encoding="utf-8")

    assert cli("--restart") == 0

    assert (tmp_path / "links.csv").read_text(encoding="utf-8") == first


def test_main_changed_input_stops_with_a_hint(tmp_path, cli, capsys):
    write_jsonl(tmp_path / "spans.jsonl", faust_records(3))
    cli()
    write_jsonl(tmp_path / "spans.jsonl", faust_records(4))

    with pytest.raises(SystemExit) as exc:
        cli()

    assert exc.value.code == 1
    assert "--restart" in capsys.readouterr().err


def test_main_dry_run_writes_nothing_and_needs_no_database(tmp_path, monkeypatch, capsys):
    write_jsonl(tmp_path / "spans.jsonl", [FAUST_RECORD])
    output = tmp_path / "links.csv"
    monkeypatch.setattr(sys, "argv", ["x", "--input", str(tmp_path / "spans.jsonl"), "--output", str(output), "--dry-run"])

    assert link.main() == 0

    assert "faust" in capsys.readouterr().out
    assert not output.exists()
    assert not (tmp_path / "links.csv.parts").exists()


def test_main_limit_processes_only_the_first_records(tmp_path, cli):
    write_jsonl(tmp_path / "spans.jsonl", faust_records(3))

    cli("--limit", "2")

    assert [r["obj_id"] for r in read_csv(tmp_path / "links.csv")] == ["id0", "id1"]


def test_main_duplicate_obj_id_exits_1_without_final_output(tmp_path, cli, capsys):
    write_jsonl(tmp_path / "spans.jsonl", [FAUST_RECORD, FAUST_RECORD])

    with pytest.raises(SystemExit) as exc:
        cli()

    assert exc.value.code == 1
    assert "appears twice" in capsys.readouterr().err
    assert not (tmp_path / "links.csv").exists()


def interrupt_on_search(backend, after_calls, error):
    """Make the fake's search raise error once it has been called after_calls times."""
    original = backend.fts

    def fts(query, prop, size):
        if len(backend.fts_calls) >= after_calls:
            raise error
        return original(query, prop, size)

    backend.fts = fts
    return original


def test_main_interrupt_exits_130_and_rerun_matches_an_uninterrupted_run(tmp_path, cli, monkeypatch):
    # Different spans per chunk, so that the cache does not hide the later searches.
    records = [{"obj_id": f"id{i}", "title_spans": [f"Faust{i}, 2"]} for i in range(6)]
    write_jsonl(tmp_path / "spans.jsonl", records)
    cli("--chunk-size", "2", "--restart")
    expected = (tmp_path / "links.csv").read_text(encoding="utf-8")
    write_jsonl(tmp_path / "spans.jsonl", records)
    cli.backend.fts_calls.clear()
    original = interrupt_on_search(cli.backend, 4, KeyboardInterrupt())

    assert cli("--chunk-size", "2", "--restart") == 130

    parts = tmp_path / "links.csv.parts"
    assert not (tmp_path / "links.csv").exists()
    assert list(parts.glob("*.tmp")) == []
    assert (parts / "part-000000.csv").exists()
    assert not (parts / "part-000001.csv").exists()

    cli.backend.fts = original
    cli.backend.fts_calls.clear()
    assert cli("--chunk-size", "2") == 0

    assert (tmp_path / "links.csv").read_text(encoding="utf-8") == expected
    assert len(cli.backend.fts_calls) == 8  # chunk 0 was not searched again


def test_main_failed_chunk_stops_by_default(tmp_path, cli):
    write_jsonl(tmp_path / "spans.jsonl", faust_records(2))
    interrupt_on_search(cli.backend, 0, RuntimeError("search failed"))

    with pytest.raises(RuntimeError):
        cli("--chunk-size", "1")

    assert not (tmp_path / "links.csv").exists()


def test_main_keep_going_lists_failures_and_a_rerun_retries_them(tmp_path, cli):
    records = [{"obj_id": f"id{i}", "title_spans": [f"Faust{i}"]} for i in range(3)]
    write_jsonl(tmp_path / "spans.jsonl", records)
    original = interrupt_on_search(cli.backend, 2, RuntimeError("search failed"))

    assert cli("--chunk-size", "1", "--keep-going") == 1

    parts = tmp_path / "links.csv.parts"
    assert (parts / "failed.txt").read_text(encoding="utf-8").split() == ["1", "2"]
    assert not (tmp_path / "links.csv").exists()

    cli.backend.fts = original

    assert cli("--chunk-size", "1", "--keep-going") == 0

    assert not (parts / "failed.txt").exists()
    assert len(read_csv(tmp_path / "links.csv")) == 3


def test_load_done_chunks_ignores_a_line_cut_off_by_a_crash(tmp_path):
    (tmp_path / link.part_name(0)).write_text("", encoding="utf-8")
    (tmp_path / "manifest.jsonl").write_text('{"chunk": 0, "rows": 1}\n{"chunk": 1, "ro', encoding="utf-8")

    assert link.load_done_chunks(tmp_path) == {0}


def test_load_done_chunks_needs_the_part_file(tmp_path):
    (tmp_path / "manifest.jsonl").write_text('{"chunk": 0, "rows": 1}\n', encoding="utf-8")

    assert link.load_done_chunks(tmp_path) == set()


def test_check_backend_missing_predicate_exits_1(capsys):
    backend = FakeBackend()
    backend.sparql = lambda q: {"results": {"bindings": []}}

    with pytest.raises(SystemExit) as exc:
        link.check_backend(backend)

    assert exc.value.code == 1
    assert "is GND loaded" in capsys.readouterr().err


def test_load_backend_missing_database_exits_1(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        link.load_backend(str(tmp_path / "no.duckdb"))

    assert exc.value.code == 1
    assert "not found" in capsys.readouterr().err


# --- Historical spelling rules ---


@pytest.mark.parametrize(
    "token, expected",
    [
        ("theil", "teil"),
        ("comet", "komet"),
        ("doctor", "doktor"),
        ("critischen", "kritischen"),
        ("zweyter", "zweiter"),
        ("vnter", "unter"),
        ("auff", "auf"),
        ("freittag", "freitag"),
        ("schach", "schach"),
        ("dick", "dick"),
        ("evangelium", "evangelium"),
        ("gustav", "gustav"),
    ],
)
def test_historical_spelling_applies_the_rules_in_their_contexts(token, expected):
    assert link.historical_spelling(token) == expected


def test_baseline_exact_match_after_historical_spelling_rules():
    record = dict(FAUST_RECORD, title_spans=["Comödie der Irrungen"])

    row = link_baseline(record, make_work(FAUST_2, ["Komödie der Irrungen"], {GOETHE: GOETHE_NAMES}))

    assert row["status"] == "linked"


def test_fts_query_adds_the_modern_form_of_a_historical_spelling():
    assert link.fts_query("comödie zweyter") == "(comödie OR komödie) (zweyter OR zweiter)"


# --- Creator roles ---


def test_fetch_works_asks_for_every_creator_role():
    backend = FakeBackend()

    link.fetch_works(backend, "faust", 500)

    creator_query = next(q for q in backend.queries if "firstAuthor" in q)
    assert all(f"gnd:{name}" in creator_query for name in link.CREATOR_PREDICATES)


# --- GND classes per type group ---


def test_fetch_works_reads_the_gnd_classes():
    backend = FakeBackend(classes={FAUST_2: ["MusicalWork"]})

    result = link.fetch_works(backend, "faust", 500)

    classes = {w.uri: w.classes for w in result.works}
    assert classes[BASE + FAUST_2] == ["MusicalWork"]
    assert classes[BASE + FAUST_1] == ["Work"]


def test_type_group_keeps_only_the_classes_it_searches():
    record = {"obj_id": "x", "title_spans": ["Faust"], "ddb_creators": [{"uri": BASE + GOUNOD}], "type_group": "music"}
    opera = link.Work(uri=BASE + GOUNOD_FAUST, names=["Faust"], authors={GOUNOD: []}, classes=["MusicalWork"])
    drama = link.Work(uri=BASE + FAUST_2, names=["Faust"], authors={GOUNOD: []}, classes=["Work"])

    row = link_baseline(record, opera, drama)

    assert row["status"] == "linked"
    assert row["gnd_uri"] == BASE + GOUNOD_FAUST
    assert row["type_group"] == "music"


def test_record_without_type_group_searches_every_class():
    record = {"obj_id": "x", "title_spans": ["Faust"], "ddb_creators": [{"uri": BASE + GOUNOD}]}
    opera = link.Work(uri=BASE + GOUNOD_FAUST, names=["Faust"], authors={GOUNOD: []}, classes=["MusicalWork"])

    assert link_baseline(record, opera)["status"] == "linked"


def test_main_unknown_type_group_exits_1(tmp_path, cli, capsys):
    write_jsonl(tmp_path / "spans.jsonl", [dict(FAUST_RECORD, type_group="not_linked")])

    with pytest.raises(SystemExit) as exc:
        cli()

    assert exc.value.code == 1
    assert "which Werk linking does not link" in capsys.readouterr().err


# --- Generic titles ---


def truncated_lookup(*works):
    return lambda term: link.TermResult(works=list(works), truncated=True)


def test_truncated_search_without_link_is_generic_title():
    record = {"obj_id": "x", "title_spans": ["Werke"]}
    works = [make_work(FAUST_1, ["Werke"]), make_work(FAUST_2, ["Werke"])]

    row = link.link_record(record, truncated_lookup(*works), 0.6, 0.85)

    assert row["status"] == "generic_title"
    assert row["truncated"] is True


def test_truncated_search_that_links_keeps_the_link():
    row = link.link_record(
        FAUST_RECORD, truncated_lookup(make_work(FAUST_2, WORK_NAMES[FAUST_2], {GOETHE: GOETHE_NAMES})), 0.6, 0.85
    )

    assert row["status"] == "linked"


def test_truncated_search_with_author_conflict_keeps_author_conflict():
    record = {"obj_id": "x", "title_spans": ["Faust"], "ddb_creators": [{"uri": BASE + GOUNOD}]}

    row = link.link_record(record, truncated_lookup(make_work(FAUST_2, ["Faust"], {GOETHE: []})), 0.6, 0.85)

    assert row["status"] == "author_conflict"


# --- Contributors stand in for a missing creator ---


def test_contributor_confirms_when_creator_is_missing():
    record = {"obj_id": "x", "title_spans": ["Faust, 2. Theil"], "ddb_contributors": [{"uri": BASE + GOETHE}]}

    row = link_baseline(record, make_work(FAUST_2, WORK_NAMES[FAUST_2], {GOETHE: GOETHE_NAMES}))

    assert row["status"] == "linked"
    assert row["author_source"] == "dc_contributor"


def test_creator_is_used_before_contributor():
    record = dict(FAUST_RECORD, ddb_contributors=[{"uri": BASE + GOUNOD}])

    row = link_baseline(record, make_work(FAUST_2, WORK_NAMES[FAUST_2], {GOETHE: GOETHE_NAMES}))

    assert row["status"] == "linked"
    assert row["author_source"] == "dc_creator"


def test_contributor_role_in_parentheses_is_not_part_of_the_name():
    record = {"obj_id": "x", "ddb_contributors": [{"label": "Goethe, Johann Wolfgang von (Verfasser)"}]}
    work = make_work(FAUST_2, ["Faust, 2"], {GOETHE: GOETHE_NAMES})

    assert link.author_check(work, record, 0.85) == "yes"


# --- Term table ---


def test_term_table_survives_a_new_lookup_on_the_same_file(tmp_path):
    backend = FakeBackend()
    table = tmp_path / "terms.sqlite"

    first = link.make_lookup(backend, 500, table)("faust")
    calls = len(backend.fts_calls)
    second = link.make_lookup(backend, 500, table)("faust")

    assert len(backend.fts_calls) == calls
    assert second == first


def test_main_keeps_the_term_table_in_the_parts_folder(tmp_path, cli):
    write_jsonl(tmp_path / "spans.jsonl", faust_records(3))

    assert cli() == 0

    assert (tmp_path / "links.csv.parts" / "terms.sqlite").is_file()
    assert len([c for c in cli.backend.fts_calls]) == len(link.WORK_NAME_PREDICATES)
