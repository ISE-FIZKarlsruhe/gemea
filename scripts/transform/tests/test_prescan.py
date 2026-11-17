"""
Purpose: Unit tests for transform.prescan — extract_record, _write_prov_db, _write_labels_db.
Usage:   pytest scripts/transform/tests/test_prescan.py -q
Deps:    pytest, pyarrow; duckdb required for TestPrescanProv / TestPrescanConceptLabels /
         TestPrescanAgentLabels (auto-skipped if absent).
Assumes: Run from the gemea project root.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from transform.constants import GRAPH_PROV, DDB_ITEM_BASE
from transform.emitters import emit_prov_triples
from transform.prescan import (
    _write_labels_db,
    _write_prov_db,
    extract_record,
    regenerate_prov_nq,
)


# ── shared helpers ─────────────────────────────────────────────────────────────

def _rec(item_id, *, xslt=None, provider_ddb_id=None, dataset_id=None, cho=None):
    props: dict = {"item-id": item_id}
    if xslt:
        props["mapping-version"] = xslt
    if dataset_id:
        props["dataset-id"] = dataset_id
    prov: dict = {}
    if provider_ddb_id:
        prov["provider-ddb-id"] = provider_ddb_id
    return {
        "properties":    props,
        "provider-info": prov,
        "edm": {"RDF": {"ProvidedCHO": cho or {}}},
    }


def _scan_prov(records):
    """Simulate prescan's PROV-O emission: collect shared-node URIs + metadata."""
    local_emitted: dict[str, str] = {}
    prov_meta: dict[str, dict]    = {}
    for rec in records:
        obj_id   = rec["properties"]["item-id"]
        cho_uri  = DDB_ITEM_BASE + obj_id
        prev     = set(local_emitted.keys())
        emit_prov_triples(rec, cho_uri, GRAPH_PROV, local_emitted)
        for uri in set(local_emitted.keys()) - prev:
            etype     = local_emitted[uri]
            prov_info = rec.get("provider-info") or {}
            props_    = rec.get("properties") or {}
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
                    "rec_type":     "",
                    "provider_uri": f"urn:ddbedm:provider:{ddb_id}" if ddb_id else "",
                }
    return local_emitted, prov_meta


# ── TestPrescanProv ────────────────────────────────────────────────────────────

class TestPrescanProv:
    """3 records sharing one XSLT version and two providers."""

    _RECORDS = [
        _rec("OBJ001", xslt="v1.0", provider_ddb_id="prov001"),
        _rec("OBJ002", xslt="v1.0", provider_ddb_id="prov001"),
        _rec("OBJ003", xslt="v1.0", provider_ddb_id="prov002"),
    ]

    def test_prov_entities_count_and_types(self, tmp_path):
        duckdb = pytest.importorskip("duckdb")
        emitted, prov_meta = _scan_prov(self._RECORDS)
        prov_db = tmp_path / "prov.duckdb"
        _write_prov_db(prov_db, dict(emitted), prov_meta)

        conn = duckdb.connect(str(prov_db))
        rows = conn.execute("SELECT entity_type FROM prov_entities").fetchall()
        conn.close()

        c = Counter(r[0] for r in rows)
        assert len(rows) == 4
        assert c["prov_xslt"]     == 1
        assert c["prov_provider"] == 2
        assert c["prov_ddb"]      == 1

    def test_prov_nq_line_count(self, tmp_path):
        # xslt: 3 lines, ddb_agent: 3 lines, prov001: 2 lines, prov002: 2 lines = 10
        # (provider type-only: no names in test fixtures)
        pytest.importorskip("duckdb")
        emitted, prov_meta = _scan_prov(self._RECORDS)
        prov_db = tmp_path / "prov.duckdb"
        prov_nq = tmp_path / "prov-shared.nq"
        _write_prov_db(prov_db, dict(emitted), prov_meta)
        regenerate_prov_nq(prov_db, prov_nq)

        nq_lines = [l for l in prov_nq.read_text().splitlines() if l.strip()]
        assert len(nq_lines) == 10

    def test_prov_nq_idempotent(self, tmp_path):
        """Second write + regen must produce the same line count as the first."""
        pytest.importorskip("duckdb")
        records = [_rec("OBJ001", xslt="v1.0", provider_ddb_id="prov001")]
        prov_db = tmp_path / "prov.duckdb"
        prov_nq = tmp_path / "prov-shared.nq"

        for _ in range(2):
            emitted, prov_meta = _scan_prov(records)
            _write_prov_db(prov_db, dict(emitted), prov_meta)

        regenerate_prov_nq(prov_db, prov_nq)
        nq_lines = [l for l in prov_nq.read_text().splitlines() if l.strip()]
        # xslt(3) + ddb_agent(3) + prov001(2) = 8
        assert len(nq_lines) == 8


