#!/usr/bin/env python3
# Purpose:  Validate §1.2 dispatch rules (transform-adr.md D1) against the full
#           corpus. For each record, extract the four dispatch signals (sector,
#           mediatype, htype, dc_type) and apply the §1.2 dispatch table to
#           determine (W_class, M_class, dispatch_rule). Output a CSV for
#           spot-checking before implementation in transform_edm_to_mocho.py
#           (Option A).
#
# Usage:    python scripts/validate_dispatch.py
#           python scripts/validate_dispatch.py --input data/items-excerpt-1000.json
#
# Inputs:   data/items-all-goethe-faust.json          — corpus (JSON array or JSONL)
#           output/config/lookup_htype_doco_rico.csv   — htype_code → rdf_type (D3)
#           output/config/audio_type2class.json        — dc_type → audio class (D16)
#
# Outputs:  output/dispatch_validation.csv
#           Columns: item_id, sector, mediatype, htype, dc_type,
#                    W_class, M_class, dispatch_rule
#
# Deps:     stdlib only (json, csv, pathlib, argparse, collections)
#
# Field paths (D3, transform-script-adr.md):
#   sector   — provider-info.domains[0]
#   mediatype — edm.RDF.WebResource[0].type.resource
#   htype    — edm.RDF.ProvidedCHO.hierarchyType
#   dc_type  — edm.RDF.ProvidedCHO.dcType.$

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT   = Path(__file__).resolve().parents[1]
OUTDIR = ROOT / "output"

DEFAULT_INPUT  = ROOT / "data" / "items-all-goethe-faust.json"
DEFAULT_HTYPE  = ROOT / "output" / "config" / "lookup_htype_doco_rico.csv"
DEFAULT_AUDIO  = ROOT / "output" / "config" / "audio_type2class.json"
OUTPUT         = OUTDIR / "dispatch_validation.csv"

SPARTE = "http://ddb.vocnet.org/sparte/"
MT     = "http://ddb.vocnet.org/medientyp/"

# §1.2 htype-first strata: (sector_code, mt_code) → fixed_M_class
# W class comes from htype lookup; VIDEO also has a fixed W default.
HTYPE_FIRST = {
    ("sparte001", "mt001"): "aco:AudioManifestation",
    ("sparte001", "mt002"): "mocho:ImageManifestation",
    ("sparte001", "mt003"): "mocho:Manifestation",
    ("sparte001", "mt005"): "ec:MediaResource",
    ("sparte002", "mt001"): "aco:AudioManifestation",
    ("sparte002", "mt002"): "mocho:ImageManifestation",
    ("sparte002", "mt003"): "mocho:Manifestation",
    ("sparte002", "mt005"): "ec:MediaResource",
    ("sparte004", "mt003"): "mocho:Manifestation",
    ("sparte005", "mt003"): "mocho:Manifestation",
    ("sparte006", "mt003"): "mocho:Manifestation",
    ("sparte007", "mt003"): "mocho:Manifestation",
}

# §1.2 fixed-class strata: (sector_code, mt_code) → (W_class, M_class)
FIXED = {
    ("sparte003", "mt002"): ("mocho:ImmovableWork",  "mocho:ImageManifestation"),
    ("sparte004", "mt002"): ("",                     "mocho:ImageManifestation"),
    ("sparte005", "mt002"): ("mocho:ImageWork",       "mocho:ImageManifestation"),
    ("sparte005", "mt005"): ("ec:EditorialWork",      "ec:MediaResource"),
    ("sparte006", "mt001"): ("",                     "aco:AudioManifestation"),
    ("sparte006", "mt002"): ("vra:Work",              "vra:Image"),
    ("sparte006", "mt005"): ("ec:EditorialWork",      "ec:MediaResource"),
    ("sparte007", "mt002"): ("",                     "mocho:ImageManifestation"),
}

# §1.2 audio-config strata: dc_type lookup via audio_type2class.json
AUDIO_CONFIG_SECTORS = {"sparte004", "sparte005"}


def load_htype_map(path: Path) -> dict:
    """htype_code → rdf_type string; pending rows excluded."""
    result = {}
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            code = row["htype_code"].strip()
            rdf  = row["rdf_type"].strip()
            if rdf and rdf != "pending":
                result[code] = rdf
    return result


def load_audio_config(path: Path) -> dict:
    """dc_type_de → {class: [W, E, M, I], group, ...}; skips 'Muster' template."""
    with path.open(encoding="utf-8") as fh:
        raw = json.load(fh)
    return {k: v for k, v in raw.items() if k != "Muster"}


def extract_signals(record: dict) -> tuple:
    """Return (item_id, sector_code, mt_code, htype, dc_type) from one record."""
    item_id = record.get("properties", {}).get("item-id", "")

    # sector: provider-info.domains[0] (D3)
    domains = record.get("provider-info", {}).get("domains", [])
    sector_iri = domains[0] if domains else ""
    sector = sector_iri.replace(SPARTE, "") if sector_iri.startswith(SPARTE) else ""

    # mediatype: edm.RDF.WebResource[0].type.resource (D3)
    wrs = record.get("edm", {}).get("RDF", {}).get("WebResource", [])
    mt_iri = ""
    if wrs:
        t = wrs[0].get("type", {})
        if isinstance(t, dict):
            mt_iri = t.get("resource") or ""
    mt = mt_iri.replace(MT, "") if mt_iri.startswith(MT) else ""

    # htype: edm.RDF.ProvidedCHO.hierarchyType (D3)
    cho   = record.get("edm", {}).get("RDF", {}).get("ProvidedCHO", {})
    htype = cho.get("hierarchyType") or ""

    # dc_type: edm.RDF.ProvidedCHO.dcType.$ (D3)
    raw_dc = cho.get("dcType")
    if isinstance(raw_dc, dict):
        dc_type = raw_dc.get("$") or ""
    elif isinstance(raw_dc, list):
        dc_type = " | ".join(
            v.get("$", "") if isinstance(v, dict) else str(v)
            for v in raw_dc
        )
    elif isinstance(raw_dc, str):
        dc_type = raw_dc
    else:
        dc_type = ""

    return item_id, sector, mt, htype, dc_type.strip()


