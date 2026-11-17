"""A small made-up run folder for the writer's tests, and the values it holds."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any

RUN_ID = "20261110T090205Z"
STEP1_ID = "20261109T144427Z"
COMMIT = "1" * 40
INPUT_SHA256 = "a" * 64
RULE_SHA256 = "b" * 64
HF_REVISION = "4ffb3596f4359f0c8799ea15bbf5dbb3b0915a53"  # the revision the published scores are tied to
TABLES = {"gnd_work_names.parquet": "c" * 64, "gnd_creator_names.parquet": "d" * 64}
TABLES_SHA256 = hashlib.sha256(
    f"gnd_creator_names.parquet {'d' * 64}\ngnd_work_names.parquet {'c' * 64}\n".encode()
).hexdigest()

OBJ_A, OBJ_B, OBJ_C, OBJ_D, OBJ_E = ("A" * 32, "B" * 32, "C" * 32, "D" * 32, "E" * 32)
# Work 1 is reached from both paths; rule-span has 3 rows and 3 Works, model-span 1 row and 1 Work.
LINKS = [
    {"obj_id": OBJ_A, "status": "linked", "gnd_uri": "https://d-nb.info/gnd/1", "matched_span_source": "isbd_title"},
    {"obj_id": OBJ_B, "status": "linked", "gnd_uri": "https://d-nb.info/gnd/1", "matched_span_source": "flair"},
    {"obj_id": OBJ_C, "status": "linked", "gnd_uri": "https://d-nb.info/gnd/2-X", "matched_span_source": "volume_rule"},
    {"obj_id": OBJ_D, "status": "no_candidate", "gnd_uri": "", "matched_span_source": ""},
    {"obj_id": OBJ_E, "status": "linked", "gnd_uri": "https://d-nb.info/gnd/3", "matched_span_source": "isbd_other_title"},
]
RULE_SPAN_TRIPLES = 3 * 2 + 3 * 3
MODEL_SPAN_TRIPLES = 1 * 2 + 1 * 3
DISTINCT_TRIPLES = 4 * 2 + 3 * 3


def item(value: Any) -> dict[str, Any]:
    return {"value": value, "source": "test", "status": "found"}


def record() -> dict[str, Any]:
    return {
        "a_linker_run_id": item(RUN_ID),
        "b_linker_times": item(
            {
                "parts": {
                    "part00": {"started_at": "2026-11-10T09:02:05.752000+00:00", "ended_at": "2026-11-10T09:56:59.245104+00:00"},
                    "part01": {"started_at": "2026-11-10T09:02:05.714000+00:00", "ended_at": "2026-11-10T09:58:53.240492+00:00"},
                },
                "merge": {"started_at": None, "ended_at": "2026-11-10T10:15:06.029090+00:00"},
            }
        ),
        "c_step1_times": item(
            {
                "shard0": {"started_at": "2026-11-09T14:44:27.985000+00:00", "ended_at": "2026-11-09T21:34:07.003888+00:00"},
                "shard1": {"started_at": "2026-11-09T14:44:33.944000+00:00", "ended_at": "2026-11-09T21:29:23.445444+00:00"},
            }
        ),
        "d_step1_id_candidates": item({"utc_start_of_earlier_shard": STEP1_ID, "run_id_json": None}),
        "e_input_sha256": item({"path": "/home/user/gemea/data/out/s2/s2_meta_de_content.parquet", "sha256": INPUT_SHA256}),
        "f_model_revision": item(
            [
                {"path": "/cache/models--flair", "revision": HF_REVISION, "download_date": "2026-11-06"},
                {"path": "/cache/.locks/models--flair", "revision": None, "download_date": None},
            ]
        ),
        "g_flair_torch_versions": item({"flair": "0.15.1", "torch": "2.7.1+cu118"}),
        "h_rule_script": item({"rule_sha256": RULE_SHA256}),
        "commits": item({"step1": COMMIT, "step2": COMMIT, "merge": COMMIT}),
    }


def merge(links_sha256: str, linked: int) -> dict[str, Any]:
    return {
        "gnd": {
            "dumps": {"werk": {"date": "2026-10-13"}, "person": {"date": "2026-10-13"}},
            "tables": TABLES,
        },
        "settings": {"limit": None, "chunk_size": 1000, "decision": "baseline", "min_score": 0.6, "name_threshold": 0.85, "search_size": 500},
        "output": {"sha256": links_sha256, "statuses": {"linked": linked, "no_candidate": 1}},
    }


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def make_run(folder: Path, links: list[dict[str, str]] | None = None) -> Path:
    """Write the run folder: links CSV, merge record and PROV-CRED record."""
    rows = LINKS if links is None else links
    run = folder / "rerun"
    run.mkdir()
    csv_path = run / "werk_links.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(LINKS[0]))
        writer.writeheader()
        writer.writerows(rows)
    linked = sum(1 for row in rows if row["status"] == "linked")
    write_json(run / "werk_links_merge.json", merge(hashlib.sha256(csv_path.read_bytes()).hexdigest(), linked))
    write_json(run / "provcred_record.json", record())
    return run


def change_json(path: Path, change: Any) -> None:
    """Load a JSON file, let `change` edit it in place, and write it back."""
    data = json.loads(path.read_text(encoding="utf-8"))
    change(data)
    write_json(path, data)