# ── TestPrescanConceptLabels ────────────────────────────────────────────────────

class TestPrescanConceptLabels:
    """dcType with one locally-resolved URI and one unknown URI."""

    KNOWN_URI   = "http://www.wikidata.org/entity/Q735"
    UNKNOWN_URI = "http://www.wikidata.org/entity/Q999999"

    def _data(self):
        return {
            "properties":    {"item-id": "T001"},
            "provider-info": {},
            "edm": {"RDF": {
                "ProvidedCHO": {
                    "dcType": [
                        {"resource": self.KNOWN_URI},
                        {"resource": self.UNKNOWN_URI},
                    ],
                },
                "Concept": [
                    {"about": self.KNOWN_URI,
                     "prefLabel": [{"$": "Gemälde", "lang": "de"}]},
                ],
            }},
        }

    def test_dc_type_contains_only_resolved_label(self):
        row, _, _ = extract_record(self._data(), {})
        assert row["dc_type"] == [{"name": "Gemälde", "is_ext_uri": True}]

    def test_concept_labels_db_has_unresolved_uri_with_null(self, tmp_path):
        duckdb = pytest.importorskip("duckdb")
        _, cpending, _ = extract_record(self._data(), {})
        db = tmp_path / "concept_labels.duckdb"
        _write_labels_db(db, "concept_labels", cpending)

        conn = duckdb.connect(str(db))
        rows = conn.execute("SELECT uri, label FROM concept_labels").fetchall()
        conn.close()

        assert len(rows) == 1
        assert rows[0][0] == self.UNKNOWN_URI
        assert rows[0][1] is None


# ── TestPrescanAgentLabels ─────────────────────────────────────────────────────

class TestPrescanAgentLabels:
    """dc:creator with GND URI + inline label."""

    GND_URI = "http://d-nb.info/gnd/123456789"
    LABEL   = "Karl Meier"

    def _data(self):
        return {
            "properties":    {"item-id": "T002"},
            "provider-info": {},
            "edm": {"RDF": {
                "ProvidedCHO": {
                    "creator": [{"$": self.LABEL, "resource": self.GND_URI}],
                },
            }},
        }

    def test_agents_list_struct(self):
        row, _, _ = extract_record(self._data(), {})
        assert len(row["agents"]) == 1
        a = row["agents"][0]
        assert a["name"]       == self.LABEL
        assert a["type"]       == "creation"
        assert a["is_ext_uri"] is True

    def test_agent_labels_db_has_uri_and_label(self, tmp_path):
        duckdb = pytest.importorskip("duckdb")
        _, _, apending = extract_record(self._data(), {})
        db = tmp_path / "agent_labels.duckdb"
        _write_labels_db(db, "agent_labels", apending)

        conn = duckdb.connect(str(db))
        rows = conn.execute("SELECT uri, label FROM agent_labels").fetchall()
        conn.close()

        assert len(rows) == 1
        assert rows[0][0] == self.GND_URI
        assert rows[0][1] == self.LABEL


# ── TestAgentsStruct ──────────────────────────────────────────────────────────

