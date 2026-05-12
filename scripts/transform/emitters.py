"""Triple emitters: ddbedm passthrough, mocho alignment, PROV-O, and werk_staging."""

from __future__ import annotations

from collections import Counter

from .constants import (
    AgentDict, NQList, PropAlign,
    RDF_TYPE, RDFS_LABEL, OWL_SAMEAS,
    DCTERMS_SOURCE, DCTERMS_ID, DCTERMS_TYPE, DCTERMS_HAS_VER, DCTERMS_REF,
    DCTERMS_RIGHTS, DCTERMS_CREATOR, DCTERMS_SUBJECT,
    DC_ID, DC_TITLE, DC_DESCRIPTION, DC_SUBJECT, DC_CONTRIBUTOR,
    FOAF_ORG, FOAF_NAME, FOAF_THUMBNAIL,
    SCHEMA_URL, MOCHO_ISIL, MOCHO_NS, MOCHO_AGENT, RICO_HAS_RST,
    PROV_ENTITY, PROV_AGENT, PROV_SW_AGENT,
    PROV_DERIVED, PROV_ATTRIBUTED, PROV_GEN_TIME, PROV_ON_BEHALF,
    DCAT_DATASET, XSD_DATETIME,
    DDB_BASE, DDB_ITEM_BASE, DDB_API_BASE, DDB_HIERARCHY_TYPE, EDM_NS, EDM_HAS_TYPE,
    _HTYPE_PREFIX,
    _EDM_ENTITY_TYPES, _DDBEDM_PROP, _MOCHO_SKIP, _NEW_NS,
    _CLASS_WEMI, _CONTRIBUTOR_COL, _W_SLOT_CLASSES, SUBJECT_KEYS,
)
from .utils import (
    make_nq, coerce_list, mint_bare_id, _escape_literal, _sanitize_iri,
    value_to_nt_obj, normalize_date, is_ddb_or_gnd, resolve_agent, _to_curie,
    build_bare_id_index, expand_obj_nt, resource_uris,
)


def emit_ddbedm_triples(
    rdf: dict,
    graph_iri: str,
    lang_coll: set[str] | None = None,
) -> tuple[NQList, Counter, Counter, Counter]:
    """Emit verbatim EDM passthrough triples for all entity types in rdf (§6.1).

    Subject: first URI in entity['about']; owl:sameAs emitted for any additional URIs.
    Includes mt007 records.
    Returns (lines, class_ctr, pred_ctr, sani_ctr) where sani_ctr tracks
    uri_sanitized, uri_split, and uri_about_split counts.
    """
    lines:     NQList  = []
    class_ctr: Counter = Counter()
    pred_ctr:  Counter = Counter()
    sani_ctr:  Counter = Counter()
    _skip = frozenset({"about"})
    bare_id_to_uri = build_bare_id_index(rdf)
    for entity_type, entities in rdf.items():
        edm_class = _EDM_ENTITY_TYPES.get(entity_type)
        for entity in coerce_list(entities):
            if not isinstance(entity, dict):
                continue
            raw_about = (entity.get("about") or "").strip()
            if not raw_about:
                continue
            about_parts = raw_about.split()
            subj_uri = mint_bare_id(entity_type, _sanitize_iri(about_parts[0]))
            subj_nt  = f"<{subj_uri}>"
            if len(about_parts) > 1:
                sani_ctr["uri_about_split"] += len(about_parts) - 1
                for alt in about_parts[1:]:
                    lines.append(make_nq(subj_nt, f"<{OWL_SAMEAS}>",
                                         f"<{_sanitize_iri(alt)}>", graph_iri))
            if edm_class:
                lines.append(make_nq(subj_nt, f"<{RDF_TYPE}>", f"<{edm_class}>", graph_iri))
                class_ctr[_to_curie(edm_class)] += 1
            for key, val in entity.items():
                if key in _skip:
                    continue
                pred_iri = _DDBEDM_PROP.get(key)
                if not pred_iri:
                    continue
                pred_nt = f"<{pred_iri}>"
                curie   = _to_curie(pred_iri)
                for obj_nt in value_to_nt_obj(val, sani_ctr, lang_coll):
                    lines.append(make_nq(subj_nt, pred_nt,
                                         expand_obj_nt(obj_nt, bare_id_to_uri), graph_iri))
                    pred_ctr[curie] += 1
    return lines, class_ctr, pred_ctr, sani_ctr


