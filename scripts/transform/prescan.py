"""
Purpose:    Pass 1 of the two-pass GeMeA transform.
            Scans one sector SQLite file to:
              1. Emit unique PROV-O shared-node descriptive triples → prov-shared.nq
              2. Write PROV-O node URIs              → prov.duckdb (prov_entities table)
              3. Collect authority URIs from dcType / dc_subject → concept_labels.duckdb
              4. Collect agent URIs from creator/contributor/publisher → agent_labels.duckdb
              5. Write a per-sector Parquet metadata file
            All shared-DB writes are protected by fcntl.flock so sectors can run in
            parallel without concurrent writers.
Usage:      python -m transform.prescan
                --db <sector>.sqlite
                --prov-db prov.duckdb
                --concept-labels-db concept_labels.duckdb
                --agent-labels-db agent_labels.duckdb
                --lido lido_event_types.csv
                --prov-out prov-shared.nq
                --parquet-out <stem>_meta.parquet
Inputs:     <sector>.sqlite          — table objs, column bufgz (gzip-compressed cortex JSON)
            prov.duckdb              — shared; created if absent
            concept_labels.duckdb    — shared; created if absent
            agent_labels.duckdb      — shared; created if absent
            lido_event_types.csv     — resource → label mapping
Outputs:    prov-shared.nq           — N-Quads for PROV-O shared nodes (appended, deduped)
            <stem>_meta.parquet      — per-sector ProvidedCHO metadata
            prov.duckdb              — updated prov_entities table
            concept_labels.duckdb    — updated concept_labels table
            agent_labels.duckdb      — updated agent_labels table
Deps:       stdlib + duckdb + pyarrow
Assumes:    SQLite has objs(uid VARCHAR, bufgz BLOB); JSON follows DDB EDM cortex format.
"""

from __future__ import annotations

import argparse
import csv
import fcntl
import gzip
import json
import logging
import re
import sqlite3
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from .constants import GRAPH_PROV, DDB_ITEM_BASE
from .emitters import emit_prov_triples
from .utils import coerce_list, normalize_date

# ---------------------------------------------------------------------------
# Parquet schema
# ---------------------------------------------------------------------------

AGENT_STRUCT = pa.struct([
    pa.field("name",       pa.string()),
    pa.field("type",       pa.string()),
    pa.field("is_ext_uri", pa.bool_()),
])

DATE_STRUCT = pa.struct([
    pa.field("value", pa.string()),
    pa.field("begin", pa.string()),
    pa.field("end",   pa.string()),
    pa.field("type",  pa.string()),
])

CONCEPT_STRUCT = pa.struct([
    pa.field("name",       pa.string()),
    pa.field("is_ext_uri", pa.bool_()),
])

PARQUET_SCHEMA = pa.schema([
    ("obj_id",         pa.string()),
    ("title",          pa.string()),
    ("lang_title",     pa.string()),
    ("lang_obj",       pa.string()),
    ("description",    pa.list_(pa.string())),
    ("provider_id",    pa.string()),
    ("dataset_id",     pa.string()),
    ("dc_type",        pa.list_(CONCEPT_STRUCT)),
    ("agents",         pa.list_(AGENT_STRUCT)),
    ("dates",          pa.list_(DATE_STRUCT)),
    ("dc_subject",     pa.list_(CONCEPT_STRUCT)),
    ("hierarchy_type", pa.int16()),
    ("mediatype",      pa.int16()),
    ("sector",         pa.int16()),
    ("is_part_of",     pa.bool_()),
])

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DDB_VOCNET = "http://ddb.vocnet.org/"
_BARE_ID_RE = re.compile(r'^[A-Z0-9]{32}$')
_QUALIFIER_RE = re.compile(r'\([^)]*\)')

