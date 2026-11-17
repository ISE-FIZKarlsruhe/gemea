# Purpose:      Tests for scripts/py/merge_link_parts.py, on small part outputs written into tmp_path in
#               the layout link_gnd_local.py leaves behind.
# Usage:        .venv/bin/python -m pytest scripts/tests/test_merge_link_parts.py -q
# Dependencies: pytest, pyarrow, rapidfuzz

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "py"))
import link_gnd_local as local
import merge_link_parts as merge

SHARED: dict[str, Any] = {
    "backend": "local",
    "script": {"commit": "79019ee", "uncommitted_changes": False, "sha256": {"a.py": "x"}},
    "gnd": {"tables": {"gnd_work_names.parquet": "y"}},
    "settings": {"chunk_size": 1000, "decision": "baseline"},
    "versions": {"python": "3.12"},
}


def link_row(obj_id: str, status: str, group: str = "work", score: str = "", author: str = "") -> dict[str, str]:
    row = dict.fromkeys(local.OUTPUT_COLUMNS, "")
    row.update(obj_id=obj_id, status=status, type_group=group, score=score, author_match=author)
    return row


def write_csv(path: Path, columns: list[str], rows: list[dict[str, Any]]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def write_part(
    folder: Path, name: str, rows: list[dict[str, str]], summary: list[dict[str, Any]], timing: list[dict[str, Any]],
    **overrides: Any,
) -> Path:  # fmt: skip
    """A finished part: output CSV, its two side CSVs and <part>.parts/run.json."""
    part = folder / name
    write_csv(part, local.OUTPUT_COLUMNS, rows)
    summary_path, timing_path = local.side_outputs(part)
    write_csv(summary_path, local.SUMMARY_COLUMNS, summary)
    write_csv(timing_path, local.TIMING_COLUMNS, timing)
    state = {**SHARED, "input": {"path": f"{name}.jsonl", "records": len(rows)}, **overrides}
    state["output"] = {"sha256": local.sha256(part), "rows": len(rows), "statuses": {}}
    parts_dir = part.with_name(part.name + ".parts")
    parts_dir.mkdir()
    (parts_dir / "run.json").write_text(json.dumps(state), encoding="utf-8")
    return part


def timing(term: str, records: int, names: int, score: float, candidates: int = 600) -> dict[str, Any]:
    return {"term": term, "records": records, "candidates": candidates, "names_scored": names,
            "truncated": 1, "lookup_seconds": 0.5, "score_seconds": score}  # fmt: skip


@pytest.fixture
def parts(tmp_path: Path) -> dict[str, Any]:
    folder = tmp_path / "nerl gnd"
    folder.mkdir()
    first = write_part(
        folder, "werk_links_part00.csv",
        [link_row("a1", "linked", score="1.0", author="yes"), link_row("a2", "no_candidate")],
        [{"type_group": "work", "status": "linked", "records": 1, "high_confidence": 1},
         {"type_group": "work", "status": "no_candidate", "records": 1, "high_confidence": 0},
         {"type_group": "all", "status": "linked", "records": 1, "high_confidence": 1},
         {"type_group": "all", "status": "no_candidate", "records": 1, "high_confidence": 0}],
        [timing("die", 1, 100, 2.0), timing("faust", 1, 10, 0.1, candidates=484)],
    )  # fmt: skip
    second = write_part(
        folder, "werk_links_part01.csv",
        [link_row("b1", "no_candidate", group="music"), link_row("b2", "linked", score="1.0", author="yes")],
        [{"type_group": "music", "status": "no_candidate", "records": 1, "high_confidence": 0},
         {"type_group": "work", "status": "linked", "records": 1, "high_confidence": 1},
         {"type_group": "all", "status": "linked", "records": 1, "high_confidence": 1},
         {"type_group": "all", "status": "no_candidate", "records": 1, "high_confidence": 0}],
        [timing("die", 2, 300, 5.0)],
    )  # fmt: skip
    spans = folder / "werk_spans.jsonl"
    spans.write_text("".join(f'{{"obj_id": "{i}"}}\n' for i in ("a1", "a2", "b1", "b2")), encoding="utf-8")
    return {"folder": folder, "parts": [first, second], "input": spans, "output": folder / "werk_links.csv"}


def argv(parts: dict[str, Any], *extra: str) -> list[str]:
    return ["--parts", *map(str, parts["parts"]), "--output", str(parts["output"]), "--input", str(parts["input"]),
            *extra]  # fmt: skip


def read(path: Path) -> list[dict[str, str]]:
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


# --- Happy path ---


def test_main_joins_the_parts_in_order(parts):
    assert merge.main(argv(parts)) == 0

    assert [r["obj_id"] for r in read(parts["output"])] == ["a1", "a2", "b1", "b2"]


def test_main_sums_the_status_summary_and_recomputes_the_totals(parts):
    merge.main(argv(parts))

    rows = {(r["type_group"], r["status"]): r for r in read(local.side_outputs(parts["output"])[0])}
    assert rows[("work", "linked")]["records"] == "2"
    assert rows[("work", "linked")]["high_confidence"] == "2"
    assert rows[("music", "no_candidate")]["records"] == "1"
    assert rows[("all", "no_candidate")]["records"] == "2"
    assert rows[("all", "linked")]["high_confidence"] == "2"


def test_main_sums_the_term_timing_slowest_first(parts):
    merge.main(argv(parts))

    rows = read(local.side_outputs(parts["output"])[1])
    assert [r["term"] for r in rows] == ["die", "faust"]
    assert rows[0] == {"term": "die", "records": "3", "candidates": "600", "names_scored": "400", "truncated": "1",
                       "lookup_seconds": "1.0", "score_seconds": "7.0"}  # fmt: skip
    assert rows[1]["candidates"] == "484"


def test_main_records_the_parts_and_the_output(parts):
    merge.main(argv(parts))

    record = json.loads(parts["output"].with_name("werk_links_merge.json").read_text(encoding="utf-8"))
    assert record["output"]["rows"] == 4
    assert record["output"]["statuses"] == {"linked": 2, "no_candidate": 2}
    assert record["output"]["sha256"] == local.sha256(parts["output"])
    assert [p["output"]["rows"] for p in record["parts"]] == [2, 2]
    assert record["settings"] == SHARED["settings"]


def test_main_dry_run_writes_nothing(parts, capsys):
    assert merge.main(argv(parts, "--dry-run")) == 0

    assert "total\t4 rows" in capsys.readouterr().out
    assert not parts["output"].exists()


def test_main_refuses_to_overwrite_without_force(parts, capsys):
    merge.main(argv(parts))

    with pytest.raises(SystemExit) as exc:
        merge.main(argv(parts))

    assert exc.value.code == 1
    assert "--force" in capsys.readouterr().err
    assert merge.main(argv(parts, "--force")) == 0


# --- Failure modes ---


def test_main_unfinished_part_exits_1(parts, capsys):
    state_path = parts["parts"][1].with_name("werk_links_part01.csv.parts") / "run.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    del state["output"]
    state_path.write_text(json.dumps(state), encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        merge.main(argv(parts))

    assert exc.value.code == 1
    assert "has not finished" in capsys.readouterr().err


def test_main_changed_part_exits_1(parts, capsys):
    with open(parts["parts"][0], "a", encoding="utf-8") as f:
        f.write("x" + "," * (len(local.OUTPUT_COLUMNS) - 1) + "\n")

    with pytest.raises(SystemExit) as exc:
        merge.main(argv(parts))

    assert exc.value.code == 1
    assert "SHA-256" in capsys.readouterr().err


def test_main_parts_with_other_settings_exit_1(parts, capsys):
    other = write_part(parts["folder"], "werk_links_part02.csv", [link_row("c1", "linked")], [], [],
                       settings={"chunk_size": 500, "decision": "baseline"})  # fmt: skip

    with pytest.raises(SystemExit) as exc:
        merge.main(["--parts", str(parts["parts"][0]), str(other), "--output", str(parts["output"])])

    assert exc.value.code == 1
    assert "other settings" in capsys.readouterr().err


def test_main_duplicate_obj_id_exits_1_without_output(parts, capsys):
    duplicate = write_part(parts["folder"], "werk_links_part02.csv", [link_row("a1", "linked")], [], [])

    with pytest.raises(SystemExit) as exc:
        merge.main(["--parts", str(parts["parts"][0]), str(duplicate), "--output", str(parts["output"])])

    assert exc.value.code == 1
    assert "more than one part" in capsys.readouterr().err
    assert not parts["output"].exists()
    assert not list(parts["folder"].glob("*.tmp"))


def test_main_input_line_count_differs_exits_1(parts, capsys):
    with open(parts["input"], "a", encoding="utf-8") as f:
        f.write('{"obj_id": "z9"}\n')

    with pytest.raises(SystemExit) as exc:
        merge.main(argv(parts))

    assert exc.value.code == 1
    assert "a part is missing" in capsys.readouterr().err
    assert not parts["output"].exists()


@pytest.mark.parametrize("extra", [[], ["--parts", "only_one.csv", "--output", "out.csv"]])
def test_main_bad_arguments_exit_2(extra):
    with pytest.raises(SystemExit) as exc:
        merge.main(extra)

    assert exc.value.code == 2


# --- Run ID ---


def give_run_id(part: Path, run_id: str, started_at: str) -> None:
    path = part.with_name(part.name + ".parts") / "run-id.json"
    path.write_text(json.dumps({"run_id": run_id, "started_at": started_at}), encoding="utf-8")


def test_main_records_the_shared_run_id_and_earliest_start(parts):
    give_run_id(parts["parts"][0], "20261110T090205Z", "2026-11-10T09:02:06+00:00")
    give_run_id(parts["parts"][1], "20261110T090205Z", "2026-11-10T09:02:05+00:00")

    assert merge.main(argv(parts)) == 0

    record = json.loads(parts["output"].with_name("werk_links_merge.json").read_text(encoding="utf-8"))
    assert record["run_id"] == "20261110T090205Z"
    assert record["started_at"] == "2026-11-10T09:02:05+00:00"


def test_main_parts_with_different_run_ids_exit_1(parts, capsys):
    give_run_id(parts["parts"][0], "20261110T090205Z", "a")
    give_run_id(parts["parts"][1], "20261110T090206Z", "b")

    with pytest.raises(SystemExit) as exc:
        merge.main(argv(parts))

    assert exc.value.code == 1
    assert "--run-id" in capsys.readouterr().err
    assert not parts["output"].exists()


def test_main_only_some_parts_with_a_run_id_exit_1(parts):
    give_run_id(parts["parts"][0], "20261110T090205Z", "a")

    with pytest.raises(SystemExit) as exc:
        merge.main(argv(parts))

    assert exc.value.code == 1


def test_main_parts_from_before_run_ids_record_null(parts):
    assert merge.main(argv(parts)) == 0

    record = json.loads(parts["output"].with_name("werk_links_merge.json").read_text(encoding="utf-8"))
    assert record["run_id"] is None