def dispatch(sector: str, mt: str, htype: str, dc_type: str,
             htype_map: dict, audio_cfg: dict):
    """
    Apply §1.2 dispatch rules (transform-adr.md D1).
    Returns (W_class, M_class, dispatch_rule) or None if record is skipped (mt007).
    """
    # mt007: skip — no mocho triples (D15)
    if mt == "mt007":
        return None

    # sparte002 no-mediatype: library sector → rdac:Manifestation
    if sector == "sparte002" and not mt:
        return ("", "rdac:Manifestation", "sparte002 no-mediatype")

    # htype-first strata
    if (sector, mt) in HTYPE_FIRST:
        fixed_m = HTYPE_FIRST[(sector, mt)]
        w = htype_map.get(htype, "")
        # VIDEO: fixed W default when htype absent
        if mt == "mt005" and not w:
            w = "ec:EditorialWork"
        rule = f"{sector}×{mt} htype-first"
        return (w, fixed_m, rule)

    # fixed-class strata
    if (sector, mt) in FIXED:
        w, m = FIXED[(sector, mt)]
        return (w, m, f"{sector}×{mt} fixed")

    # audio-config strata (sparte004, sparte005 × mt001)
    if mt == "mt001" and sector in AUDIO_CONFIG_SECTORS:
        entry = audio_cfg.get(dc_type) or audio_cfg.get("default", {})
        cls   = entry.get("class", ["", "", "", ""])
        w  = cls[0] if len(cls) > 0 else ""
        m  = cls[2] if len(cls) > 2 and cls[2] else "aco:AudioManifestation"
        return (w, m, f"{sector}×mt001 audio_type2class")

    # fallback (D9)
    return ("", "mocho:Manifestation", "fallback D9")


def iter_records(path: Path):
    """Yield records from JSONL (one JSON object per line) or JSON array."""
    with path.open(encoding="utf-8") as fh:
        first_char = fh.read(1)
        fh.seek(0)
        if first_char == "[":
            for record in json.load(fh):
                yield record
        else:
            for line in fh:
                line = line.strip()
                if line:
                    yield json.loads(line)


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate §1.2 dispatch rules")
    parser.add_argument("--input",  default=DEFAULT_INPUT, type=Path)
    parser.add_argument("--htype",  default=DEFAULT_HTYPE, type=Path)
    parser.add_argument("--audio",  default=DEFAULT_AUDIO, type=Path)
    parser.add_argument("--output", default=OUTPUT,        type=Path)
    args = parser.parse_args()

    print(f"Loading htype map from {args.htype} ...")
    htype_map = load_htype_map(args.htype)
    print(f"  {len(htype_map)} codes mapped")

    print(f"Loading audio config from {args.audio} ...")
    audio_cfg = load_audio_config(args.audio)
    print(f"  {len(audio_cfg)} dc:type entries")

    fields = ["item_id", "sector", "mediatype", "htype", "dc_type",
              "W_class", "M_class", "dispatch_rule"]

    rule_counts: Counter = Counter()
    mt007_by_sector: Counter = Counter()
    skipped = 0
    written = 0

    print(f"Processing {args.input} ...")
    with args.output.open("w", newline="", encoding="utf-8") as out_fh:
        writer = csv.DictWriter(out_fh, fieldnames=fields)
        writer.writeheader()

        for record in iter_records(args.input):
            item_id, sector, mt, htype, dc_type = extract_signals(record)
            result = dispatch(sector, mt, htype, dc_type, htype_map, audio_cfg)

            if result is None:
                skipped += 1
                mt007_by_sector[sector or "unknown"] += 1
                continue

            w, m, rule = result
            writer.writerow({
                "item_id":       item_id,
                "sector":        sector,
                "mediatype":     mt,
                "htype":         htype,
                "dc_type":       dc_type,
                "W_class":       w,
                "M_class":       m,
                "dispatch_rule": rule,
            })
            written += 1
            rule_counts[rule] += 1

    print(f"\nWrote {written} rows → {args.output}")
    print(f"Skipped {skipped} mt007 records\n")

    print(f"{'mt007 skipped by sector':<35} {'count':>7}")
    print("-" * 44)
    for sector, count in sorted(mt007_by_sector.items(), key=lambda x: -x[1]):
        print(f"{sector:<35} {count:>7}")

    print(f"\n{'Dispatch rule':<45} {'count':>7}")
    print("-" * 54)
    for rule, count in sorted(rule_counts.items(), key=lambda x: -x[1]):
        print(f"{rule:<45} {count:>7}")


if __name__ == "__main__":
    main()