LIDO_CREATED: frozenset[str] = frozenset({
    "http://terminology.lido-schema.org/lido00012",
    "http://terminology.lido-schema.org/eventType/creation",
    "http://terminology.lido-schema.org/lido00007",
})
LIDO_ISSUED: frozenset[str] = frozenset({
    "http://terminology.lido-schema.org/lido00228",
    "http://terminology.lido-schema.org/eventType/publication",
})

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_ext_uri(resource: str) -> bool:
    """True if resource is an HTTP URI that is not a bare 32-char DDB ID."""
    return bool(resource) and resource.startswith("http") and not _BARE_ID_RE.match(resource)


def _scalar_str(val) -> str | None:
    """Return the first non-empty string value from a cortex field."""
    if val is None:
        return None
    if isinstance(val, str):
        return val or None
    if isinstance(val, dict):
        return val.get("$") or None
    if isinstance(val, list):
        for item in val:
            result = _scalar_str(item)
            if result:
                return result
    return None


def _scalar_values(val) -> list[str]:
    """Extract all string values from a cortex field (scalar / dict / list)."""
    if val is None:
        return []
    if isinstance(val, str):
        return [val] if val else []
    if isinstance(val, dict):
        v = val.get("$") or ""
        return [v] if v else []
    if isinstance(val, list):
        out: list[str] = []
        for item in val:
            out.extend(_scalar_values(item))
        return out
    return []


def _parse_dc_date(raw: str) -> str | None:
    """Strip parenthesized qualifiers and brackets, return first normalized date value."""
    if not raw:
        return None
    clean = _QUALIFIER_RE.sub("", raw).strip().strip("[]").strip()
    if not clean:
        return None
    vals = normalize_date(clean)
    return vals[0] if vals else None


def _build_concept_labels(rdf: dict) -> dict[str, str]:
    """Build URI → prefLabel map from all Concept blocks in the record."""
    result: dict[str, str] = {}
    for c in coerce_list(rdf.get("Concept")):
        if not isinstance(c, dict):
            continue
        about = (c.get("about") or "").strip()
        if not about:
            continue
        for item in coerce_list(c.get("prefLabel")):
            if isinstance(item, dict):
                label = item.get("$") or ""
            elif isinstance(item, str):
                label = item
            else:
                continue
            if label:
                result[about] = label
                break
    return result


def _build_agent_name_map(rdf: dict) -> dict[str, str]:
    """Build URI → name map from Agent and Organization entities in the record."""
    result: dict[str, str] = {}
    for entity_type in ("Agent", "Organization"):
        for ent in coerce_list(rdf.get(entity_type)):
            if not isinstance(ent, dict):
                continue
            about = (ent.get("about") or "").strip()
            if not about:
                continue
            for key in ("prefLabel", "name", "altLabel"):
                for item in coerce_list(ent.get(key)):
                    if isinstance(item, dict):
                        label = item.get("$") or ""
                    elif isinstance(item, str):
                        label = item
                    else:
                        continue
                    if label:
                        result[about] = label
                        break
                if about in result:
                    break
    return result


def _agent_field(
    field_val,
    agent_type: str,
    agent_labels_pending: dict[str, str | None],
) -> list[dict]:
    """Convert creator/contributor/publisher field to {name, type, is_ext_uri} structs."""
    out: list[dict] = []
    for v in coerce_list(field_val):
        if isinstance(v, dict):
            name = v.get("$") or ""
            res  = v.get("resource") or ""
            ext  = _is_ext_uri(res)
            if ext:
                agent_labels_pending.setdefault(res, name or None)
            if name or res:
                out.append({"name": name, "type": agent_type, "is_ext_uri": ext})
        elif isinstance(v, str) and v:
            out.append({"name": v, "type": agent_type, "is_ext_uri": False})
    return out


