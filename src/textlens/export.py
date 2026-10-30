"""Export parsed corpora to JSONL, plain text or Markdown."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

from .errors import TextLensError
from .models import ParsedDocument
from .parsers import discover_files, parse_file


@dataclass(slots=True)
class ExtractReport:
    files: int = 0
    segments: int = 0
    chars: int = 0
    errors: list[tuple[str, str]] = field(default_factory=list)
    output: str = ""


def infer_format(output: str, explicit: str | None = None) -> str:
    if explicit:
        choice = explicit.casefold()
        if choice in {"jsonl", "ndjson"}:
            return "jsonl"
        if choice in {"txt", "text"}:
            return "txt"
        if choice in {"md", "markdown"}:
            return "md"
        raise TextLensError(f"未知导出格式：{explicit}")
    suffix = Path(output).suffix.casefold()
    if suffix in {".jsonl", ".ndjson"}:
        return "jsonl"
    if suffix in {".md", ".markdown"}:
        return "md"
    return "txt"


def _json_line(document: ParsedDocument, line_no: int, kind: str, speaker: str | None, text: str) -> str:
    payload = {
        "path": str(document.path),
        "title": document.title,
        "language": document.language,
        "line": line_no,
        "kind": kind,
        "speaker": speaker,
        "text": text,
    }
    return json.dumps(payload, ensure_ascii=False) + "\n"


def _write_jsonl(handle: TextIO, document: ParsedDocument, dialogue_only: bool) -> tuple[int, int]:
    count = 0
    chars = 0
    for segment in document.segments:
        if dialogue_only and segment.kind != "dialogue":
            continue
        handle.write(
            _json_line(document, segment.line_no, segment.kind, segment.speaker, segment.text)
        )
        count += 1
        chars += len(segment.text)
    return count, chars


def _write_text(handle: TextIO, document: ParsedDocument, dialogue_only: bool, *, markdown: bool) -> tuple[int, int]:
    count = 0
    chars = 0
    heading = f"# {document.title}" if markdown else f"=== {document.title} ==="
    handle.write(heading + "\n")
    handle.write(f"来源：{document.path}\n\n")
    for segment in document.segments:
        if dialogue_only and segment.kind != "dialogue":
            continue
        if segment.kind == "heading":
            handle.write(f"\n## {segment.text}\n\n")
        elif segment.speaker:
            prefix = f"**{segment.speaker}**：" if markdown else f"{segment.speaker}："
            handle.write(f"{prefix}{segment.text}\n")
        else:
            handle.write(f"{segment.text}\n")
        count += 1
        chars += len(segment.text)
    handle.write("\n")
    return count, chars


def extract(
    paths: list[str | Path],
    output: str | Path,
    *,
    fmt: str | None = None,
    includes: list[str] | None = None,
    excludes: list[str] | None = None,
    dialogue_only: bool = False,
    force: bool = False,
) -> ExtractReport:
    files = discover_files(paths, includes=includes, excludes=excludes)
    output_text = str(output)
    chosen_format = infer_format(output_text, fmt)
    to_stdout = output_text == "-"
    target: Path | None = None
    if not to_stdout:
        target = Path(output_text).expanduser()
        # Never parse an old export as if it were a source document.
        files = [
            path
            for path in files
            if os.path.normcase(str(path.resolve()))
            != os.path.normcase(str(target.resolve()))
        ]
        if target.exists() and not force:
            raise TextLensError(f"输出文件已存在：{target}（加 --force 覆盖）")

    report = ExtractReport(files=len(files), output=output_text)
    handle: TextIO
    temporary: Path | None = None
    succeeded = False

    if to_stdout:
        import sys

        handle = sys.stdout
    else:
        assert target is not None
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".tmp")
        handle = temporary.open("w", encoding="utf-8", newline="\n")

    try:
        for path in files:
            try:
                document = parse_file(path)
                if chosen_format == "jsonl":
                    count, chars = _write_jsonl(handle, document, dialogue_only)
                else:
                    count, chars = _write_text(
                        handle, document, dialogue_only, markdown=chosen_format == "md"
                    )
                report.segments += count
                report.chars += chars
            except Exception as exc:
                report.errors.append((str(path), str(exc) or type(exc).__name__))
        succeeded = True
    finally:
        if not to_stdout:
            handle.close()
            if not succeeded and temporary is not None and temporary.exists():
                temporary.unlink(missing_ok=True)

    if temporary is not None and succeeded and target is not None:
        os.replace(temporary, target)
    return report
