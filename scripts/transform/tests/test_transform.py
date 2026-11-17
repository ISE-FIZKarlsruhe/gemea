"""
Purpose:    Unit tests for the transform package.
Usage:      pytest scripts/transform/tests/ -q
Deps:       pytest
Assumes:    Run from the gemea project root.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# ── Package import ────────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # adds scripts/ to sys.path

from transform.constants import CONFIG_DIR, GRAPH_MOCHO, GRAPH_PROV, MOCHO_NS
from transform.utils import (
    coerce_list,
    make_nq,
    mint_bare_id,
    mint_cho_uri,
    normalize_date,
    value_to_nt_obj,
    _escape_literal,
    get_object_id,
    build_bare_id_index,
    expand_obj_nt,
    resource_uris,
)
from transform.emitters import (
    retype_entities,
    emit_creator_triples,
    emit_contributor_triples,
    emit_subject_triples,
    emit_hastype_triples,
    emit_current_location_triples,
    emit_aggregation_triples,
    emit_place_stubs,
    werk_staging_row,
    emit_ddbedm_triples,
    emit_mocho_triples,
    emit_prov_triples,
)
from transform.constants import (
    _MOCHO_SKIP, DDB_HIERARCHY_TYPE, _HTYPE_PREFIX, EDM_HAS_TYPE, EDM_NS, DDB_BASE,
)
from transform.transform import transform_record
from transform.loaders import load_mediatype_class, load_htype_map, load_audio_type2class

# Config table paths
_CONFIG = CONFIG_DIR


# ── Helpers ───────────────────────────────────────────────────────────────────

def _rdf_types(lines: list[str]) -> set[str]:
    """Extract the object IRIs from all rdf:type triples in a list of N-Quads lines."""
    rdf_type = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
    result: set[str] = set()
    for line in lines:
        parts = line.split()
        if len(parts) >= 4 and f"<{rdf_type}>" in parts[1]:
            obj = parts[2].strip("<>")
            result.add(obj)
    return result


def _predicates(lines: list[str]) -> set[str]:
    result: set[str] = set()
    for line in lines:
        parts = line.split()
        if len(parts) >= 4:
            result.add(parts[1].strip("<>"))
    return result


def _load_configs():
    mc_map = load_mediatype_class(_CONFIG / "lookup_mediatype_class.csv")
    ht_map = load_htype_map(_CONFIG / "lookup_htype_doco_rico.csv")
    return mc_map, ht_map


# ── normalize_date ────────────────────────────────────────────────────────────

class TestNormalizeDate:
    def test_compact_yyyymmdd(self):
        assert normalize_date("19870315") == ["1987-03-15"]

    def test_iso_interval(self):
        result = normalize_date("1900/1950")
        assert result == ["1900", "1950"]

    def test_passthrough(self):
        assert normalize_date("ca. 1900") == ["ca. 1900"]

    def test_iso_already(self):
        assert normalize_date("2026-05-01") == ["2026-05-01"]


# ── mint_bare_id ──────────────────────────────────────────────────────────────

class TestMintBareId:
    _id = "A" * 32

    def test_providedcho_bare_id(self):
        result = mint_bare_id("ProvidedCHO", self._id)
        assert result == f"http://www.deutsche-digitale-bibliothek.de/item/{self._id}"

    def test_other_entity_bare_id(self):
        result = mint_bare_id("Agent", self._id)
        assert result == f"urn:ddbedm:{self._id}"

    def test_full_uri_passthrough(self):
        uri = "http://d-nb.info/gnd/1234567"
        assert mint_bare_id("Agent", uri) == uri

    def test_urn_passthrough(self):
        urn = "urn:ddbedm:12345"
        assert mint_bare_id("Agent", urn) == urn


# ── value_to_nt_obj ───────────────────────────────────────────────────────────

class TestValueToNtObj:
    def test_string(self):
        assert value_to_nt_obj("hello") == ['"hello"']

    def test_empty_string(self):
        assert value_to_nt_obj("") == []

    def test_none(self):
        assert value_to_nt_obj(None) == []

    def test_resource_dict(self):
        assert value_to_nt_obj({"resource": "http://example.org/"}) == ["<http://example.org/>"]

    def test_lang_dict(self):
        result = value_to_nt_obj({"$": "Faust", "lang": "de"})
        assert result == ['"Faust"@de']

    def test_no_lang_dict(self):
        result = value_to_nt_obj({"$": "Faust", "lang": None})
        assert result == ['"Faust"']

    def test_list_flattened(self):
        result = value_to_nt_obj([{"$": "A", "lang": "de"}, {"$": "B", "lang": "en"}])
        assert '"A"@de' in result and '"B"@en' in result

    def test_escape_quotes(self):
        result = value_to_nt_obj('say "hi"')
        assert result == ['"say \\"hi\\""']

    def test_escape_newlines(self):
        result = value_to_nt_obj("line1\nline2\r\nline3")
        assert result == ['"line1\\nline2\\r\\nline3"']


# ── get_object_id ─────────────────────────────────────────────────────────────

class TestGetObjectId:
    _id = "B" * 32

    def test_full_url(self):
        record = {"edm": {"RDF": {"ProvidedCHO": {"about": f"http://www.deutsche-digitale-bibliothek.de/item/{self._id}"}}}}
        assert get_object_id(record) == self._id

    def test_bare_id(self):
        record = {"edm": {"RDF": {"ProvidedCHO": {"about": self._id}}}}
        assert get_object_id(record) == self._id

    def test_missing_about(self):
        record = {"edm": {"RDF": {"ProvidedCHO": {}}}}
        assert get_object_id(record) is None

    def test_missing_cho(self):
        record = {"edm": {"RDF": {}}}
        assert get_object_id(record) is None


# ── retype_entities ───────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def configs():
    return _load_configs()


_SPARTE001 = "http://ddb.vocnet.org/sparte/sparte001"
_SPARTE002 = "http://ddb.vocnet.org/sparte/sparte002"
_SPARTE003 = "http://ddb.vocnet.org/sparte/sparte003"
_MT001     = "http://ddb.vocnet.org/medientyp/mt001"
_MT002     = "http://ddb.vocnet.org/medientyp/mt002"
_MT003     = "http://ddb.vocnet.org/medientyp/mt003"
_MT007     = "http://ddb.vocnet.org/medientyp/mt007"


class TestRetypeEntities:
    _cho_nt = "<https://gemea.ise.fiz-karlsruhe.de/mocho/" + "C" * 32 + ">"

    def _call(self, sector, mediatype, htype, configs, dctype_vals=None):
        mc_map, ht_map = configs
        return retype_entities(
            sector, mediatype, htype, dctype_vals or [],
            self._cho_nt, mc_map, ht_map, {}, GRAPH_MOCHO,
        )

    def test_sparte004_mt003_no_htype_fallback(self, configs):
        """sparte004/mt003 use_htype=True; no htype → fixed M class rdac:C10007."""
        _SPARTE004 = "http://ddb.vocnet.org/sparte/sparte004"
        lines, target_class, wemi, _flags = self._call(_SPARTE004, _MT003, None, configs)
        types = _rdf_types(lines)
        assert "http://rdaregistry.info/Elements/c/C10007" in types
        assert wemi == "M"

    def test_sparte001_mt003_htype021(self, configs):
        """sparte001/mt003 use_htype=True; htype_021 → rdac:C10007 from htype (no rdac:C10001, D30), mocho:Manifestation added."""
        lines, target_class, wemi, _flags = self._call(_SPARTE001, _MT003, "htype_021", configs)
        types = _rdf_types(lines)
        assert "http://rdaregistry.info/Elements/c/C10001" not in types
        assert "http://rdaregistry.info/Elements/c/C10007" in types
        assert "https://ise-fizkarlsruhe.github.io/ddbkg/mocho#Manifestation" in types
        assert wemi == "M"
        assert any(DDB_HIERARCHY_TYPE in l and f"{_HTYPE_PREFIX}ht021" in l for l in lines)

    # ── D30: library CHOs are Manifestations only; Work via Werk linking ──────

    _C10001 = "http://rdaregistry.info/Elements/c/C10001"
    _C10007 = "http://rdaregistry.info/Elements/c/C10007"

    @pytest.mark.parametrize("htype", ["htype_007", "htype_020", "htype_021"])
    def test_library_text_band_monograph_multivolume_are_manifestation_only(self, configs, htype):
        lines, _, wemi, flags = self._call(_SPARTE002, _MT003, htype, configs)

        types = _rdf_types(lines)
        assert self._C10007 in types
        assert self._C10001 not in types
        assert wemi == "M"
        assert flags["htype_used"] and not flags["fallback"]

    def test_library_text_band_keeps_doco_part(self, configs):
        lines, *_ = self._call(_SPARTE002, _MT003, "htype_007", configs)

        assert "http://purl.org/spar/doco/Part" in _rdf_types(lines)

    def test_library_text_without_htype_is_rdac_manifestation_not_d9(self, configs):
        lines, _, _, flags = self._call(_SPARTE002, _MT003, None, configs)

        assert _rdf_types(lines) == {self._C10007}
        assert not flags["fallback"]

    def test_no_rdf_type_triple_emitted_twice(self, configs):
        lines, *_ = self._call(_SPARTE002, _MT003, "htype_021", configs)

        type_lines = [l for l in lines if "rdf-syntax-ns#type>" in l]
        assert len(type_lines) == len(set(type_lines))

    def test_music_htype_keeps_domain_work_class(self, configs):
        lines, *_ = self._call(_SPARTE002, _MT003, "htype_022", configs)

        types = _rdf_types(lines)
        assert "http://purl.org/ontology/mo/MusicalWork" in types
        assert self._C10001 not in types

    @pytest.mark.parametrize("sector, htype", [(_SPARTE002, "htype_007"), (_SPARTE002, "htype_018"), (_SPARTE001, "htype_026")])
    def test_wemi_class_not_doco_drives_alignment(self, configs, sector, htype):
        """doco:Part / Chapter / TextChunk stay as types; rdac:C10007 is the primary class (D30)."""
        _, target_class, wemi, _ = self._call(sector, _MT003, htype, configs)

        assert target_class == self._C10007
        assert wemi == "M"

    def test_illustration_keeps_image_work(self, configs):
        lines, *_ = self._call(_SPARTE002, _MT003, "htype_015", configs)

        assert "https://ise-fizkarlsruhe.github.io/ddbkg/mocho#ImageWork" in _rdf_types(lines)

    def test_htype_emitted_as_iri(self, configs):
        """htype_code emits vocnet IRI in ht-prefix form (ht042, not htype_042)."""
        lines, _, _, _ = self._call(_SPARTE001, _MT003, "htype_042", configs)
        assert any(DDB_HIERARCHY_TYPE in l and f"{_HTYPE_PREFIX}ht042" in l for l in lines)
        assert not any(f"{_HTYPE_PREFIX}htype_042" in l for l in lines)

    def test_no_htype_no_hierarchy_type_triple(self, configs):
        """No htype_code → ddbedm:hierarchyType triple must not appear."""
        lines, _, _, _ = self._call(_SPARTE001, _MT003, None, configs)
        assert not any(DDB_HIERARCHY_TYPE in l for l in lines)

    def test_sparte003_mt001_fixed(self, configs):
        """sparte003/mt001 use_htype=False → mocho:ImmovableWork (W) + aco:AudioManifestation (M)."""
        lines, target_class, wemi, _flags = self._call(_SPARTE003, _MT001, None, configs)
        types = _rdf_types(lines)
        assert MOCHO_NS + "ImmovableWork" in types
        assert "https://w3id.org/ac-ontology/aco#AudioManifestation" in types

    def test_mt007_guard_does_not_add_types_here(self, configs):
        """retype_entities itself doesn't know about mt007; caller guards. Falls back to mocho:Manifestation."""
        lines, target_class, wemi, _flags = self._call(_SPARTE001, _MT007, None, configs)
        assert target_class != ""  # always returns something

    def test_unknown_sector_mediatype_fallback(self, configs):
        """Unknown (sector, mediatype) → ('any','any') D9 fallback mocho:Manifestation."""
        lines, target_class, wemi, _flags = self._call("any", "any", None, configs)
        types = _rdf_types(lines)
        assert MOCHO_NS + "Manifestation" in types
        assert target_class == MOCHO_NS + "Manifestation"

    def test_multi_htype_emits_two_triples(self, configs):
        """Space-separated hierarchyType emits one ddbedm:hierarchyType triple per code."""
        lines, _, _, _ = self._call(_SPARTE001, _MT003, "htype_007 htype_020", configs)
        htype_lines = [l for l in lines if DDB_HIERARCHY_TYPE in l]
        assert len(htype_lines) == 2
        assert any(f"{_HTYPE_PREFIX}ht007" in l for l in htype_lines)
        assert any(f"{_HTYPE_PREFIX}ht020" in l for l in htype_lines)

    def test_multi_htype_no_space_in_iri(self, configs):
        """No emitted IRI may contain a space (guard against raw multi-value passthrough)."""
        lines, _, _, _ = self._call(_SPARTE001, _MT003, "htype_007 htype_020", configs)
        for l in lines:
            if DDB_HIERARCHY_TYPE in l:
                obj_token = l.split()[2]  # "<http://...>" — whitespace-split is safe for IRI objects
                assert " " not in obj_token

    def test_mt001_group_a_emits_musical_work_and_manifestation(self, configs):
        """mt001 + group A dc:type (Schallplatte) → mo:MusicalWork (W) + mo:MusicalManifestation (M); target_class = MusicalWork → triggers werk_staging."""
        mc_map, ht_map = configs
        _SPARTE005 = "http://ddb.vocnet.org/sparte/sparte005"
        audio_map  = load_audio_type2class(_CONFIG / "audio_type2class.json")
        dctype_vals = [{"$": "Schallplatte"}]
        lines, target_class, wemi, _ = retype_entities(
            _SPARTE005, _MT001, None, dctype_vals,
            self._cho_nt, mc_map, ht_map, audio_map, GRAPH_MOCHO,
        )
        types = _rdf_types(lines)
        assert "http://purl.org/ontology/mo/MusicalWork" in types
        assert "http://purl.org/ontology/mo/MusicalManifestation" in types
        assert "https://w3id.org/ac-ontology/aco#AudioManifestation" not in types
        assert target_class == "http://purl.org/ontology/mo/MusicalWork"
        assert wemi == "W"

    def test_mt001_group_b_dctype_keeps_aco(self, configs):
        """mt001 + group B dc:type (Hörfunksendung) → aco:AudioManifestation unchanged (B1)."""
        mc_map, ht_map = configs
        _SPARTE005 = "http://ddb.vocnet.org/sparte/sparte005"
        audio_map  = load_audio_type2class(_CONFIG / "audio_type2class.json")
        dctype_vals = [{"$": "Hörfunksendung"}]
        lines, _, _, _ = retype_entities(
            _SPARTE005, _MT001, None, dctype_vals,
            self._cho_nt, mc_map, ht_map, audio_map, GRAPH_MOCHO,
        )
        types = _rdf_types(lines)
        assert "https://w3id.org/ac-ontology/aco#AudioManifestation" in types
        assert "http://purl.org/ontology/mo/MusicalManifestation" not in types

    def test_mt001_sector_specific_entry_matched(self, configs):
        """Sector-specific entry (Magnettonband × sparte004) resolves correctly (B1)."""
        mc_map, ht_map = configs
        _SPARTE004 = "http://ddb.vocnet.org/sparte/sparte004"
        audio_map  = load_audio_type2class(_CONFIG / "audio_type2class.json")
        dctype_vals = [{"$": "Magnettonband"}]
        lines, _, _, _ = retype_entities(
            _SPARTE004, _MT001, None, dctype_vals,
            self._cho_nt, mc_map, ht_map, audio_map, GRAPH_MOCHO,
        )
        types = _rdf_types(lines)
        assert "https://w3id.org/ac-ontology/aco#AudioManifestation" in types
        assert "http://purl.org/ontology/mo/MusicalManifestation" not in types

    def test_multi_htype_dispatch_uses_first(self, configs):
        """Dispatch resolves class from the first htype only; second code is ignored for typing."""
        # htype_007 → doco:Part + rdac:C10007; htype_020 → rdac:C10007 only
        # If dispatch used the full string no class would resolve → fallback mocho:Manifestation
        lines, _, _, flags = self._call(_SPARTE001, _MT003, "htype_007 htype_020", configs)
        assert flags["htype_used"] is True
        assert flags["fallback"] is False


