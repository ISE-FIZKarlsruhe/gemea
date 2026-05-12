# Purpose:  Generate old-config/video_type2class.json from the dc:type frequency
#           table for mt005 (Video). Class assignments use EBUCore Plus vocabulary
#           (confirmed in mocho-full.owl). Format mirrors audio_type2class.json:
#           positional class array [W, E, M, I]; CLASS_MAP in gen_dctype_class_mapping.py
#           resolves prefixed names to full IRIs.
# Usage:    python gen_video_type2class.py
# Inputs:   output/dctype_frequency_all.csv
# Outputs:  scripts/old-config/video_type2class.json
# Deps:     stdlib only (csv, json, pathlib)
# Assumes:  EBUCore Plus IRI base: http://www.ebu.ch/metadata/ontologies/ebucoreplus#
#           ebucoreplus:EditorialWork  — editorial/programme content (film, interview, etc.)
#           ebucoreplus:MediaResource  — generic media carrier (clip, trailer, fragment)

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FREQ_CSV = ROOT / "output" / "dctype_frequency_all.csv"
OUTPUT = ROOT / "output" / "config" / "video_type2class.json"

# dc:type values that map to EditorialWork (programme/editorial content).
# Promotional clips (Trailer, Teaser) and historical formats (Tonbild) are
# treated as standalone editorial works in this corpus.
EDITORIAL_WORK_TYPES = {
    "Dokumentarfilm",
    "Inszenierung",
    "Interview",
    "Teaser",
    "Tonbild",
    "Trailer",
    "Veranstaltungsmitschnitt",
    "Videofilm",
}

# English translations
EN_LABELS = {
    "Dokumentarfilm":                   "Documentary film",
    "Filmanfang":                       "Film opening",
    "Inszenierung":                     "Stage production (video recording)",
    "Interview":                        "Interview",
    "Teaser":                           "Teaser",
    "Tonbild":                          "Sound film / slide show",
    "Trailer":                          "Trailer",
    "Veranstaltungsmitschnitt":         "Event recording",
    "Videofilm":                        "Video film",
    "zweidimensionales bewegtes Bild":  "Two-dimensional moving image",
}

REMARKS = {
    "Dokumentarfilm":                   "Documentary; editorial content.",
    "Filmanfang":                       "Fragment — opening sequence of a film.",
    "Inszenierung":                     "Video recording of a theatrical or musical production.",
    "Interview":                        "Recorded interview; archival sector.",
    "Teaser":                           "Short promotional clip.",
    "Tonbild":                          "Historical audiovisual format (slide show with audio).",
    "Trailer":                          "Promotional trailer; museum sector.",
    "Veranstaltungsmitschnitt":         "Recording of a live event.",
    "Videofilm":                        "General video film.",
    "zweidimensionales bewegtes Bild":  "Generic moving image; library sector.",
}


def main():
    # Collect mt005 rows grouped by dc_type
    seen = {}
    with FREQ_CSV.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if "mt005" not in row["mediatype"]:
                continue
            dc = row["dc_type_de"].strip()
            if not dc:
                continue
            seen.setdefault(dc, {"count": 0, "sectors": set()})
            seen[dc]["count"] += int(row["count"])
            seen[dc]["sectors"].add(row["sector"].rsplit("/", 1)[-1])

    result = {}
    for dc in sorted(seen, key=lambda x: -seen[x]["count"]):
        rdf_type_m = (
            "ebucoreplus:EditorialWork"
            if dc in EDITORIAL_WORK_TYPES
            else "ebucoreplus:MediaResource"
        )
        result[dc] = {
            "remarks": REMARKS.get(dc, ""),
            "en": EN_LABELS.get(dc, ""),
            "sectors": sorted(seen[dc]["sectors"]),
            "count": seen[dc]["count"],
            "class": [
                "",               # W — Work level (not mapped for video)
                "",               # E — Expression level
                rdf_type_m,       # M — Manifestation level (primary)
                "",               # I — Item level
            ],
        }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=4), encoding="utf-8")

    print(f"Wrote {len(result)} entries → {OUTPUT}")
    print()
    print(f"{'dc:type':<45} {'M class':<30} {'count':>6}  sectors")
    print("-" * 100)
    for dc, entry in result.items():
        secs = ", ".join(entry["sectors"])
        print(f"  {dc:<43} {entry['class'][2]:<30} {entry['count']:>6}  {secs}")


if __name__ == "__main__":
    main()