def emit_prov_triples(record: dict, ddb_cho_uri: str, graph_iri: str) -> NQList:
    """Emit PROV-O Layer 1 (Without-Activity) for one record (§6.2, ddbedm-prov-o-plan.md §2)."""
    lines: NQList = []
    props = record.get("properties") or {}
    prov  = record.get("provider-info") or {}

    item_id         = (props.get("item-id")         or "").strip()
    dataset_id      = (props.get("dataset-id")      or "").strip()
    dataset_label   = (props.get("dataset-label")   or "").strip()
    revision        = (props.get("revision-id")     or "").strip()
    ingest_dt       = (props.get("ingest-date")     or "").strip()
    map_ver         = (props.get("mapping-version") or "").strip()
    provider_ddb_id = (prov.get("provider-ddb-id")  or "").strip()
    provider_name   = (prov.get("provider-name")    or "").strip()
    provider_uri    = (prov.get("provider-uri")     or "").strip()
    provider_id     = (prov.get("provider-id")      or "").strip()
    provider_isil   = (prov.get("provider-isil")    or "").strip()

    src_desc  = (record.get("source") or {}).get("description") or {}
    src_ref   = (src_desc.get("record") or {}) if isinstance(src_desc, dict) else {}
    ref_val   = (src_ref.get("ref",  "") or "").strip() if isinstance(src_ref, dict) else ""
    src_href  = (src_ref.get("href", "") or "").strip() if isinstance(src_ref, dict) else ""
    rec_type  = (src_ref.get("type", "") or "").strip() if isinstance(src_ref, dict) else ""

    ds_uri   = f"urn:ddbedm:properties:dataset-id:{dataset_id}"      if dataset_id      else ""
    xslt_uri = f"urn:ddbedm:properties:mapping-version:{map_ver}"    if map_ver         else ""
    prov_uri = f"urn:ddbedm:provider-info:provider-ddb-id:{provider_ddb_id}" \
               if provider_ddb_id else ""

    # ── CHO node ──────────────────────────────────────────────────────────────
    cho_nt = f"<{ddb_cho_uri}>"
    lines.append(make_nq(cho_nt, f"<{RDF_TYPE}>", f"<{PROV_ENTITY}>", graph_iri))
    if ds_uri:
        lines.append(make_nq(cho_nt, f"<{PROV_DERIVED}>",   f"<{ds_uri}>",   graph_iri))
    if xslt_uri:
        lines.append(make_nq(cho_nt, f"<{PROV_ATTRIBUTED}>", f"<{xslt_uri}>", graph_iri))
    if ingest_dt:
        lines.append(make_nq(cho_nt, f"<{PROV_GEN_TIME}>",
                             f'"{_escape_literal(ingest_dt)}"^^<{XSD_DATETIME}>', graph_iri))
    if revision:
        lines.append(make_nq(cho_nt, f"<{DCTERMS_HAS_VER}>",
                             f'"{_escape_literal(revision)}"', graph_iri))
    if ref_val:
        lines.append(make_nq(cho_nt, f"<{DCTERMS_REF}>",
                             f'"ddb:{_escape_literal(ref_val)}"', graph_iri))

    # ── Dataset node ──────────────────────────────────────────────────────────
    if ds_uri:
        ds_nt = f"<{ds_uri}>"
        lines.append(make_nq(ds_nt, f"<{RDF_TYPE}>", f"<{DCAT_DATASET}>", graph_iri))
        lines.append(make_nq(ds_nt, f"<{RDF_TYPE}>", f"<{PROV_ENTITY}>",  graph_iri))
        lines.append(make_nq(ds_nt, f"<{DCTERMS_ID}>",
                             f'"{_escape_literal(dataset_id)}"', graph_iri))
        if dataset_label:
            lines.append(make_nq(ds_nt, f"<{RDFS_LABEL}>",
                                 f'"{_escape_literal(dataset_label)}"@de', graph_iri))
        if rec_type:
            lines.append(make_nq(ds_nt, f"<{DCTERMS_TYPE}>", f"<{rec_type}>", graph_iri))
        if prov_uri:
            lines.append(make_nq(ds_nt, f"<{PROV_ATTRIBUTED}>",
                                 f"<{prov_uri}>", graph_iri))

    # ── XSLT SoftwareAgent node ───────────────────────────────────────────────
    if xslt_uri:
        xslt_nt = f"<{xslt_uri}>"
        lines.append(make_nq(xslt_nt, f"<{RDF_TYPE}>", f"<{PROV_SW_AGENT}>", graph_iri))
        lines.append(make_nq(xslt_nt, f"<{DCTERMS_HAS_VER}>",
                             f'"{_escape_literal(map_ver)}"', graph_iri))
        lines.append(make_nq(xslt_nt, f"<{PROV_ON_BEHALF}>", f"<{DDB_BASE}>", graph_iri))

    # ── DDB Agent node (fixed URI) ────────────────────────────────────────────
    ddb_nt = f"<{DDB_BASE}>"
    lines.append(make_nq(ddb_nt, f"<{RDF_TYPE}>", f"<{PROV_AGENT}>", graph_iri))
    lines.append(make_nq(ddb_nt, f"<{RDF_TYPE}>", f"<{FOAF_ORG}>",   graph_iri))
    lines.append(make_nq(ddb_nt, f"<{FOAF_NAME}>",
                         '"Deutsche Digitale Bibliothek"', graph_iri))

    # ── Provider Agent node ───────────────────────────────────────────────────
    if prov_uri:
        prov_nt = f"<{prov_uri}>"
        lines.append(make_nq(prov_nt, f"<{RDF_TYPE}>", f"<{PROV_AGENT}>", graph_iri))
        lines.append(make_nq(prov_nt, f"<{RDF_TYPE}>", f"<{FOAF_ORG}>",   graph_iri))
        if provider_name:
            lines.append(make_nq(prov_nt, f"<{FOAF_NAME}>",
                                 f'"{_escape_literal(provider_name)}"', graph_iri))
        if provider_uri:
            lines.append(make_nq(prov_nt, f"<{SCHEMA_URL}>", f"<{provider_uri}>", graph_iri))
        if provider_id:
            lines.append(make_nq(prov_nt, f"<{DCTERMS_ID}>",
                                 f'"{_escape_literal(provider_id)}"', graph_iri))
        if provider_isil:
            lines.append(make_nq(prov_nt, f"<{MOCHO_ISIL}>",
                                 f"<{_sanitize_iri(provider_isil)}>", graph_iri))

    # ── SourceRecord node (one per binary entry under same URI) ───────────────
    if src_href:
        src_rec_uri = DDB_API_BASE + src_href.lstrip("/")
        src_nt      = f"<{src_rec_uri}>"
        lines.append(make_nq(src_nt, f"<{RDF_TYPE}>", f"<{PROV_ENTITY}>", graph_iri))
        for binary in coerce_list((record.get("binaries") or {}).get("binary")):
            if not isinstance(binary, dict):
                continue
            ref   = (binary.get("ref")            or "").strip()
            name  = (binary.get("name")           or "").strip()
            name2 = (binary.get("name2")          or "").strip()
            kind  = (binary.get("kind")           or "").strip()
            lpath = (binary.get("local_pathname") or "").strip()
            if ref:
                lines.append(make_nq(src_nt, f"<{DC_ID}>",
                                     f'"{_escape_literal(ref)}"', graph_iri))
            if name:
                lines.append(make_nq(src_nt, f"<{DC_TITLE}>",
                                     f'"{_escape_literal(name)}"@de', graph_iri))
            desc = (name2 + " | " + name) if name2 and name else (name2 or name)
            if desc:
                lines.append(make_nq(src_nt, f"<{DC_DESCRIPTION}>",
                                     f'"{_escape_literal(desc)}"@de', graph_iri))
            if kind:
                lines.append(make_nq(src_nt, f"<{DCTERMS_RIGHTS}>", f"<{kind}>", graph_iri))
            if lpath and lpath.startswith("http"):
                lines.append(make_nq(src_nt, f"<{DCTERMS_SOURCE}>", f"<{lpath}>", graph_iri))

    return lines


