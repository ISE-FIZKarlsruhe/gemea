# Purpose:      Tests for scripts/py/sample_werk_links.py, on a small links CSV, metadata parquet and
#               GND tables written into tmp_path.
# Usage:        .venv/bin/python -m pytest scripts/tests/test_sample_werk_links.py -q
# Dependencies: pytest, pyarrow, rapidfuzz, openpyxl

from __future__ import annotations

import csv
import sys
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from openpyxl import load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "py"))
import link_gnd_local as local
import sample_werk_links as sample

AGENT = pa.list_(pa.struct([("label", pa.string()), ("uri", pa.string())]))


def link_row(obj_id: str, status: str, gnd: str = "", **fields: str) -> dict[str, str]:
    row = dict.fromkeys(local.OUTPUT_COLUMNS, "")
    row.update(obj_id=obj_id, status=status, type_group="work", candidate_count="1", truncated="False",
               gnd_uri=f"https://d-nb.info/gnd/{gnd}" if gnd else "", search_terms="faust | eine tragödie")  # fmt: skip
    row.update(fields)
    return row


@pytest.fixture
def inputs(tmp_path: Path) -> dict[str, Path]:
    folder = tmp_path / "data dir"
    folder.mkdir()
    links = folder / "werk_links.csv"
    rows = [link_row(f"L{i}", "linked", gnd="W1") for i in range(5)]
    rows += [link_row(f"C{i}", "author_conflict") for i in range(3)]
    rows += [link_row("N1", "no_candidate")]
    with open(links, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=local.OUTPUT_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    ids = [r["obj_id"] for r in rows]
    meta = folder / "meta.parquet"
    pq.write_table(pa.table({
        "obj_id": ids,
        "title": [f"Faust {i}" for i in ids],
        "dc_creator": pa.array([[{"label": "Goethe, Johann Wolfgang von", "uri": "https://d-nb.info/gnd/118540238"}]]
                               * len(ids), type=AGENT),
        "dc_contributor": pa.array([[]] * len(ids), type=AGENT),
        "dc_date": ["1808"] * len(ids),
        "lang": ["ger"] * len(ids),
    }), meta)  # fmt: skip

    gnd = folder / "gnd"
    gnd.mkdir()
    pq.write_table(pa.table({"gnd_id": ["W1", "W1"], "kind": ["preferred", "variant"],
                             "name": ["Faust", "Faust I"]}), gnd / "gnd_work_names.parquet")  # fmt: skip
    pq.write_table(pa.table({"gnd_id": ["W1"], "role": ["firstAuthor"], "creator_id": ["118540238"]}),
                   gnd / "gnd_work_creators.parquet")  # fmt: skip
    pq.write_table(pa.table({"gnd_id": ["118540238"], "entity": ["person"], "kind": ["preferred"],
                             "name": ["Goethe, Johann Wolfgang von"]}), gnd / "gnd_creator_names.parquet")  # fmt: skip
    return {"links": links, "meta": meta, "gnd": gnd, "output": folder / "sample.xlsx"}


def argv(inputs: dict[str, Path], *extra: str) -> list[str]:
    return ["--links", str(inputs["links"]), "--meta", str(inputs["meta"]), "--gnd-dir", str(inputs["gnd"]),
            "--output", str(inputs["output"]), "--sample", "linked=2", "--sample", "author_conflict=5", *extra]  # fmt: skip


def sheet_rows(path: Path, name: str) -> list[dict[str, Any]]:
    sheet = load_workbook(path)[name]
    values = list(sheet.values)
    return [dict(zip(values[0], row)) for row in values[1:]]


# --- Pure logic ---


def test_parse_sample_keeps_the_order_given():
    assert list(sample.parse_sample(["below_threshold=5", "linked=10"]).items()) == [
        ("below_threshold", 5),
        ("linked", 10),
    ]


@pytest.mark.parametrize("value", ["linkd=10", "linked=", "linked=0", "linked"])
def test_parse_sample_rejects_bad_pairs(value):
    with pytest.raises(ValueError):
        sample.parse_sample([value])


def test_search_url_uses_the_first_term_and_restricts_to_works():
    url = sample.search_url("faust | eine tragödie")

    assert url == "https://lobid.org/gnd/search?q=faust&filter=type%3AWork"


@pytest.mark.parametrize("title, expected", [
    ("Geschichte der Stadt Rom im Mittelalter : vom 5. bis zum 16. Jahrhundert, 4",
     "Geschichte der Stadt Rom im Mittelalter"),
    ("Faust / Goethe : eine Tragödie", "Faust"),
    ("Die fünff Bücher Mose", "Die fünff Bücher Mose"),
    ("", ""),
])  # fmt: skip
def test_title_proper_cuts_at_the_first_isbd_separator(title, expected):
    assert sample.title_proper(title) == expected


def test_title_search_url_searches_the_title_proper_and_is_empty_without_a_title():
    url = sample.title_search_url("Faust : eine Tragödie")

    assert url == "https://lobid.org/gnd/search?q=Faust&filter=type%3AWork"
    assert sample.title_search_url("") == ""


def test_agents_formats_label_and_uri():
    assert sample.agents([{"label": "A", "uri": "u"}, {"label": "B", "uri": None}]) == "A <u>; B"
    assert sample.agents(None) == ""


# --- Happy path ---


def test_main_writes_one_sheet_per_status_with_ddb_and_gnd_columns(inputs):
    assert sample.main(argv(inputs)) == 0

    book = load_workbook(inputs["output"])
    assert book.sheetnames == ["linked", "author_conflict", "labels", "run"]
    linked = sheet_rows(inputs["output"], "linked")
    assert len(linked) == 2
    first = linked[0]
    assert first["ddb_url"] == f"https://www.deutsche-digitale-bibliothek.de/item/{first['obj_id']}"
    assert first["title"] == f"Faust {first['obj_id']}"
    assert first["dc_creator"] == "Goethe, Johann Wolfgang von <https://d-nb.info/gnd/118540238>"
    assert first["gnd_preferred_name"] == "Faust"
    assert first["gnd_creators"] == "firstAuthor: Goethe, Johann Wolfgang von (118540238)"
    assert book["linked"]["B2"].hyperlink.target == first["ddb_url"]


def test_main_takes_every_row_when_the_status_has_fewer_than_asked(inputs):
    sample.main(argv(inputs))

    conflicts = sheet_rows(inputs["output"], "author_conflict")
    assert sorted(r["obj_id"] for r in conflicts) == ["C0", "C1", "C2"]
    assert all(r["gnd_url"] is None and r["gnd_preferred_name"] is None for r in conflicts)


def test_main_same_seed_gives_the_same_sample_and_another_seed_may_not(inputs):
    sample.main(argv(inputs, "--seed", "7"))
    first = [r["obj_id"] for r in sheet_rows(inputs["output"], "linked")]
    sample.main(argv(inputs, "--seed", "7", "--force"))

    assert [r["obj_id"] for r in sheet_rows(inputs["output"], "linked")] == first


def test_main_records_population_and_seed_in_the_run_sheet(inputs):
    sample.main(argv(inputs))

    run = {row[0]: row[1] for row in load_workbook(inputs["output"])["run"].values}
    assert run["sample_linked"] == "2 of 5"
    assert run["sample_author_conflict"] == "3 of 3"
    assert run["seed"] == sample.DEFAULT_SEED
    assert run["links_sha256"] == local.sha256(inputs["links"])


def test_main_adds_a_label_dropdown_with_the_sheet_labels(inputs):
    sample.main(argv(inputs))

    validations = load_workbook(inputs["output"])["linked"].data_validations.dataValidation
    assert len(validations) == 1
    assert "wrong_work" in validations[0].formula1


def test_main_no_candidate_sheet_has_title_search_and_span_ok_before_label(inputs):
    sample.main(argv(inputs, "--sample", "no_candidate=1"))

    sheet = load_workbook(inputs["output"])["no_candidate"]
    header = [cell.value for cell in sheet[1]]
    assert header[-4:] == ["title_search_url", "span_ok", "label", "note"]
    row = sheet_rows(inputs["output"], "no_candidate")[0]
    assert row["title_search_url"] == "https://lobid.org/gnd/search?q=Faust+N1&filter=type%3AWork"
    formulas = [v.formula1 for v in sheet.data_validations.dataValidation]
    assert any("uniform_title" in f and "tokenisation" in f for f in formulas)
    assert '"y,n,na"' in formulas


def test_main_linked_sheet_keeps_its_columns(inputs):
    sample.main(argv(inputs))

    header = [cell.value for cell in load_workbook(inputs["output"])["linked"][1]]
    assert header == [column for column, _ in sample.SHEET_COLUMNS]


def test_main_dry_run_writes_nothing(inputs, capsys):
    assert sample.main(argv(inputs, "--dry-run")) == 0

    assert "would write" in capsys.readouterr().out
    assert not inputs["output"].exists()


def test_main_refuses_to_overwrite_a_sheet_without_force(inputs, capsys):
    sample.main(argv(inputs))

    with pytest.raises(SystemExit) as exc:
        sample.main(argv(inputs))

    assert exc.value.code == 1
    assert "may hold labels" in capsys.readouterr().err


# --- Failure modes ---


def test_main_missing_gnd_table_exits_1(inputs, capsys):
    (inputs["gnd"] / "gnd_creator_names.parquet").unlink()

    with pytest.raises(SystemExit) as exc:
        sample.main(argv(inputs))

    assert exc.value.code == 1
    assert "gnd_creator_names.parquet" in capsys.readouterr().err


def test_main_links_with_other_columns_exit_1(inputs, capsys):
    inputs["links"].write_text("obj_id,status\nL0,linked\n", encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        sample.main(argv(inputs))

    assert exc.value.code == 1
    assert "not those of link_gnd_local.py" in capsys.readouterr().err
    assert not inputs["output"].exists()


@pytest.mark.parametrize("extra", [["--sample", "linkd=5"], ["--bogus"]])
def test_main_bad_arguments_exit_2(inputs, extra):
    with pytest.raises(SystemExit) as exc:
        sample.main(argv(inputs, *extra))

    assert exc.value.code == 2