class TestLoadAudioType2Class:
    """Loader correctness tests (B1 fix)."""

    def test_non_empty(self):
        """Loader returns a non-empty dict for the real config file."""
        result = load_audio_type2class(_CONFIG / "audio_type2class.json")
        assert len(result) > 0

    def test_sector_agnostic_group_a(self):
        """Sector-agnostic Group A entry keyed as ('', dc_type)."""
        result = load_audio_type2class(_CONFIG / "audio_type2class.json")
        assert result[("", "Schallplatte")] == "A"
        assert result[("", "CD")] == "A"
        assert result[("", "Langspielplatte")] == "A"

    def test_sector_agnostic_group_b(self):
        """Sector-agnostic Group B entry keyed as ('', dc_type)."""
        result = load_audio_type2class(_CONFIG / "audio_type2class.json")
        assert result[("", "Hörfunksendung")] == "B"

    def test_sector_specific_full_iri(self):
        """Sector-specific entry uses full IRI as sector key."""
        result = load_audio_type2class(_CONFIG / "audio_type2class.json")
        key = ("http://ddb.vocnet.org/sparte/sparte004", "Magnettonband")
        assert result[key] == "B"

    def test_skips_doc_and_muster(self):
        """_doc and Muster meta-entries are not loaded as dc:type entries."""
        result = load_audio_type2class(_CONFIG / "audio_type2class.json")
        assert not any(k[1] in {"_doc", "Muster"} for k in result)


# ── emit_creator_triples ──────────────────────────────────────────────────────

