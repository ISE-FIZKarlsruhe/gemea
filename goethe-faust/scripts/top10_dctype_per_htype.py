#!/usr/bin/env python3
# Purpose:  For each sector × mediatype stratum where the dominant dispatch
#           signal is dc:type or both, report the top-10 dc:types per htype
#           and check whether each dc:type has a mapping in the general
#           dispatch table (lookup_dctype_to_class.csv) and/or in the
#           mediatype-specific config JSONs.
# Usage:    python scripts/top10_dctype_per_htype.py
# Inputs:   output/dispatch_signal_ratio.csv
#           output/dctype_sparte*.csv
#           output/config/lookup_dctype_to_class.csv
#           output/config/image_type2class.json  (mt002)
#           output/config/video_type2class.json  (mt005)
#           scripts/old-config/audio_type2class.json  (mt001)
# Outputs:  output/top10_dctype_per_htype.csv
#           Columns: sector, sector_label, mediatype, dominant_signal,
#                    htype, dc_type_de, count,
#                    mapped_general, mapped_mt_specific, mapped_any,
#                    rdf_type_w, rdf_type_m, mt_specific_class, mapping_source,
#                    justification
# Notes:    The justification column is hand-annotated (domain-expert layer,
#           ADR D2 in transform-adr.md). Existing values are preserved across
#           reruns — the script merges them back in by (sector, mediatype,
#           htype, dc_type_de) key.
# Deps:     stdlib only (csv, json, pathlib, collections, re)

import csv
import json
import re
from collections import defaultdict
from pathlib import Path

ROOT   = Path(__file__).resolve().parents[1]
OUTDIR = ROOT / "output"
OUTPUT = OUTDIR / "top10_dctype_per_htype.csv"

SECTOR_RE = re.compile(r"dctype_(sparte\d+)\.csv$")

SECTOR_LABELS = {
    "sparte001": "Archive",
    "sparte002": "Library",
    "sparte003": "Monument Preservation",
    "sparte004": "Research",
    "sparte005": "Media Library",
    "sparte006": "Museum",
    "sparte007": "Others",
}

MT_LABELS = {
    "mt001": "AUDIO",
    "mt002": "PHOTO",
    "mt003": "TEXT",
    "mt005": "VIDEO",
    "mt007": "NOT DIGITIZED",
    "any":   "ANY",
}

# Mediatype codes that have specific config JSONs
MT_SPECIFIC = {"mt002", "mt001", "mt005"}


def dominant_signal(row: dict) -> str:
    dctype  = float(row["dctype_only"])
    both    = float(row["both"])
    htype   = float(row["htype_only"])
    if both >= dctype and both > htype:
        return "both"
    if dctype >= both and dctype > htype:
        return "dc:type"
    return "htype"


def load_signal_strata() -> dict[tuple, str]:
    """Return {(sector, mediatype_code): dominant_signal} for dc:type/both strata."""
    path = OUTDIR / "dispatch_signal_ratio.csv"
    strata: dict[tuple, str] = {}
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            mt = row["mediatype"]
            if mt == "NOT DIGITIZED":
                continue  # always mocho:Manifestation — skip
            # reverse-map label to code for joining with dctype_sparte*.csv
            mt_code = next((k for k, v in MT_LABELS.items() if v == mt), mt)
            sig = dominant_signal(row)
            if sig in ("dc:type", "both"):
                strata[(row["sector"], mt_code)] = sig
    return strata


def load_general_lookup() -> dict[tuple, dict]:
    """Return {(mt_iri_or_any, sector_iri_or_any, dc_type_de): row}."""
    path = OUTDIR / "lookup_dctype_to_class.csv"
    index: dict[tuple, dict] = {}
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            key = (row["mediatype"].strip(), row["sector"].strip(), row["dc_type_de"].strip())
            index[key] = row
    return index


def check_general(lookup: dict, mt_code: str, sector: str, dc: str) -> tuple[str, str, str, str]:
    """Three-level fallback. Returns (mapped_yes_no, rdf_type_w, rdf_type_m, source_note)."""
    mt_iri = f"http://ddb.vocnet.org/medientyp/{mt_code}" if mt_code != "any" else "any"
    s_iri  = f"http://ddb.vocnet.org/sparte/{sector}"
    row = (
        lookup.get((mt_iri, s_iri, dc))
        or lookup.get((mt_iri, "any", dc))
        or lookup.get(("any", "any", dc))
    )
    if not row:
        return ("no", "", "", "")
    has_class = bool(row.get("rdf_type_w") or row.get("rdf_type_m"))
    mapped = "yes" if has_class else ("no" if dc else "no-dctype")
    parts = ["lookup_dctype_to_class.csv"]
    if row.get("source_vocab"):
        parts.append(row["source_vocab"].strip())
    if row.get("notes"):
        parts.append(row["notes"].strip())
    source = " | ".join(p for p in parts if p)
    return (mapped, row.get("rdf_type_w", ""), row.get("rdf_type_m", ""), source)