class TestAgentsStruct:
    """Creator (GND URI + label), contributor (bare string), LIDO photography participant."""

    _PHOTO_URI   = "http://terminology.lido-schema.org/lido01127"
    _PART_URI    = "http://d-nb.info/gnd/456789123"
    _LIDO_LABELS = {_PHOTO_URI: "photography"}

    def _data(self):
        return {
            "properties":    {"item-id": "T003"},
            "provider-info": {},
            "edm": {"RDF": {
                "ProvidedCHO": {
                    "creator":     [{"$": "Anna Schmidt",
                                     "resource": "http://d-nb.info/gnd/987654321"}],
                    "contributor": [{"$": "Anon Beitrag"}],
                    "hasMet":      [{"resource": "http://urn.test/evt001"}],
                },
                "Event": [{
                    "about":              "http://urn.test/evt001",
                    "hasType":            {"resource": self._PHOTO_URI},
                    "P11_had_participant": [{"resource": self._PART_URI}],
                }],
            }},
        }

    def test_three_agents(self):
        row, _, _ = extract_record(self._data(), self._LIDO_LABELS)
        assert len(row["agents"]) == 3

    def test_agent_types_in_order(self):
        row, _, _ = extract_record(self._data(), self._LIDO_LABELS)
        types = [a["type"] for a in row["agents"]]
        assert types[0] == "creation"
        assert types[1] == "contribution"
        assert types[2] == "photography"

    def test_is_ext_uri_flags(self):
        row, _, _ = extract_record(self._data(), self._LIDO_LABELS)
        agents = row["agents"]
        assert agents[0]["is_ext_uri"] is True   # GND URI
        assert agents[1]["is_ext_uri"] is False  # bare string — no resource key
        assert agents[2]["is_ext_uri"] is True   # GND URI participant

    def test_contributor_with_uri_and_literal_is_ext_uri_true(self):
        """Contributor dict with both $ and GND resource → is_ext_uri=True, name=literal."""
        data = {
            "properties":    {"item-id": "T003b"},
            "provider-info": {},
            "edm": {"RDF": {"ProvidedCHO": {
                "contributor": [{"$": "Max Mustermann",
                                 "resource": "http://d-nb.info/gnd/111111111"}],
            }}},
        }
        row, _, _ = extract_record(data, {})
        assert len(row["agents"]) == 1
        a = row["agents"][0]
        assert a["name"]       == "Max Mustermann"
        assert a["type"]       == "contribution"
        assert a["is_ext_uri"] is True

    def test_publisher_with_uri_and_literal_is_ext_uri_true(self):
        """Publisher dict with both $ and GND resource → is_ext_uri=True."""
        data = {
            "properties":    {"item-id": "T003c"},
            "provider-info": {},
            "edm": {"RDF": {"ProvidedCHO": {
                "publisher": [{"$": "Teubner Verlag",
                               "resource": "http://d-nb.info/gnd/222222222"}],
            }}},
        }
        row, _, _ = extract_record(data, {})
        assert len(row["agents"]) == 1
        a = row["agents"][0]
        assert a["name"]       == "Teubner Verlag"
        assert a["type"]       == "publication"
        assert a["is_ext_uri"] is True


# ── TestDatesStruct ────────────────────────────────────────────────────────────

