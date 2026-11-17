"""Tests for collect_run_provenance.py."""

from __future__ import annotations

import csv
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "py"))
import collect_run_provenance as crp

LINKS = [
    {"obj_id": "A", "status": "linked", "gnd_uri": "https://d-nb.info/gnd/1", "matched_span_source": "isbd_title"},
    {"obj_id": "B", "status": "linked", "gnd_uri": "https://d-nb.info/gnd/1", "matched_span_source": "flair"},
    {"obj_id": "C", "status": "linked", "gnd_uri": "https://d-nb.info/gnd/2", "matched_span_source": "isbd_title"},
    {"obj_id": "D", "status": "no_candidate", "gnd_uri": "", "matched_span_source": ""},
]


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def make_run(tmp_path: Path, offsets: tuple[int, int] = (1, 1)) -> Path:
    """A run folder with two parts on a UTC+1 host, two step 1 shards, a merge record and the links CSV."""
    run = tmp_path / "rerun"
    links = run / "werk_links.csv"
    run.mkdir()
    with open(links, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(LINKS[0]))
        writer.writeheader()
        writer.writerows(LINKS)
    links_sha = hashlib.sha256(links.read_bytes()).hexdigest()
    for i, (start, end, hours) in enumerate(zip(("10:02:05", "10:02:07"), ("09:56:59", "09:58:30"), offsets)):
        local_end = f"{int(end[:2]) + hours:02d}{end[2:]}"
        (run / f"werk_links_part0{i}.log").write_text(
            f"nohup: ignoring input\n2026-11-10 {start},752 INFO Host: load 0.8\n2026-11-10 {local_end},249 INFO Wrote 2 rows -> x.csv\n",
            encoding="utf-8",
        )
        write_json(run / f"werk_links_part0{i}.csv.parts" / "run.json", {"output": {"finished_at": f"2026-11-10T{end}.245104+00:00"}})
    for k, start in ((0, "15:44:27"), (1, "15:44:34")):
        (run / f"werk_spans_shard{k}.log").write_text(f"2026-11-09 {start},985 INFO Shard {k} of 2\n", encoding="utf-8")
        write_json(
            run / "werk_spans" / f"run-shard{k}.json",
            {"input_size": 10, "input_mtime": 1.5, "rule_sha256": "abc", "commit": "c1"},
        )
        (run / "werk_spans" / f"manifest-shard{k}.jsonl").write_text(
            json.dumps({"chunk": k, "finished_at": f"2026-11-09T21:3{k}:07.003888+00:00"}) + "\n", encoding="utf-8"
        )
    write_json(
        run / "werk_links_merge.json",
        {
            "merged_by": {"commit": "c2"},
            "script": {"commit": "c2"},
            "output": {"sha256": links_sha, "statuses": {"linked": 3}, "finished_at": "2026-11-10T10:15:06+00:00"},
        },
    )
    (run / "SHA256SUMS").write_text(f"{links_sha}  werk_links.csv\n{'f' * 64}  werk_spans.jsonl\n", encoding="utf-8")
    return run


HOST_FACTS = {
    "host": "gpu-host",
    "timezone": {"name": "CET", "utc_offset": "+0100"},
    "input_parquet": {"path": "/p.parquet", "sha256": "e" * 64, "size": 10, "mtime": 1.5},
    "rule_script": {"sha256": "abc"},
    "model_caches": [{"path": "/m", "revision": "r1", "download_date": "2026-11-06"}],
    "versions": {"flair": "0.15.1", "torch": "2.7.1+cu118"},
    "gpus": ["GPU A", "GPU B"],
}


def test_run_id_is_the_earliest_part_start_in_utc_from_the_logged_offset(tmp_path: Path) -> None:
    record = crp.build_record(make_run(tmp_path), None)
    assert record["utc_offset_of_logs"]["value"] == "1:00:00"
    assert record["a_linker_run_id"]["value"] == "20261110T090205Z"
    assert record["b_linker_times"]["value"]["parts"]["part00"]["started_at"].startswith("2026-11-10T09:02:05.752")


def test_step1_times_and_id_candidate_come_from_shard_logs_and_manifests(tmp_path: Path) -> None:
    record = crp.build_record(make_run(tmp_path), None)
    shards = record["c_step1_times"]["value"]
    assert shards["shard0"]["started_at"].startswith("2026-11-09T14:44:27")
    assert shards["shard1"]["ended_at"].startswith("2026-11-09T21:31:07")
    assert record["d_step1_id_candidates"]["value"]["utc_start_of_earlier_shard"] == "20261109T144427Z"


def test_without_host_facts_the_host_items_are_listed_missing(tmp_path: Path) -> None:
    record = crp.build_record(make_run(tmp_path), None)
    missing = {k for k, v in record.items() if isinstance(v, dict) and v.get("status") == "missing"}
    assert missing == {"e_input_sha256", "f_model_revision", "g_flair_torch_versions", "j_hosts_and_gpus"}


