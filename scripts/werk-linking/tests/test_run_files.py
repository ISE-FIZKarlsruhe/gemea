"""Tests for run_files.py."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import run_files
import run_folder as rf


@pytest.fixture(autouse=True)
def no_git(monkeypatch: pytest.MonkeyPatch) -> None:
    """MIN_TERM_LENGTH comes from git; the tests give it a fixed value instead."""
    monkeypatch.setattr(run_files, "min_term_length", lambda repo, commit: 3)


def test_load_run_reads_every_fact(tmp_path: Path) -> None:
    run = rf.make_run(tmp_path)

    facts, gaps = run_files.load_run(run, tmp_path)

    assert gaps == []
    assert (facts.run_id, facts.step1_id) == (rf.RUN_ID, rf.STEP1_ID)
    assert (facts.linker_start, facts.linker_end) == ("2026-11-10T09:02:05Z", "2026-11-10T09:58:53Z")
    assert (facts.step1_start, facts.step1_end) == ("2026-11-09T14:44:27Z", "2026-11-09T21:34:07Z")
    assert (facts.input_name, facts.input_sha256) == ("s2_meta_de_content", rf.INPUT_SHA256)
    assert (facts.model_revision, facts.model_download_date) == (rf.HF_REVISION, "2026-11-06")
    assert facts.tables_sha256 == rf.TABLES_SHA256
    assert facts.dump_date == "2026-10-13"
    assert (facts.decision, facts.name_threshold, facts.search_size, facts.min_score) == ("baseline", 0.85, 500, 0.6)
    assert facts.min_term_length == 3
    assert facts.linked_rows == 4


def test_load_run_prefers_step1_run_id_json(tmp_path: Path) -> None:
    run = rf.make_run(tmp_path)
    rf.change_json(
        run / "provcred_record.json",
        lambda r: r["d_step1_id_candidates"]["value"].update(run_id_json="20261111T080000Z"),
    )

    facts, _ = run_files.load_run(run, tmp_path)

    assert facts.step1_id == "20261111T080000Z"


def test_load_run_missing_optional_value_is_a_gap(tmp_path: Path) -> None:
    run = rf.make_run(tmp_path)
    rf.change_json(run / "provcred_record.json", lambda r: r["h_rule_script"].update(status="missing", value=None))

    facts, gaps = run_files.load_run(run, tmp_path)

    assert facts.rule_sha256 is None
    assert gaps == ["rule_sha256"]


def test_load_run_dumps_with_different_dates_give_no_date(tmp_path: Path) -> None:
    run = rf.make_run(tmp_path)
    rf.change_json(run / "werk_links_merge.json", lambda m: m["gnd"]["dumps"]["person"].update(date="2026-06-13"))

    facts, gaps = run_files.load_run(run, tmp_path)

    assert facts.dump_date is None
    assert "dump_date" in gaps


def drop_run_id(record: dict[str, Any]) -> None:
    record["a_linker_run_id"].update(status="missing", value=None)


def drop_commit(record: dict[str, Any]) -> None:
    record["commits"]["value"]["step2"] = None


def two_revisions(record: dict[str, Any]) -> None:
    record["f_model_revision"]["value"][1]["revision"] = "f" * 40


@pytest.mark.parametrize("change, phrase", [(drop_run_id, "linker run ID"), (drop_commit, "step 2 commit"), (two_revisions, "2 model revisions")])
def test_load_run_stops_on_missing_or_conflicting_record(tmp_path: Path, caplog: pytest.LogCaptureFixture, change: Any, phrase: str) -> None:
    run = rf.make_run(tmp_path)
    rf.change_json(run / "provcred_record.json", change)

    with pytest.raises(SystemExit) as stop:
        run_files.load_run(run, tmp_path)

    assert stop.value.code == 1
    assert phrase in caplog.text


def test_load_run_stops_without_links_checksum(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    run = rf.make_run(tmp_path)
    rf.change_json(run / "werk_links_merge.json", lambda m: m["output"].pop("sha256"))

    with pytest.raises(SystemExit):
        run_files.load_run(run, tmp_path)

    assert "SHA-256 of werk_links.csv" in caplog.text


def test_load_run_stops_on_a_run_that_reused_flair(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    run = rf.make_run(tmp_path)
    (run / "werk_spans").mkdir()
    rf.write_json(run / "werk_spans" / "run-shard0.json", {"flair_from": {"folder": "rerun-d38/werk_spans"}})

    with pytest.raises(SystemExit):
        run_files.load_run(run, tmp_path)

    assert "flair_from" in caplog.text


def test_utc_seconds_converts_offset_and_cuts_to_the_second() -> None:
    assert run_files.utc_seconds("2026-11-10T10:02:05.999000+01:00") == "2026-11-10T09:02:05Z"


def test_min_term_length_without_git_history_is_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.undo()

    assert run_files.min_term_length(tmp_path, "0" * 40) is None
