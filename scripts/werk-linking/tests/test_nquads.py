"""Tests for nquads.py."""

from __future__ import annotations

from pathlib import Path

import nquads
import pytest


def test_literal_escapes_quotes_backslashes_and_newlines() -> None:
    term = nquads.literal('a "b" \\ c\nd')

    assert term == '"a \\"b\\" \\\\ c\\nd"'


def test_literal_with_datatype_and_with_language() -> None:
    assert nquads.integer(3) == '"3"^^<http://www.w3.org/2001/XMLSchema#integer>'
    assert nquads.decimal(0.85) == '"0.85"^^<http://www.w3.org/2001/XMLSchema#decimal>'
    assert nquads.literal("x", lang="en") == '"x"@en'


@pytest.mark.parametrize("value", ["", "http://x/a b", "http://x/<a>", 'http://x/"a"', "http://x/a\n"])
def test_iri_with_forbidden_character_raises(value: str) -> None:
    with pytest.raises(ValueError):
        nquads.iri(value)


def test_quad_file_writes_lines_counts_and_renames(tmp_path: Path) -> None:
    path = tmp_path / "out.nq"

    with nquads.QuadFile(path) as out:
        out.write(("<urn:s>", "<urn:p>", '"o"'), "<urn:g>")

    assert path.read_text(encoding="utf-8") == '<urn:s> <urn:p> "o" <urn:g> .\n'
    assert out.count == 1
    assert not (tmp_path / "out.nq.tmp").exists()


def test_quad_file_error_leaves_no_file(tmp_path: Path) -> None:
    path = tmp_path / "out.nq"

    with pytest.raises(RuntimeError), nquads.QuadFile(path) as out:
        out.write(("<urn:s>", "<urn:p>", '"o"'), "<urn:g>")
        raise RuntimeError("stop")

    assert list(tmp_path.iterdir()) == []
