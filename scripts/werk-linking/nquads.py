# Purpose:      N-Quads terms and an atomic, counting N-Quads file for the Werk linking writer.
#               Imported by write_werk_rdf.py, ner.py and linking.py.
# Usage:        not run itself.
# Inputs:       none.
# Outputs:      QuadFile writes <path>.tmp line by line and renames it to <path> on a clean exit.
# Dependencies: standard library.
# Assumptions:  IRIs are written as given, so iri() refuses the characters N-Quads forbids in an
#               IRI instead of escaping them. Literals are escaped per N-Quads (\\, \", \n, \r).

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from types import TracebackType
from typing import IO

GEMEA = "https://gemea.ise.fiz-karlsruhe.de/"
RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
RDFS = "http://www.w3.org/2000/01/rdf-schema#"
OWL = "http://www.w3.org/2002/07/owl#"
XSD = "http://www.w3.org/2001/XMLSchema#"
PROV = "http://www.w3.org/ns/prov#"
PROVCRED = "https://prov-cred.org/ns/"
MLS = "http://www.w3.org/ns/mls#"
MOCHO = "https://ise-fizkarlsruhe.github.io/ddbkg/mocho#"
RDAC = "http://rdaregistry.info/Elements/c/"
SKOS = "http://www.w3.org/2004/02/skos/core#"
VOID = "http://rdfs.org/ns/void#"
DCTERMS = "http://purl.org/dc/terms/"

GRAPH_WORK = GEMEA + "graph/work"
GRAPH_PROV = GEMEA + "graph/prov"
GRAPH_DDBEDM = GEMEA + "graph/ddbedm"

# Characters that N-Quads does not allow inside <...>.
FORBIDDEN_IN_IRI = re.compile(r'[\x00-\x20<>"{}|^`\\]')

Triple = tuple[str, str, str]


def iri(value: str) -> str:
    """An IRI as an N-Quads term; raises ValueError on a character N-Quads forbids."""
    if not value or FORBIDDEN_IN_IRI.search(value):
        raise ValueError(f"not a valid IRI for N-Quads: {value!r}")
    return f"<{value}>"


def literal(value: str, datatype: str | None = None, lang: str | None = None) -> str:
    """A literal as an N-Quads term: plain, typed (datatype IRI) or language-tagged."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r")
    if lang:
        return f'"{escaped}"@{lang}'
    if datatype:
        return f'"{escaped}"^^{iri(datatype)}'
    return f'"{escaped}"'


def integer(value: int) -> str:
    return literal(str(value), XSD + "integer")


def decimal(value: float) -> str:
    return literal(repr(float(value)), XSD + "decimal")


def quad_line(triple: Triple, graph: str) -> str:
    """One N-Quads line from three terms and a graph term."""
    subject, predicate, obj = triple
    return f"{subject} {predicate} {obj} {graph} .\n"


class QuadFile:
    """Writes quads to <path>.tmp and renames it to <path> when the block ends without an error."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.temp = path.with_name(path.name + ".tmp")
        self.count = 0
        self._hash = hashlib.sha256()
        self._file: IO[bytes] | None = None

    def __enter__(self) -> QuadFile:  # noqa: PYI034 (typing.Self needs Python 3.11)
        self._file = open(self.temp, "wb")
        return self

    def write(self, triple: Triple, graph: str) -> None:
        assert self._file is not None, "QuadFile used outside its with block"
        data = quad_line(triple, graph).encode("utf-8")
        self._file.write(data)
        self._hash.update(data)
        self.count += 1

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None) -> None:
        assert self._file is not None
        self._file.close()
        if exc_type is None:
            os.replace(self.temp, self.path)
        else:
            self.temp.unlink(missing_ok=True)

    @property
    def sha256(self) -> str:
        return self._hash.hexdigest()
