"""
Purpose:    Measure what fraction of dc:type values in the corpus have a corresponding
            GND URI via edm.RDF.Concept, and export the dc_type_de → GND URI mapping
            for use as the dnb_uri column in lookup_dctype_to_class.csv.

            Lookup: ProvidedCHO.dcType value → search Concept[] for case-insensitive
            prefLabel match → Concept.about (GND URI if d-nb.info domain).

Usage:      python count_dctype_gnd_coverage.py

Inputs:     data/items-all-goethe-faust.json (JSONL, one record per line)

Outputs:    output/dctype_gnd_coverage.csv
                Columns: mediatype, sector, dc_type_de, count, gnd_uri, has_gnd
                One row per (mediatype, sector, dc_type_de) triple.

            output/dctype_to_gnd_uri.csv
                Columns: dc_type_de, gnd_uri
                Deduplicated dc_type_de → GND URI mapping (for dnb_uri column).

Deps:       stdlib only (json, csv, pathlib, collections)

Assumes:    - dcType.$  holds the German dc:type string (may be a list of such dicts)
            - Concept.prefLabel.$  holds the label string
            - Concept.about  is the URI (GND if d-nb.info domain)
            - Mediatype and sector Concepts have vocnet.org/medientyp or /sparte in about
"""

import csv
import json
import pathlib
import collections
import sys

# --- Paths -------------------------------------------------------------------

BASE = pathlib.Path(__file__).parent.parent
INPUT = BASE / "data" / "items-all-goethe-faust.json"
OUT_COVERAGE = BASE / "output" / "dctype_gnd_coverage.csv"
OUT_MAPPING = BASE / "output" / "dctype_to_gnd_uri.csv"

# --- Vocabulary IRI prefixes -------------------------------------------------

MEDIATYPE_PREFIX = "http://ddb.vocnet.org/medientyp/"
SECTOR_PREFIX = "http://ddb.vocnet.org/sparte/"
GND_PREFIX = "https://d-nb.info/gnd/"
GND_PREFIX_HTTP = "http://d-nb.info/gnd/"
GETTY_AAT_PREFIX = "http://vocab.getty.edu/aat/"
GETTY_AAT_PREFIX_HTTPS = "https://vocab.getty.edu/aat/"

# Other known-vocab prefixes that we skip (internal DDB / Filmportal vocnets)
SKIP_PREFIXES = (
    "http://ddb.vocnet.org/hierarchietyp/",
    "http://filmportal.vocnet.org/",
)


# --- Helpers -----------------------------------------------------------------

def get_text(value: object) -> str:
    """Extract string from a DDB-EDM text node (dict with '$' key) or plain string."""
    if isinstance(value, dict):
        return (value.get("$") or "").strip()
    if isinstance(value, str):
        return value.strip()
    return ""