def _lido_agents(
    cho: dict,
    rdf: dict,
    lido_labels: dict[str, str],
    agent_name_map: dict[str, str],
) -> list[dict]:
    """Extract LIDO participants from hasMet → Event.P11_had_participant."""
    out: list[dict] = []
    event_map = {
        e["about"]: e for e in coerce_list(rdf.get("Event"))
        if isinstance(e, dict) and e.get("about")
    }
    for hm in coerce_list(cho.get("hasMet")):
        if not isinstance(hm, dict):
            continue
        ev_ref = hm.get("resource")
        if not ev_ref:
            continue
        ev = event_map.get(ev_ref, {})
        ht = ev.get("hasType") or {}
        ht_uri = ht.get("resource") if isinstance(ht, dict) else None
        event_label = lido_labels.get(ht_uri or "", "unknown_event")
        for p in coerce_list(ev.get("P11_had_participant")):
            if not isinstance(p, dict):
                continue
            res  = p.get("resource") or ""
            name = p.get("$") or agent_name_map.get(res, "")
            ext  = _is_ext_uri(res)
            out.append({"name": name, "type": event_label, "is_ext_uri": ext})
    return out


def _timespan_to_date(ts: dict, date_type: str) -> dict | None:
    """Convert a TimeSpan dict to a {value, begin, end, type} struct."""
    ts_begin = (_scalar_values(ts.get("begin")) or [None])[0]
    ts_end   = (_scalar_values(ts.get("end"))   or [None])[0]
    if ts_begin is None and ts_end is None:
        pref = (_scalar_values(ts.get("prefLabel")) or [None])[0]
        return {"value": pref, "begin": None, "end": None, "type": date_type} if pref else None
    if ts_begin and ts_end and ts_begin == ts_end:
        return {"value": ts_begin, "begin": None, "end": None, "type": date_type}
    return {"value": None, "begin": ts_begin, "end": ts_end, "type": date_type}


def _lido_dates(
    events: list,
    ts_map: dict[str, dict],
    lido_uris: frozenset[str],
    date_type: str,
) -> list[dict]:
    """Extract date structs from LIDO events matching lido_uris via TimeSpan lookup."""
    out: list[dict] = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        ht = ev.get("hasType") or {}
        ht_uri = ht.get("resource") if isinstance(ht, dict) else None
        if ht_uri not in lido_uris:
            continue
        oc = ev.get("occuredAt") or ev.get("occurredAt")
        if not oc:
            continue
        ts_ref = oc.get("resource") if isinstance(oc, dict) else oc
        ts = ts_map.get(ts_ref, {}) if isinstance(ts_ref, str) else {}
        d = _timespan_to_date(ts, date_type)
        if d:
            out.append(d)
    return out


def _resolve_subject_items(
    field_val,
    concept_labels: dict[str, str],
    concept_labels_pending: dict[str, str | None],
) -> list[dict]:
    """Convert subject field values to {name, is_ext_uri} structs."""
    out: list[dict] = []
    for v in coerce_list(field_val):
        if isinstance(v, dict):
            lit = v.get("$") or ""
            res = v.get("resource") or ""
            is_ext = bool(res and _is_ext_uri(res) and not res.startswith(_DDB_VOCNET))
            if lit:
                out.append({"name": lit, "is_ext_uri": is_ext})
            elif is_ext:
                label = concept_labels.get(res)
                if label:
                    out.append({"name": label, "is_ext_uri": True})
                else:
                    concept_labels_pending.setdefault(res, None)
        elif isinstance(v, str) and v and not _BARE_ID_RE.match(v):
            out.append({"name": v, "is_ext_uri": False})
    return out


# ---------------------------------------------------------------------------
# Record extraction
# ---------------------------------------------------------------------------

