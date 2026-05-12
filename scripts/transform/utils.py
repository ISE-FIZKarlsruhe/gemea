"""Utility functions: N-Quads formatting, URI minting, value normalisation."""

from __future__ import annotations

import re
from collections import Counter
from functools import lru_cache
from pathlib import Path

import langcodes

from .constants import (
    AgentDict, NQuad, NQList,
    GEMEA_BASE, DDB_ITEM_BASE,
    _PREFIXES, _MEDIATYPE_PREFIX, _SECTOR_PREFIX,
)

# Characters forbidden inside N-Triples IRI references (RFC 3987 + NT spec)
_IRI_UNSAFE_RE = re.compile(r'[\x00-\x20<>"{}|\\^`\x7f]')
_BR_RE         = re.compile(r'<br\s*/?\s*>', re.IGNORECASE)


def _sanitize_iri(iri: str) -> str:
    """Percent-encode characters illegal inside NT IRI references."""
    return _IRI_UNSAFE_RE.sub(lambda m: f"%{ord(m.group()):02X}", iri)


def make_nq(s_nt: str, p_nt: str, o_nt: str, graph_iri: str) -> NQuad:
    """Return one N-Quads line."""
    return f"{s_nt} {p_nt} {o_nt} <{graph_iri}> ."


def coerce_list(val: object) -> list:
    """Normalise None/dict/list to a list."""
    if val is None:
        return []
    if isinstance(val, dict):
        return [val]
    if isinstance(val, list):
        return val
    return []


def _expand_prefix(curie: str) -> str:
    """Expand a CURIE (e.g. 'rdam:P30134') to a full IRI."""
    for prefix, base in _PREFIXES.items():
        if curie.startswith(prefix + ":"):
            return base + curie[len(prefix) + 1:]
    return curie


def _to_curie(iri: str) -> str:
    """Collapse a full IRI to a CURIE using _PREFIXES; return IRI unchanged if no match."""
    for prefix, base in _PREFIXES.items():
        if iri.startswith(base):
            return f"{prefix}:{iri[len(base):]}"
    return iri


def mint_cho_uri(obj_id: str) -> str:
    """Return the minted GeMeA mocho CHO URI for a 32-char DDB object ID (D22)."""
    return GEMEA_BASE + obj_id


def mint_bare_id(entity_class: str, raw_id: str) -> str:
    """Expand a bare 32-char ID to a full URI (D27).

    ProvidedCHO bare IDs → DDB item URI.
    All other entity types → urn:ddbedm:<ClassName>:<id>.
    Full URIs and URNs are returned unchanged.
    """
    if raw_id.startswith(("http", "urn")):
        return raw_id
    if entity_class == "ProvidedCHO":
        return DDB_ITEM_BASE + raw_id
    return f"urn:ddbedm:{entity_class}:{raw_id}"


def _escape_literal(s: str) -> str:
    """Escape characters illegal in N-Triples/N-Quads literal content."""
    s = _BR_RE.sub('\n', s)
    return (s.replace("\\", "\\\\")
             .replace('"', '\\"')
             .replace("\n", "\\n")
             .replace("\r", "\\r")
             .replace("\t", "\\t"))


def _build_iana_collection_codes() -> frozenset[str]:
    """Return all IANA language subtags with Scope: collection, read from the
    registry bundled with langcodes. Falls back to empty set on any error."""
    try:
        registry = (Path(langcodes.__file__).parent / "data" / "language-subtag-registry.txt").read_text()
    except Exception:
        return frozenset()
    codes: set[str] = set()
    in_language_block = is_collection = False
    subtag = ""
    for line in registry.splitlines():
        if line == "%%":
            if in_language_block and is_collection and subtag:
                codes.add(subtag)
            in_language_block = is_collection = False
            subtag = ""
        elif line == "Type: language":
            in_language_block = True
        elif line.startswith("Subtag: "):
            subtag = line[8:].strip()
        elif line == "Scope: collection":
            is_collection = True
    return frozenset(codes)


_IANA_COLLECTION_CODES: frozenset[str] = _build_iana_collection_codes()


@lru_cache(maxsize=512)
def _invalid_bcp47(lang: str) -> bool:
    """True if lang should be normalized to 'und': invalid BCP 47 or IANA collection."""
    try:
        return not langcodes.tag_is_valid(lang) or lang in _IANA_COLLECTION_CODES
    except Exception:
        return True