class TestEmitCreatorTriples:
    _cho_nt = "<https://gemea.ise.fiz-karlsruhe.de/mocho/" + "D" * 32 + ">"
    _agent_uri = "http://d-nb.info/gnd/118540238"
    _rdac_c10007 = "http://rdaregistry.info/Elements/c/C10007"
    _rdam_P30329 = "http://rdaregistry.info/Elements/m/P30329"

    def _agents_index(self):
        return {
            self._agent_uri: {
                "about":     self._agent_uri,
                "prefLabel": "Goethe, Johann Wolfgang von",
            }
        }

    def _align(self):
        """Minimal class_prop_align for rdac:C10007/dc:creator → rdam:P30329."""
        dc_creator = "http://purl.org/dc/elements/1.1/creator"
        return {(self._rdac_c10007, dc_creator): self._rdam_P30329}

    def test_track1_iri(self):
        vals = [{"resource": self._agent_uri, "$": "", "lang": ""}]
        lines = emit_creator_triples(
            self._cho_nt, vals, {}, self._rdac_c10007, self._align(), GRAPH_MOCHO,
        )
        preds = _predicates(lines)
        assert self._rdam_P30329 in preds

    def test_track2_agent_stub(self):
        vals = [{"resource": self._agent_uri, "$": "Goethe", "lang": ""}]
        lines = emit_creator_triples(
            self._cho_nt, vals, self._agents_index(), self._rdac_c10007,
            self._align(), GRAPH_MOCHO,
        )
        preds = _predicates(lines)
        assert "http://purl.org/dc/terms/creator" in preds
        assert "http://www.w3.org/1999/02/22-rdf-syntax-ns#type" in preds

    def test_label_only_no_track2(self):
        """Label-only creator: Track 2 is silent (no dcterms:creator without URI)."""
        vals = [{"resource": "", "$": "Unbekannt", "lang": "de"}]
        lines = emit_creator_triples(
            self._cho_nt, vals, {}, self._rdac_c10007, self._align(), GRAPH_MOCHO,
        )
        assert "http://purl.org/dc/terms/creator" not in _predicates(lines)
        assert self._rdam_P30329 in _predicates(lines)

    def test_no_match_no_crash(self):
        lines = emit_creator_triples(
            self._cho_nt, [], {}, self._rdac_c10007, {}, GRAPH_MOCHO,
        )
        assert lines == []


# ── emit_contributor_triples ──────────────────────────────────────────────────

class TestEmitContributorTriples:
    _cho_nt   = "<https://gemea.ise.fiz-karlsruhe.de/mocho/" + "E" * 32 + ">"
    _agent_uri = "http://d-nb.info/gnd/987654321"
    _lido_creation = "http://terminology.lido-schema.org/lido00012"
    _rdac_c10007   = "http://rdaregistry.info/Elements/c/C10007"

    def test_lido_dispatch_matched(self):
        event_idx = {self._agent_uri: self._lido_creation}
        lido_row = {
            "rdam_agent_prop": "http://rdaregistry.info/Elements/m/P30329",
            "dc_agent_fallback": "http://purl.org/dc/elements/1.1/contributor",
        }
        lido_dispatch = {self._lido_creation: lido_row}
        vals = [{"resource": self._agent_uri, "$": "Schiller", "lang": ""}]
        lines = emit_contributor_triples(
            self._cho_nt, vals, event_idx, lido_dispatch,
            self._rdac_c10007, "M", GRAPH_MOCHO,
        )
        preds = _predicates(lines)
        assert "http://rdaregistry.info/Elements/m/P30329" in preds

    def test_no_event_fallback_dc_contributor(self):
        vals = [{"resource": self._agent_uri, "$": "X", "lang": ""}]
        lines = emit_contributor_triples(
            self._cho_nt, vals, {}, {}, self._rdac_c10007, "M", GRAPH_MOCHO,
        )
        preds = _predicates(lines)
        assert "http://purl.org/dc/elements/1.1/contributor" in preds

    def test_label_only_literal_fallback(self):
        vals = [{"resource": "", "$": "Anonym", "lang": "de"}]
        lines = emit_contributor_triples(
            self._cho_nt, vals, {}, {}, self._rdac_c10007, "M", GRAPH_MOCHO,
        )
        preds = _predicates(lines)
        assert "http://purl.org/dc/elements/1.1/contributor" in preds
        assert any('"Anonym"@de' in nq for nq in lines)


# ── emit_subject_triples ──────────────────────────────────────────────────────

class TestEmitSubjectTriples:
    _cho_nt = "<https://gemea.ise.fiz-karlsruhe.de/mocho/" + "F" * 32 + ">"

    def test_iri_subject(self):
        vals = [{"resource": "http://d-nb.info/gnd/4018197-4", "$": "Faust", "lang": "de"}]
        lines = emit_subject_triples(self._cho_nt, vals, {}, GRAPH_MOCHO)
        preds = _predicates(lines)
        assert "http://purl.org/dc/terms/subject" in preds

    def test_literal_subject(self):
        vals = [{"resource": "", "$": "Goethe", "lang": "de"}]
        lines = emit_subject_triples(self._cho_nt, vals, {}, GRAPH_MOCHO)
        preds = _predicates(lines)
        assert "http://purl.org/dc/elements/1.1/subject" in preds

    def test_dedup(self):
        uri = "http://d-nb.info/gnd/4018197-4"
        vals = [
            {"resource": uri, "$": "Faust", "lang": "de"},
            {"resource": uri, "$": "Faust", "lang": "de"},
        ]
        lines = emit_subject_triples(self._cho_nt, vals, {}, GRAPH_MOCHO)
        dcterms_sub_lines = [l for l in lines if "terms/subject" in l]
        assert len(dcterms_sub_lines) == 1


# ── emit_place_stubs ──────────────────────────────────────────────────────────

class TestEmitPlaceStubs:
    def test_emits_label(self):
        places = [{"about": "http://example.org/place/1",
                   "prefLabel": [{"$": "Weimar", "lang": "de"}]}]
        lines = emit_place_stubs(places, GRAPH_MOCHO)
        assert any('"Weimar"@de' in nq for nq in lines)

    def test_no_about_skipped(self):
        places = [{"prefLabel": [{"$": "Weimar", "lang": "de"}]}]
        lines = emit_place_stubs(places, GRAPH_MOCHO)
        assert lines == []

    def test_none_input(self):
        assert emit_place_stubs(None, GRAPH_MOCHO) == []


# ── werk_staging_row ──────────────────────────────────────────────────────────

class TestWerkStagingRow:
    """Staging follows the Werk-linking type groups (D30)."""
    _cho_uri = "https://gemea.ise.fiz-karlsruhe.de/mocho/" + "G" * 32
    _rdac_c10007 = "http://rdaregistry.info/Elements/c/C10007"

    def _row(self, htype=None, dc_types=(), **extra):
        cho = dict(extra)
        if htype is not None:
            cho["hierarchyType"] = htype
        if dc_types:
            cho["dcType"] = [{"$": t} for t in dc_types]
        return werk_staging_row(self._cho_uri, cho, self._rdac_c10007)

    def test_monograph_is_staged_in_work_group(self):
        row = self._row("htype_021", title={"$": "Faust", "lang": "de"},
                        creator=[{"resource": "http://d-nb.info/gnd/118540238", "$": "Goethe"}])

        assert row is not None
        assert row["type_group"] == "work"
        assert row["gnd_classes"] == ["Work"]
        assert row["dc_title"] == "Faust"
        assert "http://d-nb.info/gnd/118540238" in row["creator_uris"]
        assert row["target_class"] == self._rdac_c10007

    def test_band_is_staged_like_monograph(self):
        assert self._row("htype_007")["type_group"] == "work"

    def test_multivolume_has_its_own_group(self):
        row = self._row("htype_020")

        assert row["type_group"] == "multivolume"
        assert row["gnd_classes"] == ["Work", "Collection"]

    def test_music_dc_type_stages_without_htype(self):
        assert self._row(None, ["Noten"])["type_group"] == "music"

    def test_not_linked_htype_is_not_staged(self):
        assert self._row("htype_006") is None

    def test_not_linked_htype_wins_over_music_dc_type(self):
        """As in the linker: the not_linked htypes are checked before dc:type."""
        assert self._row("htype_008", ["Noten"]) is None

    def test_no_htype_and_no_typed_dc_type_is_ignored(self):
        assert self._row(None, ["Text"]) is None

    def test_obj_id_extracted(self):
        assert self._row("htype_021")["ddb_obj_id"] == "G" * 32


# ── make_nq ───────────────────────────────────────────────────────────────────

def test_make_nq_format():
    line = make_nq("<http://s>", "<http://p>", '"o"', "https://graph/g")
    assert line == '<http://s> <http://p> "o" <https://graph/g> .'


# ── build_bare_id_index / expand_obj_nt ──────────────────────────────────────

_BARE_CONCEPT = "J" * 32
_BARE_PLACE   = "K" * 32
_BARE_AGENT   = "L" * 32