def extract_record(
    data: dict,
    lido_labels: dict[str, str],
) -> tuple[dict, dict[str, str | None], dict[str, str | None]]:
    """Extract a Parquet row + pending label DB inserts from one record.

    Returns:
        (parquet_row, concept_labels_pending, agent_labels_pending)
    """
    props     = data.get("properties") or {}
    prov_info = data.get("provider-info") or {}
    rdf       = (data.get("edm") or {}).get("RDF") or {}
    cho       = rdf.get("ProvidedCHO") or {}
    if isinstance(cho, list):
        cho = cho[0] if cho else {}

    events    = coerce_list(rdf.get("Event"))
    timespans = coerce_list(rdf.get("TimeSpan"))
    ts_map    = {
        t["about"]: t for t in timespans
        if isinstance(t, dict) and t.get("about")
    }

    concept_labels        = _build_concept_labels(rdf)
    agent_name_map        = _build_agent_name_map(rdf)
    concept_labels_pending: dict[str, str | None] = {}
    agent_labels_pending:   dict[str, str | None] = {}

    # -- title + lang_title --
    title_raw  = cho.get("title")
    title_str  = ""
    lang_title = ""
    if isinstance(title_raw, list):
        title_raw = title_raw[0] if title_raw else None
    if isinstance(title_raw, dict):
        title_str  = title_raw.get("$") or ""
        lang_title = title_raw.get("lang") or ""
    elif isinstance(title_raw, str):
        title_str = title_raw

    # -- lang_obj --
    lang_obj = _scalar_str(cho.get("language") or cho.get("dcTermsLanguage")) or ""

    # -- description --
    description = _scalar_values(cho.get("description"))

    # -- dc_type structs --
    dc_type: list[dict] = []
    for v in coerce_list(cho.get("dcType")):
        if isinstance(v, dict):
            lit = v.get("$") or ""
            res = v.get("resource") or ""
            is_ext = bool(res and not res.startswith(_DDB_VOCNET) and _is_ext_uri(res))
            if lit:
                dc_type.append({"name": lit, "is_ext_uri": is_ext})
            elif is_ext:
                label = concept_labels.get(res)
                if label:
                    dc_type.append({"name": label, "is_ext_uri": True})
                else:
                    concept_labels_pending.setdefault(res, None)
        elif isinstance(v, str) and v:
            dc_type.append({"name": v, "is_ext_uri": False})

    # -- dc_subject structs (three field variants) --
    dc_subj: list[dict] = []
    for key in ("dcSubject", "dcTermsSubject", "dcTermSubject"):
        dc_subj.extend(
            _resolve_subject_items(cho.get(key), concept_labels, concept_labels_pending)
        )

    # -- agents unified --
    agents: list[dict] = (
        _agent_field(cho.get("creator"),     "creation",    agent_labels_pending)
        + _agent_field(cho.get("publisher"), "publication", agent_labels_pending)
        + _agent_field(cho.get("contributor"), "contribution", agent_labels_pending)
        + _lido_agents(cho, rdf, lido_labels, agent_name_map)
    )

    # -- dates unified --
    dates: list[dict] = []
    dc_date_raw = _scalar_str(cho.get("date"))
    if dc_date_raw:
        normed = _parse_dc_date(dc_date_raw)
        if normed:
            dates.append({"value": normed, "begin": None, "end": None, "type": "unknown_event"})
    for val in _scalar_values(cho.get("created")):
        dates.append({"value": val, "begin": None, "end": None, "type": "creation"})
    for val in _scalar_values(cho.get("issued")):
        dates.append({"value": val, "begin": None, "end": None, "type": "publication"})
    dates.extend(_lido_dates(events, ts_map, LIDO_CREATED, "creation"))
    dates.extend(_lido_dates(events, ts_map, LIDO_ISSUED,  "publication"))

    # -- mediatype / sector (int16) --
    mediatype: int | None = None
    sector:    int | None = None
    for c in coerce_list(rdf.get("Concept")):
        if not isinstance(c, dict):
            continue
        about = c.get("about") or ""
        if mediatype is None and "/medientyp/mt" in about:
            try:
                mediatype = int(about.rsplit("/mt", 1)[-1])
            except ValueError:
                pass
        if sector is None and "/sparte/sparte" in about:
            try:
                sector = int(about.rsplit("/sparte", 1)[-1])
            except ValueError:
                pass

    # -- hierarchy_type (int16) --
    raw_ht = (_scalar_values(cho.get("hierarchyType")) or [None])[0]
    htype: int | None = None
    if raw_ht:
        try:
            htype = int(raw_ht.replace("htype_", ""))
        except ValueError:
            pass

    row = {
        "obj_id":         props.get("item-id", ""),
        "title":          title_str,
        "lang_title":     lang_title,
        "lang_obj":       lang_obj,
        "description":    description,
        "provider_id":    prov_info.get("provider-ddb-id", ""),
        "dataset_id":     props.get("dataset-id", ""),
        "dc_type":        dc_type,
        "agents":         agents,
        "dates":          dates,
        "dc_subject":     dc_subj,
        "hierarchy_type": htype,
        "mediatype":      mediatype,
        "sector":         sector,
        "is_part_of":     bool(cho.get("isPartOf")),
    }
    return row, concept_labels_pending, agent_labels_pending