class TestDatesStruct:
    """dc:date bare string, dc:issued, and two LIDO creation TimeSpans (point + range)."""

    _CREATION_URI = "http://terminology.lido-schema.org/lido00012"

    def _data(self):
        return {
            "properties":    {"item-id": "T004"},
            "provider-info": {},
            "edm": {"RDF": {
                "ProvidedCHO": {
                    "date":   {"$": "1850"},
                    "issued": {"$": "1920"},
                    "hasMet": [
                        {"resource": "http://urn.test/ev_point"},
                        {"resource": "http://urn.test/ev_range"},
                    ],
                },
                "Event": [
                    {
                        "about":     "http://urn.test/ev_point",
                        "hasType":   {"resource": self._CREATION_URI},
                        "occuredAt": {"resource": "http://urn.test/ts_point"},
                    },
                    {
                        "about":     "http://urn.test/ev_range",
                        "hasType":   {"resource": self._CREATION_URI},
                        "occuredAt": {"resource": "http://urn.test/ts_range"},
                    },
                ],
                "TimeSpan": [
                    {"about": "http://urn.test/ts_point",
                     "begin": {"$": "1900"}, "end": {"$": "1900"}},
                    {"about": "http://urn.test/ts_range",
                     "begin": {"$": "1900"}, "end": {"$": "1950"}},
                ],
            }},
        }

    def test_date_count(self):
        # dc:date(1) + dc:issued(1) + lido_point(1) + lido_range(1) = 4
        row, _, _ = extract_record(self._data(), {})
        assert len(row["dates"]) == 4

    def test_date_types_present(self):
        row, _, _ = extract_record(self._data(), {})
        types = {d["type"] for d in row["dates"]}
        assert "unknown_event" in types
        assert "publication"   in types
        assert "creation"      in types

    def test_point_in_time_struct(self):
        row, _, _ = extract_record(self._data(), {})
        creation_dates = [d for d in row["dates"] if d["type"] == "creation"]
        point = [d for d in creation_dates if d["value"] is not None]
        assert len(point) == 1
        assert point[0]["value"] == "1900"
        assert point[0]["begin"] is None
        assert point[0]["end"]   is None

    def test_range_struct(self):
        row, _, _ = extract_record(self._data(), {})
        creation_dates = [d for d in row["dates"] if d["type"] == "creation"]
        rng = [d for d in creation_dates if d["value"] is None]
        assert len(rng) == 1
        assert rng[0]["begin"] == "1900"
        assert rng[0]["end"]   == "1950"


# ── TestLangSplit ──────────────────────────────────────────────────────────────

class TestLangSplit:
    """French-tagged title + dc:language=ger → lang_title ≠ lang_obj."""

    def test_lang_title_and_lang_obj(self):
        data = {
            "properties":    {"item-id": "T005"},
            "provider-info": {},
            "edm": {"RDF": {
                "ProvidedCHO": {
                    "title":    {"$": "La Fleur", "lang": "fre"},
                    "language": {"$": "ger"},
                },
            }},
        }
        row, _, _ = extract_record(data, {})
        assert row["lang_title"] == "fre"
        assert row["lang_obj"]   == "ger"


# ── TestSectorMediatypeInt ─────────────────────────────────────────────────────

class TestSectorMediatypeInt:
    """Concept block with sparte006 + mt002 → integer fields."""

    def test_sector_and_mediatype_are_ints(self):
        data = {
            "properties":    {"item-id": "T006"},
            "provider-info": {},
            "edm": {"RDF": {
                "ProvidedCHO": {},
                "Concept": [
                    {"about": "http://ddb.vocnet.org/sparte/sparte006"},
                    {"about": "http://ddb.vocnet.org/medientyp/mt002"},
                ],
            }},
        }
        row, _, _ = extract_record(data, {})
        assert row["sector"]    == 6
        assert row["mediatype"] == 2
        assert isinstance(row["sector"],    int)
        assert isinstance(row["mediatype"], int)


# ── TestDcTypeList ─────────────────────────────────────────────────────────────

class TestDcTypeList:
    """dc_type is always a list, never a bare string."""

    def test_two_literals_produce_list_of_length_two(self):
        data = {
            "properties":    {"item-id": "T007"},
            "provider-info": {},
            "edm": {"RDF": {
                "ProvidedCHO": {
                    "dcType": [{"$": "Buch"}, {"$": "Monografie"}],
                },
            }},
        }
        row, _, _ = extract_record(data, {})
        assert isinstance(row["dc_type"], list)
        assert row["dc_type"] == [
            {"name": "Buch",      "is_ext_uri": False},
            {"name": "Monografie","is_ext_uri": False},
        ]


