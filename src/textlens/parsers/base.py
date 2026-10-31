"""Helpers shared by format parsers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..models import ParsedDocument, Segment
from ..text import clean_line, detect_language


def finalize(
    path: Path,
    *,
    parser: str,
    encoding: str,
    segments: list[Segment | None],
    metadata: dict[str, Any] | None = None,
    title: str | None = None,
) -> ParsedDocument:
    """Drop empty segments and fill in document-level fields."""
    usable: list[Segment] = []
    for segment in segments:
        if segment is None:
            continue
        cleaned = clean_line(segment.text)
        if not cleaned:
            continue
        segment.text = cleaned
        usable.append(segment)
    metadata = metadata or {}
    doc_title = title or str(metadata.get("title") or "").strip() or path.stem
    return ParsedDocument(
        path=path,
        title=doc_title,
        parser=parser,
        language=detect_language("\n".join(segment.text for segment in usable)),
        encoding=encoding,
        segments=usable,
        metadata=metadata,
    )
