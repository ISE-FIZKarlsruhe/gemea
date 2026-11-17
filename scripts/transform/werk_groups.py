"""Werk-linking type groups, used to select the records written to werk_staging (D30).

Copied from the linker on gemea link-gnd-local (scripts/py/count_werk_linking_types.py HTYPE_GROUPS,
GROUP_ORDER, htype_group; scripts/py/count_werk_type_pairs.py MUSIC_DC_TYPES, SONG_DC_TYPES, type_group;
scripts/py/werk_link_rules.py GROUP_CLASSES), so the transform stages the records the linker links.
Change both places together.

One difference: the linker reads one dc:type string per record (April s2 Parquet); a record here can
carry several dc:type literals, and a group by dc:type applies if any of them matches.
"""

from __future__ import annotations

HTYPE_GROUPS: dict[str, frozenset[str]] = {
    "work": frozenset({
        "htype_021", "htype_007", "htype_020", "htype_003", "htype_026",
        "htype_027", "htype_012", "htype_011", "htype_040", "htype_024",
    }),
    "manuscript": frozenset({"htype_013"}),
    "music": frozenset({"htype_022"}),
    "not_linked": frozenset({"htype_014", "htype_006", "htype_008", "htype_015", "htype_019", "htype_023", "htype_038"}),
}
GROUP_ORDER = ("music", "manuscript", "work", "not_linked")
MUSIC_DC_TYPES = frozenset({"Noten", "notated music", "Musiknoten", "Musikdruck", "Musikhandschrift"})
SONG_DC_TYPES = frozenset({"Lied", "Liederbuch", "Liedersammlung", "Kirchenlied", "Musikbuch"})

# The groups the linker links, and the GND classes each one searches.
GROUP_CLASSES: dict[str, tuple[str, ...]] = {
    "music": ("MusicalWork", "VersionOfAMusicalWork"),
    "libretto": ("MusicalWork", "Work"),
    "song": ("MusicalWork", "Work"),
    "manuscript": ("Manuscript", "CollectiveManuscript", "Work"),
    "multivolume": ("Work", "Collection"),
    "work": ("Work",),
}


def htype_group(htype: str) -> str:
    """The group of an htype value, which may hold several space-separated codes."""
    if not htype:
        return "no_htype"
    codes = set(htype.split())
    for group in GROUP_ORDER:
        if codes & HTYPE_GROUPS[group]:
            return group
    return "unassigned"


def type_group(htype: str, dc_types: list[str]) -> str:
    """The type group of one record, in the linker's order: not_linked htypes first, then dc:type, then htype."""
    group = htype_group(htype)
    if group == "not_linked":
        return "not_linked"
    if group == "music" or any(t in MUSIC_DC_TYPES for t in dc_types):
        return "music"
    if any(t.startswith("Libretto") for t in dc_types):
        return "libretto"
    if any(t in SONG_DC_TYPES for t in dc_types):
        return "song"
    if group == "manuscript":
        return "manuscript"
    if "htype_020" in htype.split():
        return "multivolume"
    if group == "work":
        return "work"
    if group == "no_htype":
        return "ignored"
    return "unassigned"