def retype_entities(
    sector: str,
    mediatype: str,
    htype_code: str | None,
    dctype_vals: list,
    cho_nt: str,
    mediatype_class_map: dict,
    htype_map: dict,
    audio_type2class: dict,
    graph_iri: str,
) -> tuple[NQList, str, str, dict]:
    """Dispatch rdf:type triples for a ProvidedCHO (§6.4, transform-revised-plan.md §1.1).

    Returns (lines, target_class_full_iri, wemi, dispatch_flags).
    target_class is used for property alignment lookup; wemi for contributor column selection.
    dispatch_flags: {"htype_used": bool, "fallback": bool}
    """
    lines: NQList = []
    row = (
        mediatype_class_map.get((sector, mediatype))
        or mediatype_class_map.get(("any", "any"))
        or {}
    )
    use_htype  = row.get("use_htype", False)
    rdf_type_w = row.get("rdf_type_w", "")
    rdf_type_m = row.get("rdf_type_m", "")

    primary_class = ""
    htype_used    = False

    # Layer 1: htype-derived class (for htype-first strata)
    # hierarchyType may be space-separated (e.g. "htype_007 htype_020"); use first for dispatch
    if use_htype and htype_code:
        entry = htype_map.get(htype_code.split()[0])
        if entry:
            type_iris, rst_iris = entry
            for t in type_iris:
                lines.append(make_nq(cho_nt, f"<{RDF_TYPE}>", f"<{t}>", graph_iri))
            for r in rst_iris:
                lines.append(make_nq(cho_nt, f"<{RICO_HAS_RST}>", f"<{r}>", graph_iri))
            primary_class = type_iris[0]  # first = most specific
            htype_used    = True

    # Layer 2: fixed mediatype class (always emit if set; added on top of layer 1)
    if rdf_type_w:
        lines.append(make_nq(cho_nt, f"<{RDF_TYPE}>", f"<{rdf_type_w}>", graph_iri))
        if not primary_class:
            primary_class = rdf_type_w
    if rdf_type_m:
        lines.append(make_nq(cho_nt, f"<{RDF_TYPE}>", f"<{rdf_type_m}>", graph_iri))
        if not primary_class:
            primary_class = rdf_type_m

    # Audio group dispatch: dc:type → mo:MusicalManifestation (Group A) (§2.3)
    _aco_audio = "https://w3id.org/ac-ontology/aco#AudioManifestation"
    _mo_mani   = "http://purl.org/ontology/mo/MusicalManifestation"
    if rdf_type_m == _aco_audio and dctype_vals and not use_htype:
        for dct in dctype_vals:
            dc_text = (dct.get("$") or "").strip() if isinstance(dct, dict) else ""
            if audio_type2class.get((sector, dc_text)) == "A":
                lines.append(make_nq(cho_nt, f"<{RDF_TYPE}>", f"<{_mo_mani}>", graph_iri))
                primary_class = _mo_mani
                break

    # D9 fallback — no class resolved
    is_fallback = not primary_class
    if is_fallback:
        fallback = MOCHO_NS + "Manifestation"
        lines.append(make_nq(cho_nt, f"<{RDF_TYPE}>", f"<{fallback}>", graph_iri))
        primary_class = fallback

    # hierarchyType may be space-separated; emit one triple per code with correct URI form
    # (raw: "htype_007" → vocnet: "ht007")
    if htype_code:
        for _code in htype_code.split():
            _uri_code = _code.replace("htype_", "ht")
            lines.append(make_nq(cho_nt, f"<{DDB_HIERARCHY_TYPE}>",
                                 f"<{_HTYPE_PREFIX}{_uri_code}>", graph_iri))

    wemi = _CLASS_WEMI.get(primary_class, "M")
    return lines, primary_class, wemi, {"htype_used": htype_used, "fallback": is_fallback}