class TestBuildBareIdIndex:
    def _rdf(self):
        return {
            "Concept": [{"about": _BARE_CONCEPT, "prefLabel": [{"$": "Test", "lang": "de"}]}],
            "Place":   [{"about": _BARE_PLACE,   "prefLabel": [{"$": "Berlin", "lang": "de"}]}],
            "Agent":   [{"about": f"http://d-nb.info/gnd/99999", "prefLabel": "Name"}],
        }

    def test_bare_concept_indexed(self):
        idx = build_bare_id_index(self._rdf())
        assert _BARE_CONCEPT in idx
        assert idx[_BARE_CONCEPT] == f"urn:ddbedm:{_BARE_CONCEPT}"

    def test_bare_place_indexed(self):
        idx = build_bare_id_index(self._rdf())
        assert idx[_BARE_PLACE] == f"urn:ddbedm:{_BARE_PLACE}"

    def test_full_uri_not_indexed(self):
        idx = build_bare_id_index(self._rdf())
        assert "http://d-nb.info/gnd/99999" not in idx

    def test_empty_rdf(self):
        assert build_bare_id_index({}) == {}


class TestExpandObjNt:
    _idx = {_BARE_CONCEPT: f"urn:ddbedm:{_BARE_CONCEPT}"}

    def test_bare_id_resolved(self):
        result = expand_obj_nt(f"<{_BARE_CONCEPT}>", self._idx)
        assert result == f"<urn:ddbedm:{_BARE_CONCEPT}>"

    def test_full_uri_unchanged(self):
        result = expand_obj_nt("<http://example.org/foo>", self._idx)
        assert result == "<http://example.org/foo>"

    def test_urn_unchanged(self):
        result = expand_obj_nt("<urn:ddbedm:XXXX>", self._idx)
        assert result == "<urn:ddbedm:XXXX>"

    def test_literal_unchanged(self):
        result = expand_obj_nt('"hello"@de', self._idx)
        assert result == '"hello"@de'

    def test_unknown_bare_id_unchanged(self):
        result = expand_obj_nt(f"<{'Z' * 32}>", self._idx)
        assert result == f"<{'Z' * 32}>"


# ── emit_ddbedm_triples: bare-ID expansion in property objects ─────────────────

_DDB_ITEM = "http://www.deutsche-digitale-bibliothek.de/item/"
_BARE_CHO  = "M" * 32
_BARE_CONC = "N" * 32

class TestEmitDdbedmBareIds:
    """Bare IDs in property-object positions must be expanded to match entity subjects."""

    def _rdf(self):
        return {
            "ProvidedCHO": [{
                "about":          _BARE_CHO,
                "dcTermsSubject": {"resource": _BARE_CONC},
            }],
            "Concept": [{"about": _BARE_CONC}],
        }

    def test_subject_object_expanded(self):
        lines, _, _, _ = emit_ddbedm_triples(self._rdf(), "https://test/graph")
        subj_line = next(
            (l for l in lines if "terms/subject" in l), None
        )
        assert subj_line is not None, "dcterms:subject triple not emitted"
        assert f"urn:ddbedm:{_BARE_CONC}" in subj_line, (
            f"bare ID not expanded; got: {subj_line}"
        )

    def test_cho_subject_expanded(self):
        lines, _, _, _ = emit_ddbedm_triples(self._rdf(), "https://test/graph")
        cho_uri = _DDB_ITEM + _BARE_CHO
        assert any(cho_uri in l for l in lines), "CHO subject URI not expanded"

    def test_no_bare_id_iri_in_output(self):
        lines, _, _, _ = emit_ddbedm_triples(self._rdf(), "https://test/graph")
        for line in lines:
            parts = line.split()
            for part in parts:
                if part.startswith("<") and part.endswith(">"):
                    inner = part[1:-1]
                    assert inner.startswith(("http", "urn")), (
                        f"bare IRI in output: {part}\n  line: {line}"
                    )


# ── emit_subject_triples: bare-ID expansion ───────────────────────────────────

class TestEmitSubjectTriplesBareId:
    _cho_nt = "<https://gemea.ise.fiz-karlsruhe.de/mocho/" + "P" * 32 + ">"
    _bare   = "Q" * 32

    def test_bare_id_expanded_via_index(self):
        bare_id_to_uri = {self._bare: f"urn:ddbedm:{self._bare}"}
        vals = [{"resource": self._bare, "$": "", "lang": ""}]
        lines = emit_subject_triples(self._cho_nt, vals, {}, GRAPH_MOCHO, bare_id_to_uri)
        assert any(f"urn:ddbedm:{self._bare}" in l for l in lines)

    def test_bare_id_fallback_concept_mint(self):
        """No index entry → mint as Concept URN as fallback."""
        vals = [{"resource": self._bare, "$": "", "lang": ""}]
        lines = emit_subject_triples(self._cho_nt, vals, {}, GRAPH_MOCHO, {})
        assert any(f"urn:ddbedm:{self._bare}" in l for l in lines)

    def test_full_uri_unchanged(self):
        uri = "http://d-nb.info/gnd/4018197-4"
        vals = [{"resource": uri, "$": "", "lang": ""}]
        lines = emit_subject_triples(self._cho_nt, vals, {}, GRAPH_MOCHO, {})
        assert any(uri in l for l in lines)

    def test_label_stub_uses_expanded_uri(self):
        bare_id_to_uri = {self._bare: f"urn:ddbedm:{self._bare}"}
        concept = {"about": self._bare, "prefLabel": [{"$": "Faust", "lang": "de"}]}
        vals = [{"resource": self._bare, "$": "", "lang": ""}]
        lines = emit_subject_triples(
            self._cho_nt, vals, {self._bare: concept}, GRAPH_MOCHO, bare_id_to_uri
        )
        label_line = next((l for l in lines if '"Faust"@de' in l), None)
        assert label_line is not None
        assert f"urn:ddbedm:{self._bare}" in label_line


# ── _MOCHO_SKIP: hasMet excluded from mocho graph ────────────────────────────

def test_hasmet_in_mocho_skip():
    assert "hasMet" in _MOCHO_SKIP, "hasMet must be in _MOCHO_SKIP to prevent edm:hasMet on gemea CHOs"


# ── emit_hastype_triples ──────────────────────────────────────────────────────

_BARE_HT = "R" * 32

class TestEmitHastypeTriples:
    _cho_nt = "<https://gemea.ise.fiz-karlsruhe.de/mocho/" + "S" * 32 + ">"

    def test_full_uri_emitted(self):
        uri = "http://ddb.vocnet.org/medientyp/mt003"
        vals = [{"resource": uri, "$": "", "lang": ""}]
        lines = emit_hastype_triples(self._cho_nt, vals, {}, GRAPH_MOCHO)
        assert any(EDM_HAS_TYPE in l and uri in l for l in lines)

    def test_bare_id_expanded_via_index(self):
        bare_id_to_uri = {_BARE_HT: f"urn:ddbedm:{_BARE_HT}"}
        vals = [{"resource": _BARE_HT}]
        lines = emit_hastype_triples(self._cho_nt, vals, {}, GRAPH_MOCHO, bare_id_to_uri)
        assert any(f"urn:ddbedm:{_BARE_HT}" in l for l in lines)

    def test_bare_id_fallback_concept_mint(self):
        vals = [{"resource": _BARE_HT}]
        lines = emit_hastype_triples(self._cho_nt, vals, {}, GRAPH_MOCHO, {})
        assert any(f"urn:ddbedm:{_BARE_HT}" in l for l in lines)

    def test_label_stub_from_concept(self):
        uri = "http://ddb.vocnet.org/thema/th001"
        concept = {"about": uri, "prefLabel": [{"$": "Musik", "lang": "de"}]}
        vals = [{"resource": uri}]
        lines = emit_hastype_triples(self._cho_nt, vals, {uri: concept}, GRAPH_MOCHO)
        label_line = next((l for l in lines if '"Musik"@de' in l), None)
        assert label_line is not None
        assert uri in label_line

    def test_literal_only_skipped(self):
        vals = [{"resource": "", "$": "Foto", "lang": "de"}]
        lines = emit_hastype_triples(self._cho_nt, vals, {}, GRAPH_MOCHO)
        assert lines == []

    def test_dedup(self):
        uri = "http://ddb.vocnet.org/medientyp/mt003"
        vals = [{"resource": uri}, {"resource": uri}]
        lines = emit_hastype_triples(self._cho_nt, vals, {}, GRAPH_MOCHO)
        assert len([l for l in lines if EDM_HAS_TYPE in l]) == 1


def test_hastype_in_mocho_skip():
    assert "hasType" in _MOCHO_SKIP


def test_currentlocation_in_mocho_skip():
    assert "currentLocation" in _MOCHO_SKIP


# ── _escape_literal — <br> normalization ─────────────────────────────────────

class TestEscapeLiteralBr:
    def test_br_lowercase(self):
        assert _escape_literal("a<br>b") == r"a\nb"

    def test_br_uppercase(self):
        assert _escape_literal("A<BR>B") == r"A\nB"

    def test_br_self_closing(self):
        assert _escape_literal("a<br/>b") == r"a\nb"

    def test_br_xhtml(self):
        assert _escape_literal("a<br />b") == r"a\nb"

    def test_br_mixed_with_other_escapes(self):
        result = _escape_literal('say "hi"<br/>next')
        assert r'\n' in result
        assert r'\"' in result
        assert "<br" not in result