# ---------------------------------------------------------------------------
# Locked DB writes
# ---------------------------------------------------------------------------

def _write_prov_db(
    prov_db_path: Path,
    new_entities: dict[str, str],
    prov_meta: dict[str, dict],
) -> None:
    """Lock-protected write of PROV-O shared node URIs + metadata to prov.duckdb.

    Schema: (uri, entity_type, label, url, identifier, isil, rec_type, provider_uri)
      label        — foaf:name (provider) or rdfs:label (dataset)
      url          — schema:url (provider)
      identifier   — dcterms:identifier (provider)
      isil         — mocho:isil (provider)
      rec_type     — dcterms:type URI (dataset)
      provider_uri — prov:wasAttributedTo target (dataset → provider)
    """
    import duckdb
    with open(str(prov_db_path) + ".lock", "a") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            conn = duckdb.connect(str(prov_db_path))
            conn.execute("""
                CREATE TABLE IF NOT EXISTS prov_entities (
                    uri          VARCHAR PRIMARY KEY,
                    entity_type  VARCHAR NOT NULL,
                    label        VARCHAR DEFAULT '',
                    url          VARCHAR DEFAULT '',
                    identifier   VARCHAR DEFAULT '',
                    isil         VARCHAR DEFAULT '',
                    rec_type     VARCHAR DEFAULT '',
                    provider_uri VARCHAR DEFAULT ''
                )
            """)
            # Migrate tables from older schemas
            cols = {r[0] for r in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='prov_entities'"
            ).fetchall()}
            for col in ("label", "url", "identifier", "isil", "rec_type", "provider_uri"):
                if col not in cols:
                    conn.execute(f"ALTER TABLE prov_entities ADD COLUMN {col} VARCHAR DEFAULT ''")
            # nq_lines column is obsolete — drop silently if present (DuckDB doesn't support DROP COLUMN easily, just ignore)

            uris = list(new_entities.keys())
            if uris:
                ph = ",".join("?" * len(uris))
                existing = {r[0] for r in conn.execute(
                    f"SELECT uri FROM prov_entities WHERE uri IN ({ph})", uris
                ).fetchall()}
            else:
                existing = set()
            truly_new = {u: t for u, t in new_entities.items() if u not in existing}
            if truly_new:
                rows = []
                for u, t in truly_new.items():
                    m = prov_meta.get(u) or {}
                    rows.append((
                        u, t,
                        m.get("label",        "") or "",
                        m.get("url",          "") or "",
                        m.get("identifier",   "") or "",
                        m.get("isil",         "") or "",
                        m.get("rec_type",     "") or "",
                        m.get("provider_uri", "") or "",
                    ))
                conn.executemany(
                    "INSERT OR IGNORE INTO prov_entities(uri,entity_type,label,url,identifier,isil,rec_type,provider_uri) VALUES (?,?,?,?,?,?,?,?)",
                    rows,
                )
            conn.close()
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def regenerate_prov_nq(prov_db_path: Path, prov_out_path: Path) -> int:
    """Generate prov-shared.nq from prov.duckdb metadata columns.

    Overwrites prov_out_path. Returns number of lines written.
    Run once after all sectors' prescan completes.
    """
    from .constants import (
        GRAPH_PROV, RDF_TYPE, DDB_BASE,
        PROV_AGENT, PROV_SW_AGENT, PROV_ENTITY, PROV_ON_BEHALF, PROV_ATTRIBUTED,
        FOAF_ORG, FOAF_NAME, DCAT_DATASET,
        DCTERMS_ID, DCTERMS_TYPE, DCTERMS_HAS_VER, RDFS_LABEL,
        SCHEMA_URL, MOCHO_ISIL,
    )
    from .utils import make_nq

    def _nq(s: str, p: str, o: str) -> str:
        return make_nq(f"<{s}>", f"<{p}>", o, GRAPH_PROV)

    def _lit(value: str, lang: str = "") -> str:
        escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r")
        return f'"{escaped}"@{lang}' if lang else f'"{escaped}"'

    import duckdb
    conn = duckdb.connect(str(prov_db_path), read_only=True)
    cols = {r[0] for r in conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name='prov_entities'"
    ).fetchall()}
    has_meta = "label" in cols
    if has_meta:
        raw = conn.execute(
            "SELECT uri, entity_type, label, url, identifier, isil, rec_type, provider_uri "
            "FROM prov_entities"
        ).fetchall()
    else:
        raw = [(r[0], r[1], "", "", "", "", "", "")
               for r in conn.execute("SELECT uri, entity_type FROM prov_entities").fetchall()]
    conn.close()

    lines: list[str] = []
    for uri, etype, label, url, ident, isil, rec_type, provider_uri in raw:
        if etype == "prov_xslt":
            version = uri.rsplit(":", 1)[-1]
            lines += [
                _nq(uri, RDF_TYPE,        f"<{PROV_SW_AGENT}>"),
                _nq(uri, DCTERMS_HAS_VER, _lit(version)),
                _nq(uri, PROV_ON_BEHALF,  f"<{DDB_BASE}>"),
            ]
        elif etype == "prov_ddb":
            lines += [
                _nq(uri, RDF_TYPE,  f"<{PROV_AGENT}>"),
                _nq(uri, RDF_TYPE,  f"<{FOAF_ORG}>"),
                _nq(uri, FOAF_NAME, _lit("Deutsche Digitale Bibliothek")),
            ]
        elif etype == "prov_provider":
            lines += [
                _nq(uri, RDF_TYPE, f"<{PROV_AGENT}>"),
                _nq(uri, RDF_TYPE, f"<{FOAF_ORG}>"),
            ]
            if label:
                lines.append(_nq(uri, FOAF_NAME, _lit(label)))
            if url:
                lines.append(_nq(uri, SCHEMA_URL, f"<{url}>"))
            if ident:
                lines.append(_nq(uri, DCTERMS_ID, _lit(ident)))
            if isil:
                lines.append(_nq(uri, MOCHO_ISIL, _lit(isil)))
        elif etype == "prov_dataset":
            dataset_id = uri.rsplit(":", 1)[-1]
            lines += [
                _nq(uri, RDF_TYPE,   f"<{DCAT_DATASET}>"),
                _nq(uri, RDF_TYPE,   f"<{PROV_ENTITY}>"),
                _nq(uri, DCTERMS_ID, _lit(dataset_id)),
            ]
            if label:
                lines.append(_nq(uri, RDFS_LABEL, _lit(label, "de")))
            if rec_type:
                lines.append(_nq(uri, DCTERMS_TYPE, f"<{rec_type}>"))
            if provider_uri:
                lines.append(_nq(uri, PROV_ATTRIBUTED, f"<{provider_uri}>"))

    prov_out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(prov_out_path, "w", encoding="utf-8") as f:
        for line in lines:
            f.write(line + "\n")
    return len(lines)