def emit_subject_triples(
    cho_nt: str,
    subject_vals: list,
    concepts_index: dict[str, dict],
    graph_iri: str,
    bare_id_to_uri: dict[str, str] | None = None,
) -> NQList:
    """Emit dcterms:subject (IRI path) or dc:subject (literal path) per value (D1 amended)."""
    lines: NQList = []
    seen: set[str] = set()
    _bare = bare_id_to_uri or {}
    for val in subject_vals:
        if not isinstance(val, dict):
            continue
        resource_raw = (val.get("resource") or "").strip()
        label        = (val.get("$")        or "").strip()
        lang         = (val.get("lang")     or "").strip()
        if resource_raw:
            for uri in resource_uris(resource_raw, _bare, "Concept"):
                if uri in seen:
                    continue
                seen.add(uri)
                lines.append(make_nq(cho_nt, f"<{DCTERMS_SUBJECT}>", f"<{uri}>", graph_iri))
                concept = concepts_index.get(resource_raw) or concepts_index.get(uri)
                if concept:
                    for pl in coerce_list(concept.get("prefLabel")):
                        for obj_nt in value_to_nt_obj(pl):
                            lines.append(make_nq(f"<{uri}>", f"<{RDFS_LABEL}>",
                                                 obj_nt, graph_iri))
        elif label:
            key = f"lit:{label}"
            if key in seen:
                continue
            seen.add(key)
            escaped = _escape_literal(label)
            obj_nt  = f'"{escaped}"@{lang}' if lang else f'"{escaped}"'
            lines.append(make_nq(cho_nt, f"<{DC_SUBJECT}>", obj_nt, graph_iri))
    return lines


def emit_hastype_triples(
    cho_nt: str,
    hastype_vals: list,
    concepts_index: dict[str, dict],
    graph_iri: str,
    bare_id_to_uri: dict[str, str] | None = None,
) -> NQList:
    """Emit edm:hasType + rdfs:label stub for each IRI-valued hasType entry.

    Bare 32-char IDs are expanded via the per-record index (fallback: mint as Concept URN).
    Literal-only values (no resource) are silently skipped — edm:hasType range is skos:Concept.
    """
    lines: NQList = []
    seen: set[str] = set()
    _bare = bare_id_to_uri or {}
    for val in coerce_list(hastype_vals):
        if not isinstance(val, dict):
            continue
        resource_raw = (val.get("resource") or "").strip()
        if not resource_raw:
            continue
        for uri in resource_uris(resource_raw, _bare, "Concept"):
            if uri in seen:
                continue
            seen.add(uri)
            lines.append(make_nq(cho_nt, f"<{EDM_HAS_TYPE}>", f"<{uri}>", graph_iri))
            concept = concepts_index.get(resource_raw) or concepts_index.get(uri)
            if concept:
                for pl in coerce_list(concept.get("prefLabel")):
                    for obj_nt in value_to_nt_obj(pl):
                        lines.append(make_nq(f"<{uri}>", f"<{RDFS_LABEL}>",
                                             obj_nt, graph_iri))
    return lines


def emit_current_location_triples(
    cho_nt: str,
    currentloc_vals: object,
    places_index: dict[str, dict],
    graph_iri: str,
    bare_id_to_uri: dict[str, str] | None = None,
) -> NQList:
    """Emit edm:currentLocation triples with optional rdfs:label stub from matching edm:Place.

    URI values: emit <cho> edm:currentLocation <uri> + <uri> rdfs:label <prefLabel> if found.
    Literal values: emit <cho> edm:currentLocation "literal" (pass-through, no stub).
    Multi-URI resource fields are split; bare IDs are expanded. Deduplicates URIs per record.
    No rdf:type emitted for Place stubs in mocho graph (D17).
    """
    edm_current_location = EDM_NS + "currentLocation"
    lines: NQList = []
    seen: set[str] = set()
    _bare = bare_id_to_uri or {}

    for val in coerce_list(currentloc_vals):
        if not isinstance(val, dict):
            continue
        resource_raw = (val.get("resource") or "").strip()
        label        = (val.get("$")        or "").strip()
        lang         = (val.get("lang")     or "").strip()

        if resource_raw:
            for uri in resource_uris(resource_raw, _bare, "Place"):
                if uri in seen:
                    continue
                seen.add(uri)
                lines.append(make_nq(cho_nt, f"<{edm_current_location}>",
                                     f"<{uri}>", graph_iri))
                place = places_index.get(resource_raw) or places_index.get(uri)
                if place:
                    for pl in coerce_list(place.get("prefLabel")):
                        for obj_nt in value_to_nt_obj(pl):
                            lines.append(make_nq(f"<{uri}>", f"<{RDFS_LABEL}>",
                                                 obj_nt, graph_iri))
        elif label:
            escaped = _escape_literal(label)
            obj_nt  = f'"{escaped}"@{lang}' if lang else f'"{escaped}"'
            lines.append(make_nq(cho_nt, f"<{edm_current_location}>", obj_nt, graph_iri))
    return lines


