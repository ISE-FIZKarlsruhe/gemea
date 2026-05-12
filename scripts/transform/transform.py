"""Core transform: orchestrates per-record triple emission."""

from __future__ import annotations

from collections import Counter

from .constants import (
    NQList, PropAlign,
    MT007_IRI, GRAPH_DDBEDM, GRAPH_MOCHO, GRAPH_PROV, GRAPH_LANG_TITLE,
)
from .utils import get_object_id, make_nq, mint_bare_id, mint_cho_uri, _extract_mediatype_sector, coerce_list
from .emitters import emit_ddbedm_triples, emit_prov_triples, emit_mocho_triples, werk_staging_row

_DCTERMS_LANGUAGE = "http://purl.org/dc/terms/language"
_ISO639_2_BASE    = "http://id.loc.gov/vocabulary/iso639-2/"


def transform_record(
    record: dict,
    ids_set: set[str] | None,
    mediatype_class_map: dict,
    htype_map: dict,
    audio_type2class: dict,
    class_prop_align: PropAlign,
    lido_dispatch: dict,
) -> tuple[dict[str, NQList], dict | None, dict, dict]:
    """Transform one JSONL record into per-graph N-Quads lists (§7.1).

    Returns (streams, werk_row, dispatch_info, pred_info).
    streams is empty dict when record is filtered by IDs.
    dispatch_info: {"target_class", "wemi", "htype_used", "fallback", "is_mt007", "mediatype"}
    pred_info: {"ddbedm_classes", "ddbedm_preds", "mocho_preds_all", "mocho_preds_new"} — Counters
    """
    obj_id = get_object_id(record)
    if obj_id is None:
        raise ValueError("Cannot extract object ID from record")

    if ids_set is not None and obj_id not in ids_set:
        return {}, None, {}, {}

    rdf = record["edm"]["RDF"]
    cho: dict = rdf.get("ProvidedCHO") or {}
    if isinstance(cho, list):
        cho = cho[0] if cho else {}

    about_str = (cho.get("about") or "").strip()
    ddb_uri   = mint_bare_id("ProvidedCHO", (about_str.split()[0] if about_str else obj_id))
    cho_uri = mint_cho_uri(obj_id)

    mediatype, sector = _extract_mediatype_sector(rdf.get("Concept"))
    is_mt007 = (mediatype == MT007_IRI)

    streams: dict[str, NQList] = {}
    lang_coll: set[str] = set()

    # Streams [1] and [4] always run, including mt007 (faithfulness + audit trail)
    ddbedm_lines, ddbedm_classes, ddbedm_preds, ddbedm_sani = emit_ddbedm_triples(
        rdf, GRAPH_DDBEDM, lang_coll,
    )
    streams["ddbedm"] = ddbedm_lines
    streams["prov"]   = emit_prov_triples(record, ddb_uri, GRAPH_PROV)

    # Stream [2] mocho and [3] werk: skip mt007 (D15)
    werk_row: dict | None = None
    dispatch_info: dict = {"target_class": "", "wemi": "", "htype_used": False,
                           "fallback": False, "is_mt007": is_mt007, "mediatype": mediatype}
    if not is_mt007:
        mocho_lines, target_class, wemi, dflags = emit_mocho_triples(
            rdf, cho_uri, ddb_uri, sector, mediatype,
            mediatype_class_map, htype_map, audio_type2class,
            class_prop_align, lido_dispatch, GRAPH_MOCHO,
            lang_coll=lang_coll,
        )
        streams["mocho"] = mocho_lines
        werk_row = werk_staging_row(cho_uri, cho, target_class)
        mocho_preds_all  = dflags.pop("preds_all",      Counter())
        mocho_preds_new  = dflags.pop("preds_new",      Counter())
        mocho_uri_sani   = dflags.pop("uri_sanitized",  0)
        mocho_uri_split  = dflags.pop("uri_split",      0)
        dispatch_info.update({"target_class": target_class, "wemi": wemi, **dflags})
    else:
        mocho_preds_all = Counter()
        mocho_preds_new = Counter()
        mocho_uri_sani  = 0
        mocho_uri_split = 0

    if lang_coll:
        ddb_nt = f"<{ddb_uri}>"
        lang_title_lines: NQList = []
        for orig_lang in sorted(lang_coll):
            lang_title_lines.append(make_nq(
                ddb_nt,
                f"<{_DCTERMS_LANGUAGE}>",
                f"<{_ISO639_2_BASE}{orig_lang}>",
                GRAPH_LANG_TITLE,
            ))
        streams[GRAPH_LANG_TITLE] = lang_title_lines

    pred_info: dict = {
        "ddbedm_classes":   ddbedm_classes,
        "ddbedm_preds":     ddbedm_preds,
        "mocho_preds_all":  mocho_preds_all,
        "mocho_preds_new":  mocho_preds_new,
        "uri_sanitized":    ddbedm_sani["uri_sanitized"]  + mocho_uri_sani,
        "uri_split":        ddbedm_sani["uri_split"]      + mocho_uri_split,
        "uri_about_split":  ddbedm_sani["uri_about_split"],
    }
    return streams, werk_row, dispatch_info, pred_info
