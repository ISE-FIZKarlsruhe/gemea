"""Tests for load_nq_qlever.py, with a fake server instead of docker and QLever."""

from __future__ import annotations

import re
from pathlib import Path

import load_nq_qlever as loader
import pytest

G1 = "<http://x/g1>"
G2 = "<http://x/g2>"
QUADS = [
    f"<http://x/a> <http://x/p> <http://x/b> {G1} .\n",
    f'<http://x/a> <http://x/p> "two words ." {G1} .\n',
    f'<http://x/a> <http://x/p> "c"@en {G1} .\n',
    f"<http://x/d> <http://x/p> <http://x/e> {G2} .\n",
]


class FakeServer:
    """Keeps triples per graph like a store with set semantics, and records every request."""

    def __init__(self) -> None:
        self.graphs: dict[str, set[str]] = {}
        self.requests: list[tuple[str, str]] = []

    def send(self, kind: str, text: str) -> str:
        self.requests.append((kind, text))
        graph = re.search(r"GRAPH (<[^>]+>)", text).group(1)  # type: ignore[union-attr]
        if kind == "query":
            return f"?n\n{len(self.graphs.get(graph, set()))}\n"
        if text.startswith("DROP"):
            self.graphs.pop(graph, None)
        else:
            body = text[text.index("{\n") + 2 : text.rindex(" .\n}")]
            self.graphs.setdefault(graph, set()).update(body.split(" .\n"))
        return "{}"


def write_nq(tmp_path: Path, lines: list[str] = QUADS) -> Path:
    path = tmp_path / "data.nq"
    path.write_text("".join(lines), encoding="utf-8")
    return path


def test_split_quad_keeps_literal_with_spaces_and_dot() -> None:
    triple, graph = loader.split_quad(QUADS[1])

    assert triple == '<http://x/a> <http://x/p> "two words ."'
    assert graph == G1


@pytest.mark.parametrize("line", ["<http://x/a> <http://x/p> <http://x/b> .\n", "<a> <b> <c> <g>\n", 'x "lit" .\n'])
def test_split_quad_without_graph_raises(line: str) -> None:
    with pytest.raises(ValueError):
        loader.split_quad(line)


def test_batches_split_by_graph_and_size() -> None:
    result = [(graph, len(triples)) for graph, triples in loader.batches(QUADS, 2)]

    assert result == [(G1, 2), (G1, 1), (G2, 1)]


def test_insert_update_wraps_triples_in_their_graph() -> None:
    update = loader.insert_update(G1, ["<a> <b> <c>", '<a> <b> "d"'])

    assert update == 'INSERT DATA { GRAPH <http://x/g1> {\n<a> <b> <c> .\n<a> <b> "d" .\n} }'


def test_main_loads_every_quad_and_counts(tmp_path: Path) -> None:
    server = FakeServer()

    code = loader.main(["--input", str(write_nq(tmp_path)), "--batch-size", "2"], send=server.send)

    assert code == 0
    assert {graph: len(triples) for graph, triples in server.graphs.items()} == {G1: 3, G2: 1}
    assert sum(1 for kind, _ in server.requests if kind == "update") == 3


def test_main_second_load_changes_nothing(tmp_path: Path) -> None:
    server = FakeServer()
    path = write_nq(tmp_path)
    loader.main(["--input", str(path)], send=server.send)

    code = loader.main(["--input", str(path)], send=server.send)

    assert code == 0
    assert len(server.graphs[G1]) == 3


def test_main_drop_graph_removes_old_triples_first(tmp_path: Path) -> None:
    server = FakeServer()
    server.graphs[G1] = {"<http://x/old> <http://x/p> <http://x/o>"}

    code = loader.main(["--input", str(write_nq(tmp_path)), "--drop-graph", "http://x/g1"], send=server.send)

    assert code == 0
    assert len(server.graphs[G1]) == 3


def test_main_server_that_loses_triples_fails(tmp_path: Path) -> None:
    server = FakeServer()

    def lossy(kind: str, text: str) -> str:
        return server.send(kind, text) if kind == "query" else "{}"

    assert loader.main(["--input", str(write_nq(tmp_path))], send=lossy) == 1


def test_main_dry_run_sends_nothing(tmp_path: Path) -> None:
    server = FakeServer()

    code = loader.main(["--input", str(write_nq(tmp_path)), "--dry-run"], send=server.send)

    assert code == 0
    assert server.requests == []


def test_main_default_graph_triple_stops_before_sending(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    server = FakeServer()
    path = write_nq(tmp_path, [QUADS[0], "<http://x/a> <http://x/p> <http://x/b> .\n"])

    with pytest.raises(SystemExit) as stop:
        loader.main(["--input", str(path)], send=server.send)

    assert stop.value.code == 1
    assert "line 2" in caplog.text
    assert server.requests == []


def test_main_without_token_stops(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.delenv(loader.TOKEN_VARIABLE, raising=False)

    with pytest.raises(SystemExit):
        loader.main(["--input", str(write_nq(tmp_path))])

    assert "QLEVER_ACCESS_TOKEN is not set" in caplog.text


def test_main_missing_input_is_a_usage_error(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as stop:
        loader.main(["--input", str(tmp_path / "missing.nq")])

    assert stop.value.code == 2
