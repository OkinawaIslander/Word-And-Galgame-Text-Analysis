"""Shared data structures.

The parser layer deliberately returns plain dataclasses: they are easy to test,
serialize with ``dataclasses.asdict`` and keep the SQLite layer independent from
any particular document format.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class Segment:
    """One searchable unit, usually a line or a table row."""

    line_no: int
    text: str
    kind: str = "narration"  # dialogue | narration | heading | table | footnote | meta
    speaker: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "line": self.line_no,
            "kind": self.kind,
            "speaker": self.speaker,
            "text": self.text,
        }
        if self.meta:
            data["meta"] = self.meta
        return data


@dataclass(slots=True)
class ParsedDocument:
    """A parsed source file before it is written to the index."""

    path: Path
    title: str
    parser: str
    language: str
    encoding: str
    segments: list[Segment]
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return "\n".join(segment.text for segment in self.segments)

    @property
    def char_count(self) -> int:
        return sum(len(segment.text) for segment in self.segments)

    @property
    def dialogue_count(self) -> int:
        return sum(1 for segment in self.segments if segment.kind == "dialogue")

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "title": self.title,
            "parser": self.parser,
            "language": self.language,
            "encoding": self.encoding,
            "metadata": self.metadata,
            "segments": [segment.as_dict() for segment in self.segments],
        }


@dataclass(slots=True)
class IndexReport:
    """Summary returned by :func:`textlens.index.build_index`."""

    seen: int = 0
    indexed: int = 0
    skipped: int = 0
    failed: int = 0
    removed: int = 0
    segments: int = 0
    chars: int = 0
    skipped_archives: list[tuple[str, str]] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)
    duration: float = 0.0


@dataclass(slots=True)
class SearchHit:
    doc_id: int
    path: str
    title: str
    ext: str
    line_no: int
    kind: str
    speaker: str | None
    text: str
    rank: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "path": self.path,
            "title": self.title,
            "ext": self.ext,
            "line": self.line_no,
            "kind": self.kind,
            "speaker": self.speaker,
            "text": self.text,
        }