# ── resource_uris ─────────────────────────────────────────────────────────────

class TestResourceUris:
    def test_empty_returns_empty(self):
        assert resource_uris("") == []

    def test_single_full_uri(self):
        uri = "http://d-nb.info/gnd/118540238"
        assert resource_uris(uri) == [uri]

    def test_two_space_separated(self):
        raw = "http://d-nb.info/gnd/123 https://www.geonames.org/456"
        result = resource_uris(raw)
        assert len(result) == 2
        assert "http://d-nb.info/gnd/123" in result
        assert "https://www.geonames.org/456" in result

    def test_bare_id_index_lookup(self):
        bare = "A" * 32
        index = {bare: f"urn:ddbedm:{bare}"}
        result = resource_uris(bare, index, "Concept")
        assert result == [f"urn:ddbedm:{bare}"]

    def test_bare_id_fallback_mint(self):
        bare = "B" * 32
        result = resource_uris(bare, {}, "Concept")
        assert result == [f"urn:ddbedm:{bare}"]

    def test_entity_class_forwarded(self):
        bare = "C" * 32
        result = resource_uris(bare, {}, "Place")
        assert result == [f"urn:ddbedm:{bare}"]

    def test_unsafe_chars_sanitized(self):
        uri = "http://example.org/item<foo>"
        result = resource_uris(uri)
        assert len(result) == 1
        assert "%3C" in result[0] and "%3E" in result[0]


# ── emit_subject_triples — multi-URI ─────────────────────────────────────────

class TestEmitSubjectTriplesMultiUri:
    _cho = "<https://gemea.ise.fiz-karlsruhe.de/mocho/" + "S" * 32 + ">"
    _g   = GRAPH_MOCHO

    def test_two_uris_produce_two_triples(self):
        uri1 = "http://d-nb.info/gnd/111"
        uri2 = "http://d-nb.info/gnd/222"
        vals = [{"resource": f"{uri1} {uri2}", "$": "", "lang": ""}]
        lines = emit_subject_triples(self._cho, vals, {}, self._g)
        subject_lines = [l for l in lines if "dcterms/terms/subject" in l or "subject" in l]
        assert len([l for l in lines if uri1 in l and "subject" in l]) == 1
        assert len([l for l in lines if uri2 in l and "subject" in l]) == 1

    def test_no_space_in_any_iri(self):
        raw = "http://d-nb.info/gnd/111 http://d-nb.info/gnd/222"
        vals = [{"resource": raw}]
        lines = emit_subject_triples(self._cho, vals, {}, self._g)
        for line in lines:
            parts = line.split()
            for part in parts:
                if part.startswith("<") and part.endswith(">"):
                    assert " " not in part[1:-1]


# ── emit_hastype_triples — multi-URI ─────────────────────────────────────────

class TestEmitHastypeTriplesMultiUri:
    _cho = "<https://gemea.ise.fiz-karlsruhe.de/mocho/" + "T" * 32 + ">"

    def test_two_uris_produce_two_hastype_triples(self):
        uri1 = "http://ddb.vocnet.org/medientyp/mt001"
        uri2 = "http://ddb.vocnet.org/medientyp/mt003"
        vals = [{"resource": f"{uri1} {uri2}"}]
        lines = emit_hastype_triples(self._cho, vals, {}, GRAPH_MOCHO)
        hastype_lines = [l for l in lines if EDM_HAS_TYPE in l]
        assert len(hastype_lines) == 2


# ── emit_creator_triples — multi-URI + bare ID + agent_uri sanitize ───────────

_CREATOR_CHO = "<https://gemea.ise.fiz-karlsruhe.de/mocho/" + "X" * 32 + ">"
_CREATOR_G   = GRAPH_MOCHO


class TestEmitCreatorTriplesMultiUri:
    def test_two_uris_produce_two_track1_triples(self):
        uri1 = "http://d-nb.info/gnd/111"
        uri2 = "http://d-nb.info/gnd/222"
        align = {("http://www.w3.org/2002/07/owl#Thing",
                  "http://purl.org/dc/elements/1.1/creator"): "http://example.org/prop"}
        vals = [{"resource": f"{uri1} {uri2}", "$": "", "lang": ""}]
        lines = emit_creator_triples(_CREATOR_CHO, vals, {}, "http://www.w3.org/2002/07/owl#Thing",
                                     align, _CREATOR_G)
        prop_lines = [l for l in lines if "example.org/prop" in l]
        assert len(prop_lines) == 2


class TestEmitCreatorTriplesBareId:
    _bare = "D" * 32

    def test_bare_id_expanded_via_param(self):
        index = {self._bare: f"urn:ddbedm:{self._bare}"}
        align = {("http://www.w3.org/2002/07/owl#Thing",
                  "http://purl.org/dc/elements/1.1/creator"): "http://example.org/prop"}
        vals = [{"resource": self._bare}]
        lines = emit_creator_triples(_CREATOR_CHO, vals, {}, "http://www.w3.org/2002/07/owl#Thing",
                                     align, _CREATOR_G, index)
        assert any(f"urn:ddbedm:{self._bare}" in l for l in lines)

    def test_agent_uri_sanitized(self):
        unsafe_uri = "http://d-nb.info/gnd/118 540238"  # space in URI
        agent = {"about": unsafe_uri, "prefLabel": "Goethe"}
        agents_index = {unsafe_uri: agent}
        vals = [{"resource": "", "$": "Goethe", "lang": "de"}]
        lines = emit_creator_triples(_CREATOR_CHO, vals, agents_index,
                                     "http://www.w3.org/2002/07/owl#Thing", {}, _CREATOR_G)
        for line in lines:
            for part in line.split():
                if part.startswith("<") and part.endswith(">"):
                    assert " " not in part[1:-1]


# ── emit_contributor_triples — multi-URI + bare ID ────────────────────────────

_CONTRIB_CHO = "<https://gemea.ise.fiz-karlsruhe.de/mocho/" + "Y" * 32 + ">"


class TestEmitContributorTriplesMultiUri:
    def test_two_uris_produce_two_cho_triples(self):
        uri1 = "http://d-nb.info/gnd/333"
        uri2 = "http://d-nb.info/gnd/444"
        vals = [{"resource": f"{uri1} {uri2}", "$": "", "lang": ""}]
        lines = emit_contributor_triples(_CONTRIB_CHO, vals, {}, {}, "", "M", GRAPH_MOCHO)
        cho_lines = [l for l in lines if _CONTRIB_CHO in l]
        assert len(cho_lines) == 2


class TestEmitContributorTriplesBareId:
    _bare = "E" * 32

    def test_bare_id_expanded_via_param(self):
        index = {self._bare: f"urn:ddbedm:{self._bare}"}
        vals = [{"resource": self._bare}]
        lines = emit_contributor_triples(_CONTRIB_CHO, vals, {}, {}, "", "M", GRAPH_MOCHO, index)
        assert any(f"urn:ddbedm:{self._bare}" in l for l in lines)


# ── emit_creator_triples — prefLabel list (Bug 1 fix) ────────────────────────

_CREATOR_AGENT_URI = "http://d-nb.info/gnd/118540238"


class TestEmitCreatorTriplesPrefLabel:
    """Track 2 rdfs:label must come from agent.prefLabel list (not isinstance(str) check)."""

    _cho = "<https://gemea.ise.fiz-karlsruhe.de/mocho/" + "C" * 32 + ">"
    _g   = GRAPH_MOCHO

    def _agent(self, pref):
        return {"about": _CREATOR_AGENT_URI, "prefLabel": pref}

    def test_preflabel_list_dict_emitted(self):
        agent = self._agent([{"$": "Goethe, Johann Wolfgang von", "lang": "de"}])
        vals  = [{"resource": "", "$": "Goethe", "lang": "de"}]
        lines = emit_creator_triples(self._cho, vals, {"Goethe": agent}, "", {}, self._g)
        assert any("Goethe, Johann Wolfgang von" in l for l in lines)

    def test_preflabel_lang_tagged(self):
        agent = self._agent([{"$": "Schiller, Friedrich", "lang": "de"}])
        vals  = [{"resource": "", "$": "Schiller", "lang": "de"}]
        lines = emit_creator_triples(self._cho, vals, {"Schiller": agent}, "", {}, self._g)
        assert any('"Schiller, Friedrich"@de' in l for l in lines)

    def test_preflabel_empty_list_falls_back_to_label(self):
        agent = self._agent([])
        vals  = [{"resource": "", "$": "Fallback", "lang": "de"}]
        lines = emit_creator_triples(self._cho, vals, {"Fallback": agent}, "", {}, self._g)
        assert any('"Fallback"' in l for l in lines)

    def test_uri_track2_uses_agents_index_preflabel(self):
        agent = self._agent([{"$": "Goethe, Johann Wolfgang von", "lang": "de"}])
        vals  = [{"resource": _CREATOR_AGENT_URI, "$": "", "lang": ""}]
        lines = emit_creator_triples(
            self._cho, vals, {_CREATOR_AGENT_URI: agent}, "", {}, self._g,
        )
        assert any("Goethe, Johann Wolfgang von" in l for l in lines)