# ── TestDcTypeExtUri ──────────────────────────────────────────────────────────

class TestDcTypeExtUri:
    """dcType dict with both $ (literal) and resource (external URI) → is_ext_uri=True.

    rdf2jsonld embeds the Concept prefLabel as $ alongside the Concept about URI as
    resource. The original code used `if lit: … elif res:`, so the resource was silently
    discarded and is_ext_uri was always False for literal entries. The fix evaluates
    is_ext from resource independently.
    """

    GND_URI    = "http://d-nb.info/gnd/4023007-2"
    DDB_URI    = "http://ddb.vocnet.org/spartetyp/st001"
    WIKIDATA   = "https://www.wikidata.org/entity/Q11292"

    def _data(self, dc_types):
        return {
            "properties":    {"item-id": "T008"},
            "provider-info": {},
            "edm": {"RDF": {"ProvidedCHO": {"dcType": dc_types}}},
        }

    def test_literal_with_external_uri_is_ext_uri_true(self):
        """$ + non-DDB resource → is_ext_uri=True, name=literal."""
        row, _, _ = extract_record(
            self._data([{"$": "Gemälde", "resource": self.GND_URI}]), {}
        )
        assert row["dc_type"] == [{"name": "Gemälde", "is_ext_uri": True}]

    def test_literal_without_resource_is_ext_uri_false(self):
        """$ with no resource → is_ext_uri=False (plain free-text label)."""
        row, _, _ = extract_record(
            self._data([{"$": "Buch"}]), {}
        )
        assert row["dc_type"] == [{"name": "Buch", "is_ext_uri": False}]

    def test_literal_with_ddb_vocnet_uri_is_ext_uri_false(self):
        """$ + DDB vocnet resource → is_ext_uri=False (vocnet URIs are internal)."""
        row, _, _ = extract_record(
            self._data([{"$": "Text", "resource": self.DDB_URI}]), {}
        )
        assert row["dc_type"] == [{"name": "Text", "is_ext_uri": False}]

    def test_mixed_entries_correct_flags(self):
        """Two entries: one with external URI (→ True), one plain literal (→ False)."""
        row, _, _ = extract_record(
            self._data([
                {"$": "Gemälde",  "resource": self.GND_URI},
                {"$": "Monografie"},
            ]),
            {},
        )
        assert row["dc_type"] == [
            {"name": "Gemälde",   "is_ext_uri": True},
            {"name": "Monografie","is_ext_uri": False},
        ]

    def test_literal_with_wikidata_uri_is_ext_uri_true(self):
        """$ + Wikidata resource → is_ext_uri=True."""
        row, _, _ = extract_record(
            self._data([{"$": "Skulptur", "resource": self.WIKIDATA}]), {}
        )
        assert row["dc_type"] == [{"name": "Skulptur", "is_ext_uri": True}]

    def test_dc_subject_literal_with_external_uri_is_ext_uri_true(self):
        """Same fix applies to dc_subject: $ + non-DDB resource → is_ext_uri=True."""
        data = {
            "properties":    {"item-id": "T009"},
            "provider-info": {},
            "edm": {"RDF": {"ProvidedCHO": {
                "dcSubject": [{"$": "Landschaft", "resource": self.GND_URI}],
            }}},
        }
        row, _, _ = extract_record(data, {})
        assert row["dc_subject"] == [{"name": "Landschaft", "is_ext_uri": True}]

    def test_dc_subject_literal_without_resource_is_ext_uri_false(self):
        """dc_subject: $ with no resource → is_ext_uri=False."""
        data = {
            "properties":    {"item-id": "T010"},
            "provider-info": {},
            "edm": {"RDF": {"ProvidedCHO": {
                "dcSubject": [{"$": "Landschaft"}],
            }}},
        }
        row, _, _ = extract_record(data, {})
        assert row["dc_subject"] == [{"name": "Landschaft", "is_ext_uri": False}]