def _write_labels_db(
    db_path: Path,
    table: str,
    pending: dict[str, str | None],
) -> None:
    """Lock-protected INSERT OR IGNORE of (uri, label) pairs into a labels DuckDB."""
    if not pending:
        return
    import duckdb
    with open(str(db_path) + ".lock", "a") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            conn = duckdb.connect(str(db_path))
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {table} (
                    uri   VARCHAR PRIMARY KEY,
                    label VARCHAR
                )
            """)
            conn.executemany(
                f"INSERT OR IGNORE INTO {table} VALUES (?, ?)",
                list(pending.items()),
            )
            conn.close()
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Pass 1 prescan: PROV-O shared nodes + label DBs + Parquet"
    )
    sub = parser.add_subparsers(dest="cmd")

    # subcommand: regen — generate prov-shared.nq from prov.duckdb metadata columns
    regen = sub.add_parser("regen", help="Generate prov-shared.nq from prov.duckdb metadata")
    regen.add_argument("--prov-db",  type=Path, required=True, dest="prov_db")
    regen.add_argument("--prov-out", type=Path, required=True, dest="prov_out")

    parser.add_argument("--db",                type=Path,
                        help="Sector SQLite file (objs table, bufgz column)")
    parser.add_argument("--prov-db",           type=Path, dest="prov_db",
                        help="Shared prov.duckdb (prov_entities table); created if absent")
    parser.add_argument("--concept-labels-db", type=Path, dest="concept_labels_db",
                        help="Shared concept_labels.duckdb; created if absent")
    parser.add_argument("--agent-labels-db",   type=Path, dest="agent_labels_db",
                        help="Shared agent_labels.duckdb; created if absent")
    parser.add_argument("--lido",              type=Path,
                        help="lido_event_types.csv (resource → label)")
    parser.add_argument("--parquet-out",       type=Path, dest="parquet_out",
                        help="Per-sector Parquet output")
    args = parser.parse_args()

    if args.cmd == "regen":
        logging.basicConfig(level=logging.INFO,
                            format="%(asctime)s %(levelname)-8s %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")
        log = logging.getLogger(__name__)
        n = regenerate_prov_nq(args.prov_db, args.prov_out)
        log.info("Regenerated %s: %d lines", args.prov_out, n)
        return

    # Validate required args for the default scan mode
    missing = [f for f, v in [
        ("--db", args.db), ("--prov-db", args.prov_db),
        ("--concept-labels-db", args.concept_labels_db),
        ("--agent-labels-db", args.agent_labels_db),
        ("--lido", args.lido), ("--parquet-out", args.parquet_out),
    ] if v is None]
    if missing:
        parser.error(f"required arguments missing: {', '.join(missing)}")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    log = logging.getLogger(__name__)

    # Ensure all output parent directories exist and parquet path is writable
    for p in (args.prov_db, args.concept_labels_db, args.agent_labels_db, args.parquet_out):
        p.parent.mkdir(parents=True, exist_ok=True)
    args.parquet_out.touch()

    # Load LIDO event type labels
    lido_labels: dict[str, str] = {}
    with open(args.lido, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            lido_labels[row["resource"]] = row["label"]
    log.info("Loaded %d LIDO labels from %s", len(lido_labels), args.lido.name)

    PARQUET_FLUSH = 500_000  # rows per flush

    # Scan accumulators
    local_emitted: dict[str, str]             = {}
    prov_meta: dict[str, dict]               = {}
    concept_labels_all: dict[str, str | None] = {}
    agent_labels_all:   dict[str, str | None] = {}
    parquet_buf: list[dict]                  = []
    parquet_writer: pq.ParquetWriter | None  = None
    parquet_total = 0

    def _flush_parquet() -> None:
        nonlocal parquet_writer, parquet_total
        if not parquet_buf:
            return
        arrays = {col: [r.get(col) for r in parquet_buf] for col in PARQUET_SCHEMA.names}
        table  = pa.table(arrays, schema=PARQUET_SCHEMA)
        if parquet_writer is None:
            parquet_writer = pq.ParquetWriter(str(args.parquet_out), PARQUET_SCHEMA)
        parquet_writer.write_table(table)
        parquet_total += len(parquet_buf)
        parquet_buf.clear()

    db_conn = sqlite3.connect(str(args.db))
    cur = db_conn.cursor()
    cur.execute("SELECT COUNT(*) FROM objs")
    total = cur.fetchone()[0]
    log.info("Scanning %s (%d records)", args.db.name, total)

    cur.execute("SELECT uid, bufgz FROM objs WHERE bufgz IS NOT NULL ORDER BY rowid")
    processed = errors = 0

    for uid, blob in cur:
        try:
            data = json.loads(gzip.decompress(blob))
        except Exception as exc:
            log.warning("uid=%s decompress/parse error: %s", uid, exc)
            errors += 1
            continue

        props  = data.get("properties") or {}
        obj_id = props.get("item-id") or uid
        cho_uri = DDB_ITEM_BASE + obj_id
        cho_subj_nt = f"<{cho_uri}> "

        # PROV-O: collect shared-node URIs + metadata
        prev_keys = set(local_emitted.keys())
        emit_prov_triples(data, cho_uri, GRAPH_PROV, local_emitted)
        new_keys = set(local_emitted.keys()) - prev_keys
        if new_keys:
            prov_info = data.get("provider-info") or {}
            props_    = data.get("properties") or {}
            src_ref_  = ((data.get("source") or {}).get("description") or {}).get("record") or {}
            if not isinstance(src_ref_, dict):
                src_ref_ = {}
            for uri in new_keys:
                etype = local_emitted[uri]
                if etype == "prov_provider":
                    prov_meta[uri] = {
                        "label":      prov_info.get("provider-name") or "",
                        "url":        prov_info.get("provider-uri")  or "",
                        "identifier": prov_info.get("provider-id")   or "",
                        "isil":       prov_info.get("provider-isil") or "",
                    }
                elif etype == "prov_dataset":
                    ddb_id = prov_info.get("provider-ddb-id") or ""
                    prov_meta[uri] = {
                        "label":        props_.get("dataset-label") or "",
                        "rec_type":     src_ref_.get("type")        or "",
                        "provider_uri": f"urn:ddbedm:provider:{ddb_id}" if ddb_id else "",
                    }

        # Parquet row + pending label dicts
        try:
            row, cpending, apending = extract_record(data, lido_labels)
            parquet_buf.append(row)
            for k, v in cpending.items():
                concept_labels_all.setdefault(k, v)
            for k, v in apending.items():
                agent_labels_all.setdefault(k, v)
        except Exception as exc:
            log.warning("uid=%s extract error: %s", uid, exc)
            errors += 1

        processed += 1
        if processed % 100_000 == 0:
            log.info("  %d / %d", processed, total)
        if len(parquet_buf) >= PARQUET_FLUSH:
            _flush_parquet()
            log.info("  Parquet flush: %d rows written so far", parquet_total)

    db_conn.close()
    _flush_parquet()
    if parquet_writer:
        parquet_writer.close()
    log.info("Scan complete: %d processed, %d errors, %d Parquet rows",
             processed, errors, parquet_total)

    # After scan: write shared node URIs + metadata to prov.duckdb
    new_entities = dict(local_emitted)
    log.info("Writing prov.duckdb (%d shared nodes)", len(new_entities))
    _write_prov_db(args.prov_db, new_entities, prov_meta)

    log.info("Writing concept_labels.duckdb (%d URIs)", len(concept_labels_all))
    _write_labels_db(args.concept_labels_db, "concept_labels", concept_labels_all)

    log.info("Writing agent_labels.duckdb (%d URIs)", len(agent_labels_all))
    _write_labels_db(args.agent_labels_db, "agent_labels", agent_labels_all)

    log.info("Done.")


if __name__ == "__main__":
    main()