def emit_creator_triples(
    cho_nt: str,
    creator_vals: list,
    agents_index: dict[str, AgentDict],
    target_class: str,
    class_prop_align: PropAlign,
    graph_iri: str,
    bare_id_to_uri: dict[str, str] | None = None,
) -> NQList:
    """Emit class-specific creator predicate (Track 1) and dcterms:creator agent stub (Track 2).

    Both tracks run independently for each creator value (D2 / props-mapping §4).
    """
    lines: NQList = []
    dc_creator_iri = "http://purl.org/dc/elements/1.1/creator"
    track1_prop    = class_prop_align.get((target_class, dc_creator_iri), "")

    for val in coerce_list(creator_vals):
        if not isinstance(val, dict):
            continue
        resource_raw = (val.get("resource") or "").strip()
        label        = (val.get("$")        or "").strip()
        lang         = (val.get("lang")     or "").strip()
        primary_resource = resource_raw.split()[0] if resource_raw else ""

        # Track 1: class-specific predicate (always runs when target_prop is known)
        if track1_prop:
            if resource_raw:
                for uri in resource_uris(resource_raw, bare_id_to_uri, "Agent"):
                    lines.append(make_nq(cho_nt, f"<{track1_prop}>", f"<{uri}>", graph_iri))
            elif label:
                escaped = _escape_literal(label)
                obj_nt  = f'"{escaped}"@{lang}' if lang else f'"{escaped}"'
                lines.append(make_nq(cho_nt, f"<{track1_prop}>", obj_nt, graph_iri))

        # Track 2: generic dcterms:creator + agent stub (D2 — both tracks always run)
        agent = resolve_agent(label, primary_resource, agents_index)
        if agent:
            agent_uri = _sanitize_iri((agent.get("about") or "").strip())
            if agent_uri and is_ddb_or_gnd(agent_uri):
                lines.append(make_nq(cho_nt, f"<{DCTERMS_CREATOR}>",
                                     f"<{agent_uri}>", graph_iri))
                agent_nt = f"<{agent_uri}>"
                lines.append(make_nq(agent_nt, f"<{RDF_TYPE}>", f"<{MOCHO_AGENT}>", graph_iri))
                pref_list = coerce_list(agent.get("prefLabel"))
                if pref_list:
                    for pl in pref_list:
                        for obj_nt in value_to_nt_obj(pl):
                            lines.append(make_nq(agent_nt, f"<{RDFS_LABEL}>", obj_nt, graph_iri))
                elif label:
                    escaped = _escape_literal(label)
                    obj_nt = f'"{escaped}"@{lang}' if lang else f'"{escaped}"'
                    lines.append(make_nq(agent_nt, f"<{RDFS_LABEL}>", obj_nt, graph_iri))
    return lines


