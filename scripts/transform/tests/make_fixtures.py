"""
Purpose:    Run the full transform on the three fixture records and write .nq output files
            for human inspection. One <id>.nq file per fixture in tests/fixtures/.
Usage:      python -m transform.tests.make_fixtures
            (run from scripts/ directory)
Inputs:     scripts/transform/tests/fixtures/{multi_uri,br_tag,bare_id}.json
            output/config/  (all standard config files)
Outputs:    scripts/transform/tests/fixtures/{multi_uri,br_tag,bare_id}.nq
Deps:       transform package (stdlib only)
Assumes:    Run from goethe-faust/scripts/
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # adds scripts/ to sys.path

from transform.constants import PROJECT_DIR
from transform.loaders import (
    load_class_prop_alignment, load_lido_event_types,
    load_htype_map, load_mediatype_class, load_audio_type2class,
)
from transform.transform import transform_record

_FIXTURES = Path(__file__).parent / "fixtures"
_CONFIG   = PROJECT_DIR / "output" / "config"

_FIXTURE_NAMES = ["multi_uri", "br_tag", "bare_id"]


def main() -> None:
    class_prop_align    = load_class_prop_alignment(_CONFIG / "lookup_class_prop_alignment.csv")
    lido_dispatch       = load_lido_event_types(_CONFIG / "lido_event_types.csv")
    htype_map           = load_htype_map(_CONFIG / "lookup_htype_doco_rico.csv")
    mediatype_class_map = load_mediatype_class(_CONFIG / "lookup_mediatype_class.csv")
    audio_type2class    = load_audio_type2class(_CONFIG / "audio_type2class.json")

    for name in _FIXTURE_NAMES:
        in_path  = _FIXTURES / f"{name}.json"
        out_path = _FIXTURES / f"{name}.nq"

        with open(in_path, encoding="utf-8") as f:
            record = json.load(f)

        streams, _werk, _dispatch, _pred = transform_record(
            record, None,
            mediatype_class_map, htype_map, audio_type2class,
            class_prop_align, lido_dispatch,
        )

        with open(out_path, "w", encoding="utf-8") as f:
            if streams:
                for graph_lines in streams.values():
                    for nq in graph_lines:
                        f.write(nq + "\n")

        total = sum(len(v) for v in streams.values()) if streams else 0
        print(f"{name}: {total} triples → {out_path.name}")


if __name__ == "__main__":
    main()