def as_list(value: object) -> list:
    """Normalise a value that may be a single item or a list."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def is_gnd(uri: str) -> bool:
    return uri.startswith(GND_PREFIX) or uri.startswith(GND_PREFIX_HTTP)


def is_getty(uri: str) -> bool:
    return uri.startswith(GETTY_AAT_PREFIX) or uri.startswith(GETTY_AAT_PREFIX_HTTPS)


def is_known_vocab(uri: str) -> bool:
    """True for GND or Getty AAT URIs (both accepted in the mapping output)."""
    return is_gnd(uri) or is_getty(uri)


def vocab_priority(uri: str) -> int:
    """Higher → preferred. GND=2, Getty=1, other=0."""
    if is_gnd(uri):
        return 2
    if is_getty(uri):
        return 1
    return 0


def extract_mediatype_sector(concepts: list) -> tuple[str, str]:
    """Return (mediatype_iri, sector_iri) from the Concept list."""
    mediatype = "any"
    sector = "any"
    for c in concepts:
        about = c.get("about") or ""
        if about.startswith(MEDIATYPE_PREFIX):
            mediatype = about
        elif about.startswith(SECTOR_PREFIX):
            sector = about
    return mediatype, sector


def build_preflabel_index(concepts: list) -> dict[str, str]:
    """
    Build a case-insensitive prefLabel → vocab URI index.
    Accepts GND (preferred) and Getty AAT URIs; skips vocnet and unknown/internal URIs.
    When multiple Concepts match the same label, the higher-priority vocab wins
    (GND > Getty AAT).
    """
    index: dict[str, str] = {}
    index_priority: dict[str, int] = {}
    for c in concepts:
        about = c.get("about") or ""
        if about.startswith(MEDIATYPE_PREFIX) or about.startswith(SECTOR_PREFIX):
            continue
        if any(about.startswith(p) for p in SKIP_PREFIXES):
            continue
        if not is_known_vocab(about):
            continue
        label = get_text(c.get("prefLabel"))
        if not label:
            continue
        key = label.lower()
        pri = vocab_priority(about)
        if pri > index_priority.get(key, -1):
            index[key] = about
            index_priority[key] = pri
    return index


# --- Main --------------------------------------------------------------------

def main() -> None:
    if not INPUT.exists():
        print(f"ERROR: input file not found: {INPUT}", file=sys.stderr)
        sys.exit(1)

    # (mediatype, sector, dc_type_de) → {count, gnd_uri}
    coverage: dict[tuple[str, str, str], dict] = collections.defaultdict(
        lambda: {"count": 0, "gnd_uri": ""}
    )
    # dc_type_de (lower) → gnd_uri (best match)
    global_mapping: dict[str, tuple[str, str]] = {}  # lower → (original, gnd_uri)

    records_processed = 0
    records_with_dctype = 0

    with INPUT.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue

            records_processed += 1
            rdf = rec.get("edm", {}).get("RDF", {})
            concepts = as_list(rdf.get("Concept"))
            cho = rdf.get("ProvidedCHO", {})

            mediatype, sector = extract_mediatype_sector(concepts)
            preflabel_index = build_preflabel_index(concepts)

            dc_types_raw = as_list(cho.get("dcType"))
            if not dc_types_raw:
                continue
            records_with_dctype += 1

            for dc_node in dc_types_raw:
                dc_str = get_text(dc_node)
                if not dc_str:
                    continue

                key = (mediatype, sector, dc_str)
                coverage[key]["count"] += 1

                # Look up vocab URI (GND preferred, Getty AAT accepted)
                vocab_uri = preflabel_index.get(dc_str.lower(), "")
                existing_cov_uri = coverage[key]["gnd_uri"]
                if vocab_uri:
                    if not existing_cov_uri or (
                        vocab_priority(vocab_uri) > vocab_priority(existing_cov_uri)
                    ):
                        coverage[key]["gnd_uri"] = vocab_uri

                # Update global mapping (prefer higher-priority vocab URI)
                dc_lower = dc_str.lower()
                existing_uri = global_mapping.get(dc_lower, ("", ""))[1]
                if vocab_uri:
                    if not existing_uri or (
                        vocab_priority(vocab_uri) > vocab_priority(existing_uri)
                    ):
                        global_mapping[dc_lower] = (dc_str, vocab_uri)
                elif dc_lower not in global_mapping:
                    global_mapping[dc_lower] = (dc_str, "")

    # --- Write coverage CSV --------------------------------------------------

    OUT_COVERAGE.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["mediatype", "sector", "dc_type_de", "count", "gnd_uri", "has_gnd"]
    rows = sorted(
        [
            {
                "mediatype": mt,
                "sector": sec,
                "dc_type_de": dc,
                "count": v["count"],
                "gnd_uri": v["gnd_uri"],
                "has_gnd": "true" if is_known_vocab(v["gnd_uri"]) else "false",
            }
            for (mt, sec, dc), v in coverage.items()
        ],
        key=lambda r: (r["mediatype"], r["sector"], r["dc_type_de"]),
    )
    with OUT_COVERAGE.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    # --- Write mapping CSV ---------------------------------------------------

    mapping_rows = sorted(
        [{"dc_type_de": orig, "gnd_uri": uri} for (orig, uri) in global_mapping.values()],
        key=lambda r: r["dc_type_de"].lower(),
    )
    with OUT_MAPPING.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["dc_type_de", "gnd_uri"])
        writer.writeheader()
        writer.writerows(mapping_rows)

    # --- Print summary -------------------------------------------------------

    total_occurrences = sum(v["count"] for v in coverage.values())
    total_with_gnd = sum(v["count"] for v in coverage.values() if is_known_vocab(v["gnd_uri"]))
    pct_overall = 100 * total_with_gnd / total_occurrences if total_occurrences else 0

    print(f"Records processed : {records_processed:,}")
    print(f"Records with dcType: {records_with_dctype:,}")
    print(f"Total dc:type occurrences: {total_occurrences:,}")
    print(f"With GND URI      : {total_with_gnd:,} ({pct_overall:.1f}%)")
    print()

    # Per mediatype/sector breakdown
    mt_sec: dict[tuple[str, str], dict] = collections.defaultdict(lambda: {"total": 0, "gnd": 0})
    for (mt, sec, _), v in coverage.items():
        mt_sec[(mt, sec)]["total"] += v["count"]
        mt_sec[(mt, sec)]["gnd"] += v["count"] if is_known_vocab(v["gnd_uri"]) else 0

    print(f"{'Mediatype':<45} {'Sector':<45} {'Total':>8} {'GND':>8} {'%':>7}")
    print("-" * 115)
    for (mt, sec), v in sorted(mt_sec.items()):
        pct = 100 * v["gnd"] / v["total"] if v["total"] else 0
        mt_short = mt.split("/")[-1] if "/" in mt else mt
        sec_short = sec.split("/")[-1] if "/" in sec else sec
        print(f"{mt_short:<45} {sec_short:<45} {v['total']:>8,} {v['gnd']:>8,} {pct:>6.1f}%")

    print()
    print(f"Coverage CSV  → {OUT_COVERAGE}")
    print(f"Mapping CSV   → {OUT_MAPPING}")
    unique_with_vocab = sum(1 for _, uri in global_mapping.values() if is_known_vocab(uri))
    unique_gnd = sum(1 for _, uri in global_mapping.values() if is_gnd(uri))
    unique_getty = sum(1 for _, uri in global_mapping.values() if is_getty(uri))
    print(f"Unique dc:type values: {len(global_mapping):,} total, {unique_with_vocab:,} with vocab URI "
          f"({unique_gnd:,} GND, {unique_getty:,} Getty AAT)")


if __name__ == "__main__":
    main()