def emit_contributor_triples(
    cho_nt: str,
    contributor_vals: list,
    event_participant_index: dict[str, str],
    lido_dispatch: dict[str, dict],
    target_class: str,
    wemi: str,
    graph_iri: str,
    bare_id_to_uri: dict[str, str] | None = None,
    agents_index: dict[str, AgentDict] | None = None,
) -> NQList:
    """Emit contributor triples using LIDO event-type dispatch (D3/D25, props-mapping §5)."""
    lines: NQList = []
    prop_col = _CONTRIBUTOR_COL.get((wemi, target_class), "dc_agent_fallback")
    _agents  = agents_index or {}

    for val in coerce_list(contributor_vals):
        if not isinstance(val, dict):
            continue
        resource_raw     = (val.get("resource") or "").strip()
        label            = (val.get("$")        or "").strip()
        lang             = (val.get("lang")     or "").strip()
        primary_resource = resource_raw.split()[0] if resource_raw else ""

        lido_type   = event_participant_index.get(primary_resource) if primary_resource else None
        lido_row    = lido_dispatch.get(lido_type) if lido_type else None
        target_prop = (
            (lido_row.get(prop_col) or lido_row.get("dc_agent_fallback") or DC_CONTRIBUTOR)
            if lido_row else DC_CONTRIBUTOR
        )

        if resource_raw:
            agent     = resolve_agent("", primary_resource, _agents)
            pref_list = coerce_list(agent.get("prefLabel")) if agent else []
            for uri in resource_uris(resource_raw, bare_id_to_uri, "Agent"):
                lines.append(make_nq(cho_nt, f"<{target_prop}>", f"<{uri}>", graph_iri))
                agent_nt = f"<{uri}>"
                lines.append(make_nq(agent_nt, f"<{RDF_TYPE}>", f"<{MOCHO_AGENT}>", graph_iri))
                if pref_list:
                    for pl in pref_list:
                        for obj_nt in value_to_nt_obj(pl):
                            lines.append(make_nq(agent_nt, f"<{RDFS_LABEL}>", obj_nt, graph_iri))
                elif label:
                    escaped = _escape_literal(label)
                    obj_nt  = f'"{escaped}"@{lang}' if lang else f'"{escaped}"'
                    lines.append(make_nq(agent_nt, f"<{RDFS_LABEL}>", obj_nt, graph_iri))
        elif label:
            agent = resolve_agent(label, "", _agents)
            if agent:
                agent_uri = _sanitize_iri((agent.get("about") or "").strip())
                if agent_uri and is_ddb_or_gnd(agent_uri):
                    lines.append(make_nq(cho_nt, f"<{target_prop}>", f"<{agent_uri}>", graph_iri))
                    agent_nt  = f"<{agent_uri}>"
                    lines.append(make_nq(agent_nt, f"<{RDF_TYPE}>", f"<{MOCHO_AGENT}>", graph_iri))
                    pref_list = coerce_list(agent.get("prefLabel"))
                    if pref_list:
                        for pl in pref_list:
                            for obj_nt in value_to_nt_obj(pl):
                                lines.append(make_nq(agent_nt, f"<{RDFS_LABEL}>", obj_nt, graph_iri))
                    else:
                        escaped = _escape_literal(label)
                        obj_nt  = f'"{escaped}"@{lang}' if lang else f'"{escaped}"'
                        lines.append(make_nq(agent_nt, f"<{RDFS_LABEL}>", obj_nt, graph_iri))
                    continue
            escaped = _escape_literal(label)
            obj_nt  = f'"{escaped}"@{lang}' if lang else f'"{escaped}"'
            lines.append(make_nq(cho_nt, f"<{DC_CONTRIBUTOR}>", obj_nt, graph_iri))
    return lines


def emit_aggregation_triples(agg: dict, cho_nt: str, graph_iri: str) -> NQList:
    """Emit mocho triples derived from the Aggregation block (D23)."""
    lines: NQList = []
    _edm_dp     = EDM_NS + "dataProvider"
    _org_prefix = "http://www.deutsche-digitale-bibliothek.de/organization/"

    is_shown = agg.get("isShownAt") or {}
    if isinstance(is_shown, dict):
        for uri in (is_shown.get("resource") or "").strip().split():
            lines.append(make_nq(cho_nt, f"<{DCTERMS_SOURCE}>",
                                 f"<{_sanitize_iri(uri)}>", graph_iri))

    for dp in coerce_list(agg.get("dataProvider")):
        if not isinstance(dp, dict):
            continue
        for uri in (dp.get("resource") or "").strip().split():
            if uri.startswith(_org_prefix):
                lines.append(make_nq(cho_nt, f"<{_edm_dp}>",
                                     f"<{_sanitize_iri(uri)}>", graph_iri))

    for obj in coerce_list(agg.get("object")):
        if not isinstance(obj, dict):
            continue
        for uri in (obj.get("resource") or "").strip().split():
            lines.append(make_nq(cho_nt, f"<{FOAF_THUMBNAIL}>",
                                 f"<{_sanitize_iri(uri)}>", graph_iri))

    return lines


def emit_place_stubs(places: object, graph_iri: str) -> NQList:
    """Emit rdfs:label stubs for each Place entity referenced by the record (D24)."""
    lines: NQList = []
    for place in coerce_list(places):
        if not isinstance(place, dict):
            continue
        raw_about = (place.get("about") or "").strip()
        if not raw_about:
            continue
        place_uri = mint_bare_id("Place", _sanitize_iri(raw_about.split()[0]))
        place_nt  = f"<{place_uri}>"
        for lbl in coerce_list(place.get("prefLabel")):
            for obj_nt in value_to_nt_obj(lbl):
                lines.append(make_nq(place_nt, f"<{RDFS_LABEL}>", obj_nt, graph_iri))
    return lines


def werk_staging_row(cho_uri: str, cho: dict, target_class: str) -> dict | None:
    """Build a werk_staging dict if target_class is a W-slot class (D26). Else None."""
    if target_class not in _W_SLOT_CLASSES:
        return None

    title = ""
    tv = cho.get("title")
    if isinstance(tv, dict):
        title = (tv.get("$") or "").strip()
    elif isinstance(tv, list) and tv:
        first = tv[0]
        title = (first.get("$") or "").strip() if isinstance(first, dict) else ""

    dc_alt: list[str] = []
    for v in coerce_list(cho.get("alternative")):
        t = (v.get("$") or "").strip() if isinstance(v, dict) else ""
        if t:
            dc_alt.append(t)

    dc_created = ""
    for v in coerce_list(cho.get("date")):
        t = (v if isinstance(v, str) else "").strip()
        if t:
            dc_created = t
            break

    creator_uris: list[str] = []
    creator_lits: list[str] = []
    for v in coerce_list(cho.get("creator")):
        if not isinstance(v, dict):
            continue
        uri = (v.get("resource") or "").strip()
        lit = (v.get("$")        or "").strip()
        if uri:
            creator_uris.append(uri)
        elif lit:
            creator_lits.append(lit)

    obj_id = cho_uri.rsplit("/", 1)[-1]
    return {
        "ddb_obj_id":       obj_id,
        "cho_uri":          cho_uri,
        "target_class":     target_class,
        "dc_title":         title,
        "dc_alternative":   dc_alt,
        "dc_created":       dc_created,
        "creator_uris":     creator_uris,
        "creator_literals": creator_lits,
    }