# ── emit_contributor_triples — agents_index label (Bug 2 fix) ────────────────

_CONTRIB_AGENT_URI = "http://d-nb.info/gnd/118607626"


class TestEmitContributorTriplesAgentLabel:
    """agents_index prefLabel used for URI case; literal-match emits agent stub."""

    _cho = "<https://gemea.ise.fiz-karlsruhe.de/mocho/" + "Z" * 32 + ">"
    _g   = GRAPH_MOCHO

    def _agent(self, pref=None):
        return {
            "about": _CONTRIB_AGENT_URI,
            "prefLabel": pref or [{"$": "Schiller, Friedrich", "lang": "de"}],
        }

    def test_uri_case_uses_agents_index_preflabel(self):
        agent = self._agent()
        vals  = [{"resource": _CONTRIB_AGENT_URI, "$": "Schiller", "lang": "de"}]
        lines = emit_contributor_triples(
            self._cho, vals, {}, {}, "", "M", self._g,
            agents_index={_CONTRIB_AGENT_URI: agent},
        )
        label_lines = [l for l in lines if "label" in l.lower()]
        assert any("Schiller, Friedrich" in l for l in label_lines), \
            "Expected agents_index prefLabel, not val['$'] literal"

    def test_uri_case_fallback_to_literal_label_when_no_index(self):
        vals  = [{"resource": _CONTRIB_AGENT_URI, "$": "Schiller", "lang": "de"}]
        lines = emit_contributor_triples(self._cho, vals, {}, {}, "", "M", self._g)
        label_lines = [l for l in lines if "label" in l.lower()]
        assert any('"Schiller"' in l for l in label_lines)

    def test_literal_match_emits_agent_stub(self):
        agent = self._agent()
        vals  = [{"resource": "", "$": "Schiller, Friedrich", "lang": "de"}]
        lines = emit_contributor_triples(
            self._cho, vals, {}, {}, "", "M", self._g,
            agents_index={"Schiller, Friedrich": agent},
        )
        assert any(_CONTRIB_AGENT_URI in l for l in lines), "Expected agent.about URI"
        assert any("label" in l.lower() for l in lines)

    def test_literal_no_match_emits_plain_literal(self):
        vals  = [{"resource": "", "$": "Unknown Person", "lang": "de"}]
        lines = emit_contributor_triples(self._cho, vals, {}, {}, "", "M", self._g)
        assert any('"Unknown Person"@de' in l for l in lines)
        assert not any("d-nb.info" in l for l in lines)


# ── emit_prov_triples — provider_isil sanitize ────────────────────────────────

class TestEmitProvTriplesIsil:
    def test_isil_with_unsafe_chars_sanitized(self):
        from transform.emitters import emit_prov_triples
        from transform.constants import GRAPH_PROV
        record = {
            "properties": {"item-id": "A" * 32},
            "provider-info": {"provider-ddb-id": "org123", "provider-isil": "DE-<isil>"},
            "source": {},
        }
        lines = emit_prov_triples(record, f"http://example.org/{'A'*32}", GRAPH_PROV)
        isil_lines = [l for l in lines if "isil" in l.lower() or "mocho#isil" in l]
        for line in isil_lines:
            assert "<DE-<isil>" not in line
            assert "%3C" in line or "%3E" in line


# ── emit_place_stubs — split about ───────────────────────────────────────────

class TestEmitPlaceStubsSplitAbout:
    def test_space_separated_about_uses_first_only(self):
        uri1 = "http://d-nb.info/gnd/4044283-4"
        uri2 = "https://www.geonames.org/2855745"
        places = [{"about": f"{uri1} {uri2}", "prefLabel": [{"$": "Potsdam", "lang": "de"}]}]
        lines = emit_place_stubs(places, GRAPH_MOCHO)
        subjects = {l.split()[0] for l in lines}
        assert subjects == {f"<{uri1}>"}
        assert not any(uri2 in l.split()[0] for l in lines)

    def test_no_space_in_subject_iri(self):
        places = [{"about": "http://a.org/1 http://b.org/2",
                   "prefLabel": [{"$": "X", "lang": "de"}]}]
        lines = emit_place_stubs(places, GRAPH_MOCHO)
        for line in lines:
            subj = line.split()[0]
            assert " " not in subj[1:-1]


# ── emit_aggregation_triples — split resource URIs ───────────────────────────

class TestEmitAggregationSplitUri:
    _cho = "<https://gemea.ise.fiz-karlsruhe.de/mocho/" + "Z" * 32 + ">"

    def test_isshownat_two_uris(self):
        agg = {"isShownAt": {"resource": "http://a.org/1 http://b.org/2"}}
        lines = emit_aggregation_triples(agg, self._cho, GRAPH_MOCHO)
        src_lines = [l for l in lines if "source" in l or "DCTERMS" in l or
                     "http://purl.org/dc/terms/source" in l]
        assert len(lines) == 2

    def test_no_space_in_any_iri(self):
        agg = {
            "isShownAt":   {"resource": "http://a.org/1 http://a.org/2"},
            "object":      [{"resource": "http://b.org/1 http://b.org/2"}],
        }
        lines = emit_aggregation_triples(agg, self._cho, GRAPH_MOCHO)
        for line in lines:
            for part in line.split():
                if part.startswith("<") and part.endswith(">"):
                    assert " " not in part[1:-1]


# ── emit_current_location_triples ─────────────────────────────────────────────

_EDM_CURRENT_LOC = EDM_NS + "currentLocation"
_CURLOC_CHO = "<https://gemea.ise.fiz-karlsruhe.de/mocho/" + "L" * 32 + ">"


class TestEmitCurrentLocationTriples:
    def test_single_uri_emitted(self):
        uri = "http://d-nb.info/gnd/4044283-4"
        vals = [{"resource": uri, "$": "", "lang": ""}]
        lines = emit_current_location_triples(_CURLOC_CHO, vals, {}, GRAPH_MOCHO)
        assert any(_EDM_CURRENT_LOC in l and uri in l for l in lines)

    def test_literal_pass_through(self):
        vals = [{"resource": "", "$": "Stadtbibliothek", "lang": "de"}]
        lines = emit_current_location_triples(_CURLOC_CHO, vals, {}, GRAPH_MOCHO)
        assert any(_EDM_CURRENT_LOC in l and '"Stadtbibliothek"@de' in l for l in lines)

    def test_two_uris_produce_two_triples(self):
        uri1 = "http://d-nb.info/gnd/4044283-4"
        uri2 = "https://www.geonames.org/2855745"
        vals = [{"resource": f"{uri1} {uri2}"}]
        lines = emit_current_location_triples(_CURLOC_CHO, vals, {}, GRAPH_MOCHO)
        assert len([l for l in lines if _EDM_CURRENT_LOC in l]) == 2

    def test_label_stub_from_place(self):
        uri = "http://d-nb.info/gnd/4044283-4"
        place = {"about": uri, "prefLabel": [{"$": "Potsdam", "lang": "de"}]}
        vals = [{"resource": uri}]
        lines = emit_current_location_triples(_CURLOC_CHO, vals, {uri: place}, GRAPH_MOCHO)
        assert any('"Potsdam"@de' in l and uri in l for l in lines)

    def test_no_rdf_type_emitted(self):
        uri = "http://d-nb.info/gnd/4044283-4"
        place = {"about": uri, "prefLabel": [{"$": "X", "lang": "de"}]}
        vals = [{"resource": uri}]
        lines = emit_current_location_triples(_CURLOC_CHO, vals, {uri: place}, GRAPH_MOCHO)
        rdf_type = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
        assert not any(rdf_type in l for l in lines)

    def test_dedup(self):
        uri = "http://d-nb.info/gnd/4044283-4"
        vals = [{"resource": uri}, {"resource": uri}]
        lines = emit_current_location_triples(_CURLOC_CHO, vals, {}, GRAPH_MOCHO)
        assert len([l for l in lines if _EDM_CURRENT_LOC in l]) == 1

    def test_bare_id_expanded(self):
        bare = "F" * 32
        index = {bare: f"urn:ddbedm:{bare}"}
        vals = [{"resource": bare}]
        lines = emit_current_location_triples(_CURLOC_CHO, vals, {}, GRAPH_MOCHO, index)
        assert any(f"urn:ddbedm:{bare}" in l for l in lines)


# ── TestFixtures — integration tests on real corpus records ───────────────────

_FIXTURES = Path(__file__).parent / "fixtures"
_CONFIG   = CONFIG_DIR


def _fixture_configs():
    from transform.loaders import (
        load_class_prop_alignment, load_lido_event_types,
        load_htype_map, load_mediatype_class, load_audio_type2class,
    )
    return (
        load_mediatype_class(_CONFIG / "lookup_mediatype_class.csv"),
        load_htype_map(_CONFIG / "lookup_htype_doco_rico.csv"),
        load_audio_type2class(_CONFIG / "audio_type2class.json"),
        load_class_prop_alignment(_CONFIG / "lookup_class_prop_alignment.csv"),
        load_lido_event_types(_CONFIG / "lido_event_types.csv"),
    )


import json as _json