def test_host_facts_fill_the_items_and_check_the_input_against_step1(tmp_path: Path) -> None:
    record = crp.build_record(make_run(tmp_path), HOST_FACTS)
    assert record["e_input_sha256"]["value"]["size_and_mtime_match_run_shard0"] is True
    assert record["f_model_revision"]["value"][0]["revision"] == "r1"
    assert record["j_hosts_and_gpus"]["value"]["gpus"] == ["GPU A", "GPU B"]


def test_output_checksum_comes_from_sha256sums_when_the_file_is_absent(tmp_path: Path) -> None:
    record = crp.build_record(make_run(tmp_path), None)
    assert record["i_output_sha256"]["value"]["werk_spans.jsonl"] == "f" * 64


def test_parts_with_different_offsets_stop(tmp_path: Path) -> None:
    run = make_run(tmp_path, offsets=(1, 2))
    with pytest.raises(SystemExit):
        crp.build_record(run, None)


def test_unfinished_part_stops(tmp_path: Path) -> None:
    run = make_run(tmp_path)
    (run / "werk_links_part01.log").write_text("2026-11-10 10:02:07,752 INFO Host: load 0.8\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        crp.build_record(run, None)


def test_links_file_that_differs_from_the_merge_record_stops(tmp_path: Path) -> None:
    run = make_run(tmp_path)
    (run / "SHA256SUMS").unlink()
    with open(run / "werk_links.csv", "a", encoding="utf-8") as f:
        f.write("E,no_title,,\n")
    with pytest.raises(SystemExit):
        crp.build_record(run, None)


def test_counts_give_two_triples_per_link_and_three_per_work(tmp_path: Path) -> None:
    table = crp.subgraph_counts(make_run(tmp_path) / "werk_links.csv")
    assert table == [["flair", 1, "", 2], ["isbd_title", 2, "", 4], ["works", "", 2, 6], ["all", 3, 2, 12]]


def test_counts_stop_when_linked_rows_differ_from_the_merge_record(tmp_path: Path) -> None:
    run = make_run(tmp_path)
    merge = json.loads((run / "werk_links_merge.json").read_text())
    merge["output"]["statuses"]["linked"] = 4
    write_json(run / "werk_links_merge.json", merge)
    with pytest.raises(SystemExit):
        crp.main(["counts", "--run-dir", str(run), "--date", "20261110"])


def test_record_refuses_to_overwrite_and_dry_run_writes_nothing(tmp_path: Path) -> None:
    run = make_run(tmp_path)
    assert crp.main(["record", "--run-dir", str(run), "--dry-run"]) == 0
    assert not (run / "provcred_record.json").exists()
    assert crp.main(["record", "--run-dir", str(run)]) == 0
    with pytest.raises(SystemExit):
        crp.main(["record", "--run-dir", str(run)])
    assert crp.main(["record", "--run-dir", str(run), "--force"]) == 0


def test_model_cache_gives_revision_and_download_date_and_skips_the_lock_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    model = tmp_path / ".flair" / "models" / crp.MODEL_DIR_NAME
    (model / "refs").mkdir(parents=True)
    (model / "refs" / "main").write_text("r1\n", encoding="utf-8")
    snapshot = model / "snapshots" / "r1"
    snapshot.mkdir(parents=True)
    (snapshot / "pytorch_model.bin").write_bytes(b"x")
    (tmp_path / ".flair" / "models" / ".locks" / crp.MODEL_DIR_NAME).mkdir(parents=True)
    monkeypatch.setattr(crp.Path, "home", lambda: tmp_path)
    monkeypatch.delenv("HF_HOME", raising=False)
    monkeypatch.delenv("FLAIR_CACHE_ROOT", raising=False)
    caches = crp.model_caches()
    assert len(caches) == 1
    assert caches[0]["revision"] == "r1"
    assert caches[0]["download_date"] is not None


def test_run_dir_that_is_not_a_folder_is_a_usage_error(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as e:
        crp.parse_args(["record", "--run-dir", str(tmp_path / "missing")])
    assert e.value.code == 2


def test_run_id_and_step1_facts_come_from_the_run_records_when_present(tmp_path: Path) -> None:
    run = make_run(tmp_path)
    merge = json.loads((run / "werk_links_merge.json").read_text())
    merge["run_id"] = "20261111T080000Z"
    write_json(run / "werk_links_merge.json", merge)
    write_json(run / "werk_spans" / "run-id.json", {"run_id": "20261111T070000Z", "started_at": "x"})
    settings = json.loads((run / "werk_spans" / "run-shard0.json").read_text())
    settings.update(input_sha256="e" * 64, model_caches=[{"revision": "r1"}], versions={"flair": "0.15.1", "torch": "2.7.1"})
    write_json(run / "werk_spans" / "run-shard0.json", settings)

    record = crp.build_record(run, None)

    assert record["a_linker_run_id"]["value"] == "20261111T080000Z"
    assert record["d_step1_id_candidates"]["value"]["run_id_json"] == "20261111T070000Z"
    assert record["e_input_sha256"]["value"]["sha256"] == "e" * 64
    assert record["f_model_revision"]["value"] == [{"revision": "r1"}]
    assert record["j_hosts_and_gpus"]["status"] == "missing"

