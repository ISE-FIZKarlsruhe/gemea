"""Config loaders: CSV and JSON dispatch/alignment tables."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from .utils import _expand_prefix


def load_ids(path: Path) -> set[str]:
    """Load ids-all-goethe-faust.txt. Returns set of 32-char object IDs."""
    with open(path, encoding="utf-8") as fh:
        return {line.strip() for line in fh if line.strip()}


def load_htype_map(path: Path) -> dict[str, tuple[list[str], list[str]]]:
    """Load lookup_htype_doco_rico.csv.

    Returns dict[htype_code] → ([rdf_type_iri, ...], [rst_iri, ...]).
    rdf_type may be comma-separated (e.g. 'doco:Section, rdac:C10007').
    Rows where all rdf_types are 'pending' or empty are excluded.
    """
    result: dict = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            type_iris: list[str] = []
            for part in (row.get("rdf_type", "") or "").split(","):
                part = part.strip()
                if part and part != "pending":
                    type_iris.append(_expand_prefix(part))
            if not type_iris:
                continue
            rst_iris: list[str] = []
            for part in (row.get("has_record_set_type", "") or "").split(","):
                part = part.strip()
                if part:
                    rst_iris.append(_expand_prefix(part))
            result[row["htype_code"].strip()] = (type_iris, rst_iris)
    return result


def load_mediatype_class(path: Path) -> dict[tuple[str, str], dict]:
    """Load lookup_mediatype_class.csv.

    Returns dict[(sparte_iri, mediatype_iri)] → row dict with keys:
      use_htype (bool), rdf_type_w (str), rdf_type_m (str).
    CURIEs in rdf_type_w/rdf_type_m are expanded to full IRIs.
    """
    result: dict = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            key = (row["sparte"].strip(), row["mediatype"].strip())
            result[key] = {
                "use_htype":  row["use_htype"].strip().lower() == "true",
                "rdf_type_w": _expand_prefix(row["rdf_type_w"].strip()),
                "rdf_type_m": _expand_prefix(row["rdf_type_m"].strip()),
            }
    return result


def load_class_prop_alignment(path: Path) -> dict[tuple[str, str], str]:
    """Load lookup_class_prop_alignment.csv.

    Returns dict[(target_class_curie, edm_prop_curie)] → target_prop_iri.
    Rows where target_prop is empty or 'N/A' are skipped.
    """
    result: dict = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            target_prop = row.get("target_prop", "").strip()
            if not target_prop or target_prop in ("N/A", "TBD", "skip"):
                continue
            key = (
                _expand_prefix(row["target_class"].strip()),
                _expand_prefix(row["edm_prop"].strip()),
            )
            result[key] = _expand_prefix(target_prop)
    return result


def load_lido_event_types(path: Path) -> dict[str, dict[str, str]]:
    """Load lido_event_types.csv.

    Returns dict[lido_uri] → {col_name: expanded_iri}.
    Columns: rdam_agent_prop, rdaw_agent_prop, vra_image_agent_prop,
    vra_work_agent_prop, rico_agent_prop, dc_agent_fallback.
    """
    cols = [
        "rdam_agent_prop", "rdaw_agent_prop",
        "vra_image_agent_prop", "vra_work_agent_prop",
        "rico_agent_prop", "dc_agent_fallback",
    ]
    result: dict = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            uri = row.get("resource", "").strip()
            if not uri:
                continue
            result[uri] = {
                col: _expand_prefix(row.get(col, "").strip())
                for col in cols
            }
    return result


def load_audio_type2class(path: Path) -> dict[tuple[str, str], str]:
    """Load audio_type2class.json.

    Returns dict[(sector_iri, dc_type_de)] → group char ('A', 'B', or 'C').
    """
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    result: dict = {}
    for entry in raw if isinstance(raw, list) else raw.get("entries", []):
        sector   = entry.get("sector", "").strip()
        dc_type  = entry.get("dc_type_de", "").strip()
        group    = entry.get("group", "").strip()
        if sector and dc_type and group:
            result[(sector, dc_type)] = group
    return result
