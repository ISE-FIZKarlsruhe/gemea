"""Tests for check_work_links.py and label_work_4766057_0.py."""

from __future__ import annotations

import csv
from pathlib import Path

import check_work_links as cwl
import label_work_4766057_0 as lw
import pytest

pa = pytest.importorskip("pyarrow")
pq = pytest.importorskip("pyarrow.parquet")

WORK = "4766057-0"
A, B, C = "A" * 32, "B" * 32, "C" * 32


def write_parquet(path: Path, rows: list[dict[str, object]]) -> None:
    pq.write_table(pa.Table.from_pylist(rows), path)


def make_inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    run = tmp_path / "run"
    run.mkdir()
    with open(run / "werk_links.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["obj_id", "status", "gnd_uri", "matched_name", "matched_span"])
        writer.writeheader()
        for obj_id, status, gnd in [(A, "linked", WORK), (B, "linked", WORK), (C, "linked", "1-2")]:
            writer.writerow({"obj_id": obj_id, "status": status, "gnd_uri": cwl.GND_PREFIX + gnd,
                             "matched_name": "Geschichte der Deutschen", "matched_span": "Geschichte der Deutschen"})  # fmt: skip
    meta = tmp_path / "meta.parquet"
    rows = []
    for obj_id, title in [(A, "Geschichte der Deutschen, 2. Von Karl dem Großen"), (B, "Geschichte der Deutschen, 12. Neuere Geschichte der Deutschen ; 7"), (C, "Other")]:
        rows.append({"obj_id": obj_id, "title": title, "dc_creator": [{"label": "Schmidt, Michael Ignaz", "uri": "u"}],
                     "dc_contributor": None, "dc_date": None, "dc_issued": "1790", "dc_type": "text",
                     "dc_publisher": None, "hierarchy_type": "Band", "is_part_of": None})  # fmt: skip
    write_parquet(meta, rows)
    gnd = tmp_path / "gnd"
    gnd.mkdir()
    write_parquet(gnd / "gnd_work_names.parquet", [{"gnd_id": WORK, "name": "Geschichte der Teutschen", "kind": "preferred"},
                                                  {"gnd_id": WORK, "name": "Geschichte der Deutschen", "kind": "variant"}])  # fmt: skip
    write_parquet(gnd / "gnd_work_creators.parquet", [{"gnd_id": WORK, "creator_id": "119056151", "role": "firstAuthor"}])
    write_parquet(gnd / "gnd_creator_names.parquet", [{"gnd_id": "119056151", "name": "Schmidt, Michael Ignaz", "kind": "preferred"}])
    return run, meta, gnd


def run_check(run: Path, meta: Path, gnd: Path, *extra: str) -> int:
    return cwl.main(["--gnd-id", WORK, "--run-dir", str(run), "--meta", str(meta), "--gnd-dir", str(gnd), "--date", "20261116", *extra])


def read_csv(path: Path) -> list[dict[str, str]]:
    return lw.read_rows(path)


def test_check_extracts_only_this_works_links_with_metadata_and_gnd(tmp_path: Path) -> None:
    run, meta, gnd = make_inputs(tmp_path)

    assert run_check(run, meta, gnd) == 0

    rows = read_csv(run / f"work_links_check_{WORK}_20261116.csv")
    assert [r["obj_id"] for r in rows] == [B, A]  # sorted by title
    assert rows[1]["dc_creator"] == "Schmidt, Michael Ignaz <u>"
    assert rows[1]["gnd_preferred_name"] == "Geschichte der Teutschen"
    assert rows[1]["gnd_creators"] == "firstAuthor: Schmidt, Michael Ignaz (119056151)"
    assert rows[1]["check_label"] == ""


def test_labels_then_check_writes_labels_note_and_summary(tmp_path: Path) -> None:
    run, meta, gnd = make_inputs(tmp_path)
    run_check(run, meta, gnd)
    extract = run / f"work_links_check_{WORK}_20261116.csv"
    labels = tmp_path / "labels.csv"

    assert lw.main(["--input", str(extract), "--output", str(labels)]) == 0
    assert run_check(run, meta, gnd, "--labels", str(labels), "--force") == 0

    assert extract.read_text(encoding="utf-8").startswith("# check_label and check_reason are an AI's judgement")
    by_id = {r["obj_id"]: r["check_label"] for r in read_csv(extract)}
    assert by_id == {A: "part_or_volume", B: "other_work"}
    summary = {r["check_label"]: r["records"] for r in read_csv(run / f"work_links_check_{WORK}_20261116_summary.csv")}
    assert summary == {"same_work": "0", "other_work": "1", "part_or_volume": "1", "edition_or_translation": "0", "unsure": "0"}


@pytest.mark.parametrize(
    "title, dc_type, expected",
    [
        ("Geschichte der Deutschen", "mehrbändiges Werk", "same_work"),
        ("Geschichte der Deutschen, 5. Vom Anfange des Schmalkaldischen Kriegs", "text", "part_or_volume"),
        ("Geschichte der Deutschen, [12,a]. Vollständige Register", "text", "part_or_volume"),
        ("Michael Ignaz Schmidts neuere Geschichte der Deutschen, 3.", "text", "other_work"),
        ("Geschichte der Deutschen, 18. Kaiser Karl VII.", "Bücheranzeige", "other_work"),
        ("Geschichte der Deutschen", "text", "unsure"),
    ],
)
def test_label_judge(title: str, dc_type: str, expected: str) -> None:
    label, _ = lw.judge({"title": title, "dc_issued": "1790", "dc_type": dc_type})

    assert label == expected


def test_check_missing_label_stops(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    run, meta, gnd = make_inputs(tmp_path)
    labels = tmp_path / "labels.csv"
    labels.write_text(f"obj_id,check_label,check_reason\n{A},same_work,x\n", encoding="utf-8")

    with pytest.raises(SystemExit) as stop:
        run_check(run, meta, gnd, "--labels", str(labels))

    assert stop.value.code == 1
    assert "1 linked records have no label" in caplog.text


def test_check_unknown_label_stops(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    run, meta, gnd = make_inputs(tmp_path)
    labels = tmp_path / "labels.csv"
    labels.write_text(f"obj_id,check_label,check_reason\n{A},wrong,x\n{B},same_work,x\n", encoding="utf-8")

    with pytest.raises(SystemExit):
        run_check(run, meta, gnd, "--labels", str(labels))

    assert "use one of" in caplog.text


def test_check_dry_run_writes_nothing_and_existing_output_stops(tmp_path: Path) -> None:
    run, meta, gnd = make_inputs(tmp_path)

    assert run_check(run, meta, gnd, "--dry-run") == 0
    assert not list(run.glob("work_links_check_*"))
    run_check(run, meta, gnd)
    with pytest.raises(SystemExit):
        run_check(run, meta, gnd)


def test_check_unknown_work_stops(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    run, meta, gnd = make_inputs(tmp_path)

    with pytest.raises(SystemExit):
        cwl.main(["--gnd-id", "9-9", "--run-dir", str(run), "--meta", str(meta), "--gnd-dir", str(gnd)])

    assert "no linked rows" in caplog.text