def _run_fixture(name: str) -> list[str]:
    mc_map, ht_map, at_map, cpa, lido = _fixture_configs()
    with open(_FIXTURES / f"{name}.json", encoding="utf-8") as f:
        rec = _json.load(f)
    streams, *_ = transform_record(rec, None, mc_map, ht_map, at_map, cpa, lido)
    return [nq for lines in (streams or {}).values() for nq in lines]


class TestFixtures:
    def test_multi_uri_no_space_in_iri(self):
        lines = _run_fixture("multi_uri")
        for line in lines:
            for part in line.split():
                if part.startswith("<") and part.endswith(">"):
                    assert " " not in part[1:-1], f"Space in IRI: {part}"

    def test_multi_uri_place_split(self):
        lines = _run_fixture("multi_uri")
        gnd_uri  = "http://d-nb.info/gnd/4044283-4"
        geo_uri  = "https://www.geonames.org/2855745"
        place_subjects = {l.split()[0] for l in lines if "prefLabel" in l or "label" in l.lower()}
        assert any(gnd_uri in l for l in lines), "GND place URI missing"
        assert not any(f"{gnd_uri} {geo_uri}" in l for l in lines), "URIs not split"

    def test_br_tag_normalized(self):
        lines = _run_fixture("br_tag")
        assert not any("<br" in l.lower() for l in lines), "<br> tag not normalized"
        assert any(r"\n" in l for l in lines), r"Expected \n in output"

    def test_bare_id_hastype_expanded(self):
        bare = "DJVX2BT7X2HN24O6YRDOQM6T3CNZYYY6"
        lines = _run_fixture("bare_id")
        assert not any(f"<{bare}>" in l for l in lines), "Bare ID not expanded"
        assert any(f"urn:ddbedm:{bare}" in l for l in lines), "Expanded URN missing"


# ── mocho:mediaType and mocho:sector as vocnet IRIs ───────────────────────────

_MT002 = "http://ddb.vocnet.org/medientyp/mt002"
_SPARTE006 = "http://ddb.vocnet.org/sparte/sparte006"
_MOCHO_MEDIATYPE = MOCHO_NS + "mediaType"
_MOCHO_SECTOR    = MOCHO_NS + "sector"
_CHO_ID = "A" * 32


class TestEmitMochoMediaTypeSector:

    @staticmethod
    def _run(sector: str, mediatype: str, cho_fields: dict | None = None) -> list[str]:
        mc_map, ht_map, at_map, cpa, lido = _fixture_configs()
        rdf = {"ProvidedCHO": {"about": f"http://example.org/{_CHO_ID}", **(cho_fields or {})}}
        cho_uri = f"https://gemea.ise.fiz-karlsruhe.de/mocho/{_CHO_ID}"
        ddb_uri = f"http://www.deutsche-digitale-bibliothek.de/item/{_CHO_ID}"
        lines, *_ = emit_mocho_triples(
            rdf, cho_uri, ddb_uri, sector, mediatype,
            mc_map, ht_map, at_map, cpa, lido, GRAPH_MOCHO,
        )
        return lines

    def test_mediatype_iri_emitted(self):
        lines = self._run(_SPARTE006, _MT002)
        assert any(f"<{_MOCHO_MEDIATYPE}>" in l and f"<{_MT002}>" in l for l in lines)

    def test_mediatype_any_not_emitted(self):
        lines = self._run("any", "any")
        assert not any(f"<{_MOCHO_MEDIATYPE}>" in l for l in lines)

    def test_mediatype_no_literal(self):
        lines = self._run(_SPARTE006, _MT002, cho_fields={"edmType": "IMAGE"})
        assert not any(f"<{_MOCHO_MEDIATYPE}>" in l and '"IMAGE"' in l for l in lines)

    def test_sector_iri_emitted(self):
        lines = self._run(_SPARTE006, _MT002)
        assert any(f"<{_MOCHO_SECTOR}>" in l and f"<{_SPARTE006}>" in l for l in lines)

    def test_sector_any_not_emitted(self):
        lines = self._run("any", _MT002)
        assert not any(f"<{_MOCHO_SECTOR}>" in l for l in lines)


# ── lang normalization via langcodes + IANA collection codes ─────────────────

from transform.utils import _IANA_COLLECTION_CODES, _invalid_bcp47

class TestIanaCollectionCodes:
    def test_collection_codes_loaded(self):
        assert len(_IANA_COLLECTION_CODES) > 0, "registry parse failed"

    def test_known_collective_present(self):
        for code in ("wen", "gem", "sem", "dra", "alg", "btk", "sit", "sla"):
            assert code in _IANA_COLLECTION_CODES, code

    def test_valid_individual_absent(self):
        for code in ("ger", "eng", "lat", "hsb", "dsb", "und", "zxx"):
            assert code not in _IANA_COLLECTION_CODES, code

class TestInvalidBcp47:
    def test_collective_is_invalid(self):
        for code in ("wen", "gem", "dra"):
            assert _invalid_bcp47(code), code

    def test_malformed_is_invalid(self):
        assert _invalid_bcp47("gerger")

    def test_valid_codes_not_invalid(self):
        for code in ("ger", "eng", "lat", "und", "zxx", "hsb"):
            assert not _invalid_bcp47(code), code


class TestValueToNtObjLangNorm:
    def test_wen_normalized_to_und(self):
        assert value_to_nt_obj({"$": "Janske jěchanje", "lang": "wen"}) == ['"Janske jěchanje"@und']

    def test_valid_lang_unchanged(self):
        assert value_to_nt_obj({"$": "Faust", "lang": "ger"}) == ['"Faust"@ger']

    def test_collective_to_und(self):
        assert value_to_nt_obj({"$": "some text", "lang": "gem"}) == ['"some text"@und']

    def test_malformed_to_und(self):
        assert value_to_nt_obj({"$": "text", "lang": "gerger"}) == ['"text"@und']

    def test_no_lang_unchanged(self):
        assert value_to_nt_obj({"$": "untitled"}) == ['"untitled"']

    def test_lang_coll_populated_on_collective(self):
        coll: set[str] = set()
        value_to_nt_obj({"$": "Janske jěchanje", "lang": "wen"}, lang_coll=coll)
        assert coll == {"wen"}

    def test_lang_coll_populated_on_malformed(self):
        coll: set[str] = set()
        value_to_nt_obj({"$": "text", "lang": "gerger"}, lang_coll=coll)
        assert coll == {"gerger"}

    def test_lang_coll_empty_for_valid_lang(self):
        coll: set[str] = set()
        value_to_nt_obj({"$": "Faust", "lang": "ger"}, lang_coll=coll)
        assert coll == set()

    def test_lang_coll_none_no_crash(self):
        result = value_to_nt_obj({"$": "text", "lang": "wen"}, lang_coll=None)
        assert result == ['"text"@und']

    def test_spaced_lang_normalized_to_und(self):
        # "en en" is a DDB data-quality artifact (two URIs in about → two lang attrs joined)
        result = value_to_nt_obj({"$": "Multivolume work Volume", "lang": "en en"})
        assert result == ['"Multivolume work Volume"@und']

    def test_spaced_lang_not_added_to_lang_coll(self):
        coll: set[str] = set()
        value_to_nt_obj({"$": "Multivolume work Volume", "lang": "en en"}, lang_coll=coll)
        assert coll == set()

    def test_leading_trailing_whitespace_stripped(self):
        result = value_to_nt_obj({"$": "Faust", "lang": " ger "})
        assert result == ['"Faust"@ger']