def load_mt_configs() -> dict[str, dict]:
    """Load mediatype-specific config JSONs keyed by mt_code."""
    configs: dict[str, dict] = {}

    img_path = OUTDIR / "config" / "image_type2class.json"
    if img_path.exists():
        configs["mt002"] = json.loads(img_path.read_text(encoding="utf-8"))
        configs["mt002:source"] = "output/config/image_type2class.json"

    vid_path = OUTDIR / "config" / "video_type2class.json"
    if vid_path.exists():
        configs["mt005"] = json.loads(vid_path.read_text(encoding="utf-8"))
        configs["mt005:source"] = "output/config/video_type2class.json"

    aud_path = ROOT / "scripts" / "old-config" / "audio_type2class.json"
    if aud_path.exists():
        raw = json.loads(aud_path.read_text(encoding="utf-8"))
        configs["mt001"] = {k: v for k, v in raw.items() if k not in ("Muster", "default")}
        configs["mt001:source"] = "scripts/old-config/audio_type2class.json"

    return configs


def check_mt_specific(configs: dict, mt_code: str, sector: str, dc: str) -> tuple[str, str, str]:
    """Return (mapped_yes_no, class_string, source_note). Image uses dc__sector key."""
    cfg = configs.get(mt_code)
    if not cfg:
        return ("n/a", "", "")

    if mt_code == "mt002":
        entry = cfg.get(f"{dc}__{sector}") or cfg.get(f"{dc}__any")
    else:
        entry = cfg.get(dc)

    if not entry:
        return ("no", "", "")

    classes = entry.get("class", [])
    non_empty = [c for c in classes if c]
    cls_str = " | ".join(str(c) for c in non_empty) if non_empty else ""

    file_source = configs.get(f"{mt_code}:source", mt_code)
    remark = entry.get("notes") or entry.get("remarks") or ""
    parts = [file_source]
    if remark:
        parts.append(remark.strip())
    source = " | ".join(p for p in parts if p)

    return ("yes" if cls_str else "no", cls_str, source)


def load_existing_justifications() -> dict[tuple, str]:
    """Preserve hand-written justifications from a previous run."""
    if not OUTPUT.exists():
        return {}
    index: dict[tuple, str] = {}
    with OUTPUT.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if "justification" not in row:
                continue
            key = (row["sector"], row["mediatype"], row["htype"], row["dc_type_de"])
            if row["justification"].strip():
                index[key] = row["justification"].strip()
    return index


def main() -> None:
    strata         = load_signal_strata()
    lookup         = load_general_lookup()
    configs        = load_mt_configs()
    justifications = load_existing_justifications()

    # collect rows: (sector, mt_code, htype, dc_type_de) → count
    # only for strata in our filter
    agg: dict[tuple, int] = defaultdict(int)

    for path in sorted(OUTDIR.glob("dctype_sparte*.csv")):
        m = SECTOR_RE.search(path.name)
        if not m:
            continue
        sector = m.group(1)
        with path.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                mt    = row["mediatype"].strip()
                htype = row["htype"].strip()
                dc    = row["dc_type_de"].strip()
                count = int(row["count"] or 0)
                if (sector, mt) not in strata:
                    continue
                agg[(sector, mt, htype, dc)] += count

    # group by (sector, mt, htype), rank, top-10
    groups: dict[tuple, list] = defaultdict(list)
    for (sector, mt, htype, dc), count in agg.items():
        groups[(sector, mt, htype)].append((dc, count))

    out_rows = []
    for (sector, mt, htype) in sorted(groups):
        ranked = sorted(groups[(sector, mt, htype)], key=lambda x: -x[1])[:10]
        sig = strata[(sector, mt)]
        for dc, count in ranked:
            mapped_g, rfw, rfm, src_g = check_general(lookup, mt, sector, dc)
            mapped_s, mt_cls, src_s   = check_mt_specific(configs, mt, sector, dc)
            mapped_any = "yes" if "yes" in (mapped_g, mapped_s) else mapped_g
            sources = "; ".join(s for s in (src_g if mapped_g == "yes" else "",
                                            src_s if mapped_s == "yes" else "") if s)
            mt_label = MT_LABELS.get(mt, mt)
            jkey = (sector, mt_label, htype, dc)
            out_rows.append({
                "sector":             sector,
                "sector_label":       SECTOR_LABELS.get(sector, sector),
                "mediatype":          mt_label,
                "dominant_signal":    sig,
                "htype":              htype,
                "dc_type_de":         dc,
                "count":              count,
                "mapped_general":     mapped_g,
                "mapped_mt_specific": mapped_s,
                "mapped_any":         mapped_any,
                "rdf_type_w":         rfw,
                "rdf_type_m":         rfm,
                "mt_specific_class":  mt_cls,
                "mapping_source":     sources,
                "justification":      justifications.get(jkey, ""),
            })

    fields = [
        "sector", "sector_label", "mediatype", "dominant_signal",
        "htype", "dc_type_de", "count",
        "mapped_general", "mapped_mt_specific", "mapped_any",
        "rdf_type_w", "rdf_type_m", "mt_specific_class", "mapping_source",
        "justification",
    ]
    with OUTPUT.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(out_rows)

    print(f"Wrote {len(out_rows)} rows → {OUTPUT}")

    # summary
    total    = len(out_rows)
    mapped   = sum(1 for r in out_rows if r["mapped_any"] == "yes")
    unmapped = sum(1 for r in out_rows if r["mapped_any"] == "no")
    print(f"  mapped_any=yes : {mapped} ({100*mapped/total:.1f}%)")
    print(f"  mapped_any=no  : {unmapped} ({100*unmapped/total:.1f}%)")


if __name__ == "__main__":
    main()
