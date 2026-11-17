"""Unit tests for transform.werk_groups, the copy of the Werk-linking type groups."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # adds scripts/ to sys.path

from transform.werk_groups import GROUP_CLASSES, htype_group, type_group


@pytest.mark.parametrize("htype, dc_types, expected", [
    ("htype_021", [], "work"),                       # Monografie
    ("htype_007", [], "work"),                       # Band
    ("htype_003", [], "work"),                       # Beigefügtes Werk
    ("htype_026", [], "work"),                       # Text
    ("htype_020", [], "multivolume"),                # Mehrbändiges Werk
    ("htype_007 htype_020", [], "multivolume"),      # Band of a multivolume work
    ("htype_013", [], "manuscript"),                 # Handschrift
    ("htype_022", [], "music"),                      # Musik
    ("htype_021", ["notated music"], "music"),       # dc:type before htype
    ("", ["Noten"], "music"),
    ("htype_021", ["Libretto: Faust"], "libretto"),
    ("htype_007", ["Liederbuch"], "song"),
    ("htype_006", [], "not_linked"),                 # Aufsatz
    ("htype_023", [], "not_linked"),                 # Fortlaufendes Sammelwerk
    ("htype_015", [], "not_linked"),                 # Illustration
    ("htype_008", ["Noten"], "not_linked"),          # not_linked htype checked first
    ("", [], "ignored"),
    ("", ["Text"], "ignored"),
    ("htype_044", [], "unassigned"),                 # Zeitung: in no D29 list
])
def test_type_group_follows_d29(htype, dc_types, expected):
    assert type_group(htype, dc_types) == expected


def test_htype_group_uses_group_order_over_all_codes():
    assert htype_group("htype_021 htype_022") == "music"


def test_linked_groups_are_the_six_of_d29():
    assert set(GROUP_CLASSES) == {"music", "libretto", "song", "manuscript", "multivolume", "work"}