class TestEmitProvTriplesDedup:
    _RECORD = {
        "properties": {
            "item-id":         "TESTITEMID00000000000000000000000",
            "dataset-id":      "testdataset123",
            "dataset-label":   "Test Dataset",
            "revision-id":     "1",
            "ingest-date":     "2026-01-01T00:00:00+0000",
            "mapping-version": "6.18",
        },
        "provider-info": {
            "provider-ddb-id": "PROVIDERID00000000000000000000000",
            "provider-name":   "Test Provider",
        },
    }
    _CHO_URI  = "http://www.deutsche-digitale-bibliothek.de/item/TESTITEMID00000000000000000000000"
    _GRAPH    = "http://test.example/graph/prov"
    _XSLT_URI = "urn:ddbedm:xslt:6.18"
    _DS_URI   = "urn:ddbedm:dataset:testdataset123"
    _PROV_URI = "urn:ddbedm:provider:PROVIDERID00000000000000000000000"

    def _subjects(self, lines):
        return [nq.split(" ", 1)[0] for nq in lines]

    def test_emitted_none_emits_all(self):
        lines = emit_prov_triples(self._RECORD, self._CHO_URI, self._GRAPH, emitted=None)
        subjs = set(self._subjects(lines))
        assert f"<{self._XSLT_URI}>" in subjs
        assert f"<{DDB_BASE}>"       in subjs
        assert f"<{self._DS_URI}>"   in subjs
        assert f"<{self._PROV_URI}>" in subjs

    def test_emitted_dict_populated(self):
        emitted: dict[str, str] = {}
        emit_prov_triples(self._RECORD, self._CHO_URI, self._GRAPH, emitted=emitted)
        assert emitted.get(self._XSLT_URI) == "prov_xslt"
        assert emitted.get(DDB_BASE)       == "prov_ddb"
        assert emitted.get(self._DS_URI)   == "prov_dataset"
        assert emitted.get(self._PROV_URI) == "prov_provider"

    def test_xslt_skipped_when_emitted(self):
        lines = emit_prov_triples(
            self._RECORD, self._CHO_URI, self._GRAPH,
            emitted={self._XSLT_URI: "prov_xslt"},
        )
        assert f"<{self._XSLT_URI}>" not in self._subjects(lines)

    def test_ddb_agent_skipped_when_emitted(self):
        lines = emit_prov_triples(
            self._RECORD, self._CHO_URI, self._GRAPH,
            emitted={DDB_BASE: "prov_ddb"},
        )
        assert f"<{DDB_BASE}>" not in self._subjects(lines)

    def test_provider_skipped_when_emitted(self):
        lines = emit_prov_triples(
            self._RECORD, self._CHO_URI, self._GRAPH,
            emitted={self._PROV_URI: "prov_provider"},
        )
        assert f"<{self._PROV_URI}>" not in self._subjects(lines)

    def test_dataset_skipped_when_emitted(self):
        lines = emit_prov_triples(
            self._RECORD, self._CHO_URI, self._GRAPH,
            emitted={self._DS_URI: "prov_dataset"},
        )
        assert f"<{self._DS_URI}>" not in self._subjects(lines)

    def test_cho_linking_always_emitted(self):
        emitted = {
            self._XSLT_URI: "prov_xslt",
            DDB_BASE:        "prov_ddb",
            self._PROV_URI:  "prov_provider",
            self._DS_URI:    "prov_dataset",
        }
        lines = emit_prov_triples(self._RECORD, self._CHO_URI, self._GRAPH, emitted=emitted)
        cho_nt    = f"<{self._CHO_URI}>"
        cho_lines = [nq for nq in lines if nq.startswith(cho_nt)]
        predicates = {
            nq.split("> <", 1)[1].split(">", 1)[0]
            for nq in cho_lines if "> <" in nq
        }
        assert "http://www.w3.org/ns/prov#wasDerivedFrom"  in predicates
        assert "http://www.w3.org/ns/prov#wasAttributedTo" in predicates
        assert "http://www.w3.org/ns/prov#generatedAtTime" in predicates

    # ── URN format tests ──────────────────────────────────────────────────────

    def test_xslt_uri_short_prefix(self):
        lines = emit_prov_triples(self._RECORD, self._CHO_URI, self._GRAPH)
        assert any("urn:ddbedm:xslt:6.18" in nq for nq in lines)
        assert not any("urn:ddbedm:properties:mapping-version" in nq for nq in lines)

    def test_dataset_uri_short_prefix(self):
        lines = emit_prov_triples(self._RECORD, self._CHO_URI, self._GRAPH)
        assert any("urn:ddbedm:dataset:testdataset123" in nq for nq in lines)
        assert not any("urn:ddbedm:properties:dataset-id" in nq for nq in lines)

    def test_provider_uri_short_prefix(self):
        lines = emit_prov_triples(self._RECORD, self._CHO_URI, self._GRAPH)
        assert any("urn:ddbedm:provider:PROVIDERID00000000000000000000000" in nq for nq in lines)
        assert not any("urn:ddbedm:provider-info:provider-ddb-id" in nq for nq in lines)

    def test_prov_uris_no_collision_with_entity_bare_id(self):
        # DDB bare IDs are 32-char alphanumeric — prov URNs use typed prefixes with colons
        # so urn:ddbedm:provider:X and urn:ddbedm:X are distinct
        lines = emit_prov_triples(self._RECORD, self._CHO_URI, self._GRAPH)
        prov_subjects = {nq.split(" ", 1)[0] for nq in lines
                         if nq.startswith("<urn:ddbedm:")}
        for s in prov_subjects:
            # every prov URN must carry a type segment (provider:, dataset:, xslt:)
            inner = s[len("<urn:ddbedm:"):-1]
            assert ":" in inner, f"prov URN missing type prefix: {s}"


class TestEmitDdbedmTriplesDedup:
    _GRAPH = "http://test.example/graph/ddbedm"

    def _rdf_with(self, entity_type: str, about: str) -> dict:
        return {entity_type: {"about": about, "prefLabel": [{"$": "Test Label", "lang": "de"}]}}

    def test_agent_skipped_when_emitted(self):
        uri     = "http://d-nb.info/gnd/118540238"
        lines, *_ = emit_ddbedm_triples(
            self._rdf_with("Agent", uri), self._GRAPH,
            emitted={uri: "edm_agent"},
        )
        assert not any(f"<{uri}>" in nq for nq in lines)

    def test_place_skipped_when_emitted(self):
        uri     = "http://d-nb.info/gnd/4044283-4"
        lines, *_ = emit_ddbedm_triples(
            self._rdf_with("Place", uri), self._GRAPH,
            emitted={uri: "edm_place"},
        )
        assert not any(f"<{uri}>" in nq for nq in lines)

    def test_concept_skipped_when_emitted(self):
        uri     = "http://d-nb.info/gnd/4020531-9"
        lines, *_ = emit_ddbedm_triples(
            self._rdf_with("Concept", uri), self._GRAPH,
            emitted={uri: "skos_concept"},
        )
        assert not any(f"<{uri}>" in nq for nq in lines)

    def test_timespan_skipped_when_emitted(self):
        uri     = "http://d-nb.info/gnd/4806547-9"
        lines, *_ = emit_ddbedm_triples(
            self._rdf_with("TimeSpan", uri), self._GRAPH,
            emitted={uri: "edm_timespan"},
        )
        assert not any(f"<{uri}>" in nq for nq in lines)

    def test_provided_cho_never_skipped(self):
        cho_uri = "http://www.deutsche-digitale-bibliothek.de/item/ABCABC"
        lines, *_ = emit_ddbedm_triples(
            {"ProvidedCHO": {"about": cho_uri}}, self._GRAPH,
            emitted={cho_uri: "edm_agent"},  # present in dict but not a dedup type
        )
        assert any(f"<{cho_uri}>" in nq for nq in lines)

    def test_emitted_none_emits_all(self):
        uri     = "http://d-nb.info/gnd/118540238"
        lines, *_ = emit_ddbedm_triples(
            self._rdf_with("Agent", uri), self._GRAPH, emitted=None,
        )
        assert any(f"<{uri}>" in nq for nq in lines)

    def test_second_record_shares_agent(self):
        uri     = "http://d-nb.info/gnd/118540238"
        emitted: dict[str, str] = {}
        rdf = self._rdf_with("Agent", uri)
        lines1, *_ = emit_ddbedm_triples(rdf, self._GRAPH, emitted=emitted)
        lines2, *_ = emit_ddbedm_triples(rdf, self._GRAPH, emitted=emitted)
        assert len([nq for nq in lines1 if f"<{uri}>" in nq]) > 0
        assert len([nq for nq in lines2 if f"<{uri}>" in nq]) == 0

    def test_emitted_dict_populated_with_type(self):
        uri     = "http://d-nb.info/gnd/118540238"
        emitted: dict[str, str] = {}
        emit_ddbedm_triples(self._rdf_with("Agent", uri), self._GRAPH, emitted=emitted)
        assert emitted.get(uri) == "edm_agent"


# ── emit_prov_triples — dcterms:references as DDB item IRI (alignment Decision 6) ──

class TestEmitProvReferences:
    _ITEM  = "TESTITEMID00000000000000000000000"
    _OTHER = "OTHERITEM000000000000000000000000"
    _CHO   = f"http://www.deutsche-digitale-bibliothek.de/item/{_ITEM}"
    _REF_P = "<http://purl.org/dc/terms/references>"

    def _record(self, ref):
        rec = {"properties": {"item-id": self._ITEM}}
        if ref is not None:
            rec["source"] = {"description": {"record": {"ref": ref, "href": f"/items/{ref}/source/record"}}}
        return rec

    def _references(self, lines):
        return [nq for nq in lines if f" {self._REF_P} " in nq]

    def test_other_item_ref_is_ddb_item_iri(self):
        lines = emit_prov_triples(self._record(self._OTHER), self._CHO, GRAPH_PROV)

        refs = self._references(lines)
        assert len(refs) == 1
        assert f"{self._REF_P} <http://www.deutsche-digitale-bibliothek.de/item/{self._OTHER}> " in refs[0]

    def test_self_reference_is_kept(self):
        lines = emit_prov_triples(self._record(self._ITEM), self._CHO, GRAPH_PROV)

        refs = self._references(lines)
        assert refs == [f"<{self._CHO}> {self._REF_P} <{self._CHO}> <{GRAPH_PROV}> ."]

    def test_no_source_ref_emits_no_references(self):
        lines = emit_prov_triples(self._record(None), self._CHO, GRAPH_PROV)

        assert self._references(lines) == []

    def test_old_ddb_literal_is_gone(self):
        lines = emit_prov_triples(self._record(self._OTHER), self._CHO, GRAPH_PROV)

        assert not any('"ddb:' in nq for nq in lines)