def value_to_nt_obj(
    val: object,
    sani_ctr: Counter | None = None,
    lang_coll: set[str] | None = None,
) -> list[str]:
    """Convert a JSONL field value to a list of N-Triples object strings.

    Handles all value shapes produced by the DDB EDM JSONL:
      None / ""                    → []
      str (non-empty)              → ['"escaped"']
      list                         → recurse and flatten
      {"resource": IRI}            → ["<IRI>"] (IRI percent-encoded if unsafe chars present)
      {"lang": L, "$": T}          → ['"T"@L']
      {"lang": null, "$": T}       → ['"T"']
      {"resource": null, "$": ""}  → []

    sani_ctr: if provided, incremented once per IRI that required sanitisation.
    lang_coll: if provided, receives original collective lang codes that were normalized.
    """
    if val is None:
        return []
    if isinstance(val, str):
        return [f'"{_escape_literal(val)}"'] if val else []
    if isinstance(val, list):
        result = []
        for item in val:
            result.extend(value_to_nt_obj(item, sani_ctr, lang_coll))
        return result
    if isinstance(val, dict):
        resource = val.get("resource")
        if resource:
            parts = resource.split()  # split handles multi-URI values (DDB data quality issue)
            result = []
            for uri in parts:
                sanitized = _sanitize_iri(uri)
                if sani_ctr is not None and sanitized != uri:
                    sani_ctr["uri_sanitized"] += 1
                result.append(f"<{sanitized}>")
            if sani_ctr is not None and len(parts) > 1:
                sani_ctr["uri_split"] += len(parts)
            return result
        text = val.get("$", "")
        if not text:
            return []
        escaped = _escape_literal(str(text))
        lang = val.get("lang")
        if lang:
            lang = str(lang).strip()
        if lang and _invalid_bcp47(lang):
            if lang_coll is not None and " " not in lang:
                lang_coll.add(lang)
            lang = "und"
        if lang:
            return [f'"{escaped}"@{lang}']
        return [f'"{escaped}"']
    return []


def normalize_date(s: str) -> list[str]:
    """Normalise a dc:date string to ISO 8601.

    8-digit compact YYYYMMDD → YYYY-MM-DD.
    ISO interval begin/end → [begin, end].
    All other values returned unchanged.
    """
    s = s.strip()
    if "/" in s:
        parts = s.split("/", 1)
        return [normalize_date(p)[0] for p in parts]
    if len(s) == 8 and s.isdigit():
        return [f"{s[:4]}-{s[4:6]}-{s[6:]}"]
    return [s]


def build_bare_id_index(rdf: dict) -> dict[str, str]:
    """Map bare about IDs → expanded URIs for every entity in the record (D27)."""
    index: dict[str, str] = {}
    for entity_type, entities in rdf.items():
        for entity in coerce_list(entities):
            if not isinstance(entity, dict):
                continue
            raw_about = (entity.get("about") or "").strip()
            for part in raw_about.split():
                if part and not part.startswith(("http", "urn")):
                    index[part] = mint_bare_id(entity_type, _sanitize_iri(part))
    return index


def resource_uris(
    resource_raw: str,
    bare_id_to_uri: dict[str, str] | None = None,
    entity_class: str = "Agent",
) -> list[str]:
    """Expand, sanitize, and split all URIs from a (possibly multi-value) resource string.

    Steps: (1) split on whitespace; (2) expand bare IDs via index or mint_bare_id fallback;
    (3) percent-encode unsafe characters. Returns [] for empty input.
    """
    if not resource_raw:
        return []
    _bare = bare_id_to_uri or {}
    result = []
    for uri in resource_raw.split():
        if not uri.startswith(("http", "urn")):
            uri = _bare.get(uri) or mint_bare_id(entity_class, uri)
        result.append(_sanitize_iri(uri))
    return result


def expand_obj_nt(obj_nt: str, bare_id_to_uri: dict[str, str]) -> str:
    """Resolve a bare-ID IRI object <ID> via the index; return unchanged otherwise."""
    if obj_nt.startswith("<") and obj_nt.endswith(">"):
        inner = obj_nt[1:-1]
        if not inner.startswith(("http", "urn")):
            resolved = bare_id_to_uri.get(inner)
            if resolved:
                return f"<{resolved}>"
    return obj_nt


def is_ddb_or_gnd(uri: str) -> bool:
    """True if URI is a DDB organization or GND authority URI."""
    return uri.startswith((
        "http://www.deutsche-digitale-bibliothek.de/organization/",
        "http://d-nb.info/gnd/",
        "https://d-nb.info/gnd/",
    ))


def resolve_agent(
    label: str,
    resource: str,
    agents_index: dict[str, AgentDict],
) -> AgentDict | None:
    """Resolve a creator/contributor to an Agent dict.

    URI match preferred; label match fallback.
    Returns None if no match found.
    """
    if resource and resource in agents_index:
        return agents_index[resource]
    if label and label in agents_index:
        return agents_index[label]
    return None


def _extract_mediatype_sector(concepts: object) -> tuple[str, str]:
    """Return (mediatype_iri, sector_iri) from the record's Concept list."""
    mediatype = "any"
    sector    = "any"
    for c in coerce_list(concepts):
        if not isinstance(c, dict):
            continue
        about = c.get("about") or ""
        if about.startswith(_MEDIATYPE_PREFIX):
            mediatype = about
        elif about.startswith(_SECTOR_PREFIX):
            sector = about
    return mediatype, sector


def get_object_id(record: dict) -> str | None:
    """Extract 32-char object ID from ProvidedCHO.about URI. Returns None on failure."""
    try:
        about = record["edm"]["RDF"]["ProvidedCHO"]["about"]
    except (KeyError, TypeError):
        return None
    if not about:
        return None
    # Bare 32-char ID (D27)
    if len(about) == 32 and not about.startswith("http"):
        return about
    obj_id = about.rstrip("/").rsplit("/", 1)[-1]
    return obj_id if len(obj_id) == 32 else None
