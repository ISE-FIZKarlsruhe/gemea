# Purpose:      Tests for scripts/py/gnd_jsonld_to_parquet.py, the converter of local GND retrieval
#               on tiny dumps written into tmp_path.
# Usage:        .venv/bin/python -m pytest scripts/tests/test_gnd_jsonld_to_parquet.py -q
# Dependencies: pytest, ijson, pyarrow, rapidfuzz

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "py"))
import gnd_jsonld_to_parquet as conv

GND = "https://d-nb.info/gnd/"
GNDO = "https://d-nb.info/standards/elementset/gnd#"
DNB = "https://d-nb.info/standards/elementset/dnb#"
GOETHE = "118540238"
ECKERMANN = "118528998"
EDITOR = "118566288"
OLD_ID = "17418154X"
CURRENT_ID = "1325000884"
CORPORATE = "2001234-5"
FAUST = "4099198-2"
SONG = "300061099"


def ref(gnd_id: str) -> list[dict[str, str]]:
    return [{"@id": f"{GND}{gnd_id}"}]


def text(*values: str) -> list[dict[str, str]]:
    return [{"@value": v} for v in values]


WORK_NODES: list[dict[str, Any]] = [
    # Reified relation: repeated on a blank node, must not count.
    {"@id": "_:node1", f"{GNDO}firstAuthor": ref(GOETHE)},
    {
        "@id": f"{GND}{FAUST}",
        "@type": [f"{GNDO}Work"],
        f"{GNDO}preferredNameForTheWork": text("Faust, 2"),
        # "Räuber" in NFD (a + U+0308), kept as in the dump.
        f"{GNDO}variantNameForTheWork": text("Faust. 2. Teil", "Der Räuber"),
        f"{GNDO}abbreviatedNameForTheWork": text("F. 2"),
        f"{GNDO}firstAuthor": ref(GOETHE),
        f"{GNDO}author": ref(ECKERMANN),
        f"{GNDO}editor": ref(EDITOR),
    },
    {
        "@id": f"{GND}{SONG}",
        "@type": [f"{GNDO}MusicalWork"],
        f"{GNDO}preferredNameForTheWork": text("Lied"),
        f"{GNDO}composer": ref(CORPORATE),
        f"{GNDO}firstComposer": [{"@id": f"http://d-nb.info/gnd/{OLD_ID}"}],
    },
]
PERSON_NODES: list[dict[str, Any]] = [
    {"@id": "_:node9", f"{GNDO}forename": text("Johann")},
    {
        "@id": f"{GND}{GOETHE}",
        f"{GNDO}preferredNameForThePerson": text("Goethe, Johann Wolfgang von"),
        f"{GNDO}variantNameForThePerson": text("Goethe, J. W. von"),
    },
    {"@id": f"{GND}{ECKERMANN}", f"{GNDO}preferredNameForThePerson": text("Eckermann, Johann Peter")},
    {"@id": f"{GND}{EDITOR}", f"{GNDO}preferredNameForThePerson": text("Not a creator")},
    {
        "@id": f"{GND}{CURRENT_ID}",
        f"{GNDO}preferredNameForThePerson": text("Merged, Person"),
        f"{DNB}deprecatedUri": text(f"{GND}{OLD_ID}", f"{GND}999"),
    },
]
CORPORATE_NODES: list[dict[str, Any]] = [
    {"@id": "_:node5", f"{GNDO}preferredNameForTheCorporateBody": text("blank")},
    {
        "@id": f"{GND}{CORPORATE}",
        "@type": [f"{GNDO}MusicalCorporateBody"],
        f"{GNDO}preferredNameForTheCorporateBody": text("Berliner Philharmoniker"),
        f"{GNDO}variantNameForTheCorporateBody": text("Philharmonisches Orchester Berlin"),
    },
    {"@id": f"{GND}2009999-0", f"{GNDO}preferredNameForTheCorporateBody": text("Not a creator")},
]