def emit_mocho_triples(
    rdf: dict,
    cho_uri: str,
    ddb_uri: str,
    sector: str,
    mediatype: str,
    mediatype_class_map: dict,
    htype_map: dict,
    audio_type2class: dict,
    class_prop_align: PropAlign,
    lido_dispatch: dict,
    graph_iri: str,
    lang_coll: set[str] | None = None,
) -> tuple[NQList, str, str, dict]:
    """Emit all mocho-graph triples for one record (§6.3). Returns (lines, target_class, wemi, dispatch_flags).

    dispatch_flags includes "preds_all" and "preds_new" Counters (CURIEs) built during emission.
    """
    lines:     NQList  = []
    preds_all: Counter = Counter()
    preds_new: Counter = Counter()
    sani_ctr:  Counter = Counter()
    bare_id_to_uri = build_bare_id_index(rdf)

    def _track(pred_iri: str) -> None:
        curie = _to_curie(pred_iri)
        preds_all[curie] += 1
        if any(pred_iri.startswith(ns) for ns in _NEW_NS):
            preds_new[curie] += 1

    def _track_nqlist(nqlist: NQList) -> None:
        for nq in nqlist:
            try:
                _track(nq.split("> <", 1)[1].split(">", 1)[0])
            except IndexError:
                pass

    cho: dict = rdf.get("ProvidedCHO") or {}
    if isinstance(cho, list):
        cho = cho[0] if cho else {}

    cho_nt      = f"<{cho_uri}>"
    htype       = (cho.get("hierarchyType") or "").strip() or None
    dctype_vals = coerce_list(cho.get("dcType"))

    # ── Class dispatch ────────────────────────────────────────────────────────
    type_lines, target_class, wemi, dispatch_flags = retype_entities(
        sector, mediatype, htype, dctype_vals,
        cho_nt, mediatype_class_map, htype_map, audio_type2class, graph_iri,
    )
    lines.extend(type_lines)
    _track_nqlist(type_lines)

    # owl:sameAs link to original DDB URI (D22)
    lines.append(make_nq(cho_nt, f"<{OWL_SAMEAS}>", f"<{ddb_uri}>", graph_iri))
    _track(OWL_SAMEAS)

    # mocho:mediaType and mocho:sector as vocnet IRIs
    if mediatype != "any":
        lines.append(make_nq(cho_nt, f"<{MOCHO_NS}mediaType>", f"<{mediatype}>", graph_iri))
        _track(MOCHO_NS + "mediaType")
    if sector != "any":
        lines.append(make_nq(cho_nt, f"<{MOCHO_NS}sector>", f"<{sector}>", graph_iri))
        _track(MOCHO_NS + "sector")

    # ── Build per-record indexes ───────────────────────────────────────────────
    agents_index: dict[str, AgentDict] = {}
    for agent in coerce_list(rdf.get("Agent")):
        if not isinstance(agent, dict):
            continue
        about = (agent.get("about") or "").strip()
        if about:
            agents_index[mint_bare_id("Agent", about)] = agent
        for pl in coerce_list(agent.get("prefLabel")):
            t = (pl.get("$") or "").strip() if isinstance(pl, dict) else ""
            if t:
                agents_index.setdefault(t, agent)

    event_participant_index: dict[str, str] = {}
    for event in coerce_list(rdf.get("Event")):
        if not isinstance(event, dict):
            continue
        ht = event.get("hasType") or {}
        if isinstance(ht, list):
            ht = ht[0] if ht else {}
        lido_type_uri = (ht.get("resource") or "").strip() if isinstance(ht, dict) else ""
        for p in coerce_list(event.get("P11_had_participant")):
            puri = (p.get("resource") or "").strip() if isinstance(p, dict) else ""
            if puri and lido_type_uri:
                event_participant_index[puri] = lido_type_uri

    concepts_index: dict[str, dict] = {}
    for concept in coerce_list(rdf.get("Concept")):
        if not isinstance(concept, dict):
            continue
        about = (concept.get("about") or "").strip()
        if about:
            concepts_index[about] = concept

    places_index: dict[str, dict] = {}
    for place in coerce_list(rdf.get("Place")):
        if not isinstance(place, dict):
            continue
        about = (place.get("about") or "").strip()
        if about:
            places_index[about.split()[0]] = place

    # ── dc:title — dual-emit (props-mapping D4) ───────────────────────────────
    dc_title_iri = "http://purl.org/dc/elements/1.1/title"
    title_prop   = class_prop_align.get((target_class, dc_title_iri), "")
    for obj_nt in value_to_nt_obj(cho.get("title"), sani_ctr, lang_coll):
        lines.append(make_nq(cho_nt, f"<{dc_title_iri}>", obj_nt, graph_iri))
        _track(dc_title_iri)
        if title_prop and title_prop != dc_title_iri:
            lines.append(make_nq(cho_nt, f"<{title_prop}>", obj_nt, graph_iri))
            _track(title_prop)

    # ── Generic property loop ─────────────────────────────────────────────────
    dc_date_iri   = "http://purl.org/dc/elements/1.1/date"
    dcterms_iss   = "http://purl.org/dc/terms/issued"
    dcterms_ipart = "http://purl.org/dc/terms/isPartOf"
    _special_keys = frozenset({"creator", "contributor", "title"}) | SUBJECT_KEYS | _MOCHO_SKIP

    subject_vals: list = []
    for skey in SUBJECT_KEYS:
        subject_vals.extend(coerce_list(cho.get(skey)))
    hastype_vals: list = coerce_list(cho.get("hasType"))

    for prop, val in cho.items():
        if prop in _special_keys:
            continue
        prop_iri = _DDBEDM_PROP.get(prop)
        if not prop_iri:
            continue
        target_prop = class_prop_align.get((target_class, prop_iri), prop_iri)
        if not target_prop:
            continue

        if prop_iri in (dc_date_iri, dcterms_iss):
            # Date normalisation (D15 / props-mapping §3.1)
            for v in coerce_list(val):
                raw = (v if isinstance(v, str) else
                       (v.get("$") or "") if isinstance(v, dict) else "").strip()
                for normed in normalize_date(raw):
                    if normed:
                        lines.append(make_nq(cho_nt, f"<{target_prop}>",
                                             f'"{_escape_literal(normed)}"', graph_iri))
                        _track(target_prop)
            continue

        if prop_iri == dcterms_ipart:
            # isPartOf URI sanitisation (props-mapping §3.1)
            for obj_nt in value_to_nt_obj(val, sani_ctr):
                if not obj_nt.startswith("<"):
                    continue  # literal isPartOf skipped in mocho graph
                uri = obj_nt[1:-1]
                if not uri.startswith("http"):
                    if len(uri) == 32:
                        uri = DDB_ITEM_BASE + uri
                    else:
                        continue
                lines.append(make_nq(cho_nt, f"<{target_prop}>", f"<{uri}>", graph_iri))
                _track(target_prop)
            continue

        for obj_nt in value_to_nt_obj(val, sani_ctr, lang_coll):
            lines.append(make_nq(cho_nt, f"<{target_prop}>",
                                 expand_obj_nt(obj_nt, bare_id_to_uri), graph_iri))
            _track(target_prop)

    # ── Special handlers ──────────────────────────────────────────────────────
    _creator_lines = emit_creator_triples(
        cho_nt, cho.get("creator"), agents_index, target_class, class_prop_align, graph_iri,
        bare_id_to_uri,
    )
    lines.extend(_creator_lines)
    _track_nqlist(_creator_lines)

    _contrib_lines = emit_contributor_triples(
        cho_nt, cho.get("contributor"),
        event_participant_index, lido_dispatch, target_class, wemi, graph_iri,
        bare_id_to_uri, agents_index,
    )
    lines.extend(_contrib_lines)
    _track_nqlist(_contrib_lines)

    if subject_vals:
        _subject_lines = emit_subject_triples(cho_nt, subject_vals, concepts_index, graph_iri,
                                              bare_id_to_uri)
        lines.extend(_subject_lines)
        _track_nqlist(_subject_lines)

    if hastype_vals:
        _hastype_lines = emit_hastype_triples(cho_nt, hastype_vals, concepts_index, graph_iri,
                                              bare_id_to_uri)
        lines.extend(_hastype_lines)
        _track_nqlist(_hastype_lines)

    currentloc_vals = coerce_list(cho.get("currentLocation"))
    if currentloc_vals:
        _curloc_lines = emit_current_location_triples(
            cho_nt, currentloc_vals, places_index, graph_iri, bare_id_to_uri,
        )
        lines.extend(_curloc_lines)
        _track_nqlist(_curloc_lines)

    # ── Aggregation & Place ───────────────────────────────────────────────────
    agg = rdf.get("Aggregation") or {}
    if isinstance(agg, list):
        agg = agg[0] if agg else {}
    _agg_lines = emit_aggregation_triples(agg, cho_nt, graph_iri)
    lines.extend(_agg_lines)
    _track_nqlist(_agg_lines)

    _place_lines = emit_place_stubs(rdf.get("Place"), graph_iri)
    lines.extend(_place_lines)
    _track_nqlist(_place_lines)

    dispatch_flags["preds_all"]     = preds_all
    dispatch_flags["preds_new"]     = preds_new
    dispatch_flags["uri_sanitized"] = sani_ctr["uri_sanitized"]
    dispatch_flags["uri_split"]     = sani_ctr["uri_split"]
    return lines, target_class, wemi, dispatch_flags