def write_dump(path: Path, nodes: list[dict[str, Any]]) -> Path:
    # Two inner arrays, pretty-printed like the DNB dumps: [ [ {...} ], [ {...} ] ].
    path.write_text(json.dumps([nodes[:1], nodes[1:]], indent=2, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture
def dumps(tmp_path: Path) -> dict[str, Path]:
    return {
        "werk": write_dump(tmp_path / "werk_lds-20261013.jsonld", WORK_NODES),
        "person": write_dump(tmp_path / "person lds-20261013.jsonld", PERSON_NODES),
        "koerperschaft": write_dump(tmp_path / "koerperschaft lds-20261013.jsonld", CORPORATE_NODES),
        "out": tmp_path / "parquet out",
        "csv": tmp_path / "unresolved.csv",
    }


def argv(dumps: dict[str, Path], *extra: str) -> list[str]:
    return [
        "--werk", str(dumps["werk"]), "--person", str(dumps["person"]), "--koerperschaft", str(dumps["koerperschaft"]),
        "--out-dir", str(dumps["out"]), "--unresolved-csv", str(dumps["csv"]), *extra,
    ]  # fmt: skip


def rows(path: Path) -> list[tuple[Any, ...]]:
    table = pq.read_table(path)
    return sorted(zip(*(table.column(name).to_pylist() for name in table.column_names)))


def manifest(dumps: dict[str, Path]) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((dumps["out"] / "manifest.json").read_text(encoding="utf-8"))
    return data


# --- Pure functions ---


def test_work_rows_reads_names_classes_and_creator_roles_only():
    names, classes, creators = conv.work_rows(WORK_NODES[1])

    assert names == [
        (FAUST, "preferred", "Faust, 2"),
        (FAUST, "variant", "Faust. 2. Teil"),
        (FAUST, "variant", "Der Räuber"),
    ]
    assert classes == [(FAUST, "Work")]
    assert creators == [(FAUST, "firstAuthor", GOETHE), (FAUST, "author", ECKERMANN)]


def test_work_rows_skips_blank_nodes():
    assert conv.work_rows(WORK_NODES[0]) == ([], [], [])


def test_work_rows_accepts_http_creator_uri():
    _, _, creators = conv.work_rows(WORK_NODES[2])

    assert (SONG, "firstComposer", OLD_ID) in creators


def test_agent_rows_keeps_only_wanted_agents():
    names, deprecated = conv.agent_rows(PERSON_NODES[3], {GOETHE}, "person", conv.PERSON_NAMES)

    assert names == []
    assert deprecated == []


def test_agent_rows_reports_wanted_deprecated_uris_only():
    names, deprecated = conv.agent_rows(PERSON_NODES[4], {OLD_ID, GOETHE}, "person", conv.PERSON_NAMES)

    assert names == []
    assert deprecated == [OLD_ID]


def test_agent_rows_reads_corporate_names_with_their_entity():
    names, _ = conv.agent_rows(CORPORATE_NODES[1], {CORPORATE}, "corporate", conv.CORPORATE_NAMES)

    assert names == [
        (CORPORATE, "corporate", "preferred", "Berliner Philharmoniker"),
        (CORPORATE, "corporate", "variant", "Philharmonisches Orchester Berlin"),
    ]


def test_unresolved_counts_splits_persons_corporate_bodies_and_unresolved():
    roles = {GOETHE: {"firstAuthor"}, CORPORATE: {"composer"}, OLD_ID: {"firstComposer", "composer"}}
    found = {"person": {GOETHE}, "corporate": {CORPORATE}}

    rows = conv.unresolved_counts(roles, found, named={GOETHE}, deprecated={OLD_ID})
    counts = {row["role"]: row for row in rows}

    assert counts["all"] == {
        "role": "all", "creator_ids": 3, "with_person_record": 1, "with_corporate_record": 1, "with_names": 1,
        "unresolved": 1, "unresolved_as_deprecated_uri": 1,
    }  # fmt: skip
    assert counts["composer"]["with_corporate_record"] == 1
    assert counts["composer"]["unresolved"] == 1
    assert counts["poet"]["creator_ids"] == 0


# --- Through main ---


def test_main_writes_tables_manifest_and_csv(dumps):
    exit_code = conv.main(argv(dumps))

    assert exit_code == 0
    out = dumps["out"]
    assert rows(out / "gnd_work_classes.parquet") == [(SONG, "MusicalWork"), (FAUST, "Work")]
    assert len(rows(out / "gnd_work_names.parquet")) == 4
    assert rows(out / "gnd_creator_names.parquet") == [
        (ECKERMANN, "person", "preferred", "Eckermann, Johann Peter"),
        (GOETHE, "person", "preferred", "Goethe, Johann Wolfgang von"),
        (GOETHE, "person", "variant", "Goethe, J. W. von"),
        (CORPORATE, "corporate", "preferred", "Berliner Philharmoniker"),
        (CORPORATE, "corporate", "variant", "Philharmonisches Orchester Berlin"),
    ]
    data = manifest(dumps)
    assert data["dumps"]["werk"]["date"] == "2026-10-13"
    assert data["dumps"]["werk"]["url"] == conv.DEFAULT_WERK_URL
    assert len(data["dumps"]["person"]["sha256"]) == 64
    assert data["dumps"]["koerperschaft"]["url"] == conv.DEFAULT_KOERPERSCHAFT_URL
    assert data["tables"]["gnd_work_creators.parquet"]["rows"] == 4
    assert data["passes"]["works"]["works"] == 2


def test_main_counts_unresolved_creators_in_csv(dumps):
    conv.main(argv(dumps))

    with open(dumps["csv"], encoding="utf-8", newline="") as f:
        counts = {row["role"]: row for row in csv.DictReader(f)}
    assert counts["all"]["creator_ids"] == "4"
    assert counts["all"]["with_person_record"] == "2"
    assert counts["all"]["with_corporate_record"] == "1"
    assert counts["all"]["with_names"] == "3"
    assert counts["all"]["unresolved"] == "1"
    assert counts["all"]["unresolved_as_deprecated_uri"] == "1"


def test_main_second_run_skips_both_passes(dumps):
    conv.main(argv(dumps))
    before = manifest(dumps)["passes"]

    exit_code = conv.main(argv(dumps))

    assert exit_code == 0
    assert manifest(dumps)["passes"] == before


def test_main_force_gives_identical_tables(dumps):
    conv.main(argv(dumps))
    before = manifest(dumps)["tables"]

    conv.main(argv(dumps, "--force"))

    assert manifest(dumps)["tables"] == before


def test_main_interrupted_pass_2_resumes_without_redoing_pass_1(dumps, monkeypatch):
    original = conv.ijson.items

    def interrupt_person_dump(f, prefix):
        if "person" in f.name:
            raise KeyboardInterrupt
        return original(f, prefix)

    monkeypatch.setattr(conv.ijson, "items", interrupt_person_dump)
    assert conv.main(argv(dumps)) == 130
    assert not list(dumps["out"].glob("*.tmp"))
    assert not (dumps["out"] / "gnd_creator_names.parquet").exists()
    works_pass = manifest(dumps)["passes"]["works"]

    monkeypatch.setattr(conv.ijson, "items", original)
    assert conv.main(argv(dumps)) == 0

    assert manifest(dumps)["passes"]["works"] == works_pass
    assert (dumps["out"] / "gnd_creator_names.parquet").exists()


def test_main_dry_run_writes_nothing(dumps, capsys):
    exit_code = conv.main(argv(dumps, "--dry-run"))

    assert exit_code == 0
    assert not dumps["out"].exists()
    out = capsys.readouterr().out
    assert FAUST in out
    assert "Berliner Philharmoniker" in out


# --- Failure modes ---


def test_main_missing_dump_exits_1(dumps, capsys):
    dumps["werk"].unlink()

    with pytest.raises(SystemExit) as exc:
        conv.main(argv(dumps))

    assert exc.value.code == 1
    assert "not found" in capsys.readouterr().err


def test_main_truncated_dump_exits_1(dumps, capsys):
    content = dumps["person"].read_text(encoding="utf-8")
    dumps["person"].write_text(content[: len(content) // 2], encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        conv.main(argv(dumps))

    assert exc.value.code == 1
    assert "incomplete" in capsys.readouterr().err


def test_main_undated_dump_name_exits_1(dumps, tmp_path, capsys):
    dumps["werk"] = write_dump(tmp_path / "werk.jsonld", WORK_NODES)

    with pytest.raises(SystemExit) as exc:
        conv.main(argv(dumps))

    assert exc.value.code == 1
    assert "no date" in capsys.readouterr().err


def test_main_refuses_tables_not_in_manifest(dumps, capsys):
    dumps["out"].mkdir()
    (dumps["out"] / "gnd_work_names.parquet").write_text("other", encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        conv.main(argv(dumps))

    assert exc.value.code == 1
    assert "--force" in capsys.readouterr().err


def test_main_limit_into_default_out_dir_exits_1(dumps, capsys):
    args = argv(dumps, "--limit", "2", "--out-dir", str(conv.DEFAULT_OUT_DIR))

    with pytest.raises(SystemExit) as exc:
        conv.main(args)

    assert exc.value.code == 1
    assert "trial" in capsys.readouterr().err


def test_main_unknown_flag_exits_2(dumps):
    with pytest.raises(SystemExit) as exc:
        conv.main(argv(dumps, "--no-such-flag"))

    assert exc.value.code == 2
