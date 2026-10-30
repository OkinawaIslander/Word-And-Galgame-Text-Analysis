"""Plain text, subtitle, JSON/CSV and TJS parsers."""

from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path
from typing import Any

from ..encoding import read_text_file
from ..errors import ParseError
from ..models import Segment
from ..text import (
    clean_line,
    classify_line,
    make_segment,
    strip_ass_tags,
    valid_speaker,
)
from .base import finalize

_TEXT_KEYS = {
    "text",
    "message",
    "msg",
    "line",
    "content",
    "dialogue",
    "body",
    "sentence",
    "utterance",
    "jp",
    "cn",
    "zh",
    "en",
}
_SPEAKER_KEYS = {
    "speaker",
    "name",
    "character",
    "chara",
    "who",
    "role",
    "actor",
    "talker",
}
_JSON_SKIP_KEYS = {
    "id",
    "uid",
    "type",
    "voice",
    "file",
    "path",
    "url",
    "image",
    "bg",
    "bgm",
    "se",
    "next",
    "goto",
}


def parse_plain(path: Path) -> ParsedDocument:
    decoded = read_text_file(path)
    segments: list[Segment] = []
    for line_no, line in enumerate(decoded.text.splitlines(), start=1):
        segment = make_segment(line_no, line)
        if segment is not None:
            segments.append(segment)
    return finalize(
        path,
        parser="plaintext",
        encoding=decoded.encoding,
        segments=segments,
    )


def parse_srt(path: Path) -> ParsedDocument:
    decoded = read_text_file(path)
    lines = decoded.text.splitlines()
    segments: list[Segment] = []
    block_start = 0
    block: list[str] = []

    def flush() -> None:
        nonlocal block
        if not block:
            return
        timing_index = next((i for i, line in enumerate(block) if "-->" in line), None)
        if timing_index is None:
            block = []
            return
        body = [line for line in block[timing_index + 1 :] if clean_line(line)]
        if body:
            text = " ".join(clean_line(line) for line in body)
            segment = make_segment(block_start + timing_index + 1, text)
            if segment is not None:
                segments.append(segment)
        block = []

    for index, line in enumerate(lines, start=1):
        if not line.strip():
            flush()
            block_start = index
            continue
        if not block:
            block_start = index
        block.append(line)
    flush()

    return finalize(
        path,
        parser="srt",
        encoding=decoded.encoding,
        segments=segments,
    )


def parse_ass(path: Path) -> ParsedDocument:
    decoded = read_text_file(path)
    segments: list[Segment] = []
    for line_no, line in enumerate(decoded.text.splitlines(), start=1):
        if not line.lstrip().casefold().startswith("dialogue:"):
            continue
        payload = line.split(":", 1)[1].lstrip()
        # Dialogue: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
        fields = payload.split(",", 9)
        if len(fields) < 10:
            continue
        speaker = clean_line(fields[4]).strip() or None
        if speaker and not valid_speaker(speaker):
            speaker = None
        text = fields[9].replace("\\N", "\n").replace("\\n", "\n")
        for chunk in strip_ass_tags(text).splitlines():
            info = classify_line(chunk)
            if info is None:
                continue
            segments.append(
                Segment(
                    line_no=line_no,
                    text=info.text,
                    kind="dialogue" if speaker else info.kind,
                    speaker=speaker or info.speaker,
                )
            )
    return finalize(
        path,
        parser="ass",
        encoding=decoded.encoding,
        segments=segments,
    )


class _JsonBuilder:
    def __init__(self) -> None:
        self.segments: list[Segment] = []
        self.cursor = 0

    def add(self, value: str, *, speaker: str | None = None, kind: str | None = None) -> None:
        value = clean_line(value)
        if not value:
            return
        for line in value.splitlines():
            line = clean_line(line)
            if not line:
                continue
            self.cursor += 1
            if kind == "dialogue":
                info = classify_line(line)
                text = info.text if info is not None else line
                self.segments.append(
                    Segment(
                        line_no=self.cursor,
                        text=text,
                        kind="dialogue",
                        speaker=speaker or (info.speaker if info is not None else None),
                    )
                )
            else:
                segment = make_segment(self.cursor, line)
                if segment is None:
                    continue
                if speaker:
                    segment.kind = "dialogue"
                    segment.speaker = speaker
                self.segments.append(segment)


def _first_string(mapping: dict[str, Any], keys: set[str]) -> str | None:
    for key, value in mapping.items():
        if str(key).casefold() in keys and isinstance(value, str) and value.strip():
            return value
    return None


def _walk_json(value: Any, builder: _JsonBuilder, inherited_speaker: str | None = None) -> None:
    if isinstance(value, list):
        for item in value:
            _walk_json(item, builder, inherited_speaker)
        return
    if not isinstance(value, dict):
        if isinstance(value, str) and inherited_speaker:
            builder.add(value, speaker=inherited_speaker, kind="dialogue")
        return

    speaker = _first_string(value, _SPEAKER_KEYS) or inherited_speaker
    text = _first_string(value, _TEXT_KEYS)
    if text is not None:
        key_is_dialogue = any(
            str(key).casefold() in {"dialogue", "message", "msg", "utterance", "line"}
            for key in value
            if isinstance(value.get(key), str) and value.get(key) == text
        )
        builder.add(
            text,
            speaker=speaker,
            kind="dialogue" if speaker or key_is_dialogue else None,
        )
        return

    for key, child in value.items():
        if str(key).casefold() in _SPEAKER_KEYS | _JSON_SKIP_KEYS:
            continue
        _walk_json(child, builder, speaker)


def parse_json(path: Path) -> ParsedDocument:
    decoded = read_text_file(path)
    builder = _JsonBuilder()
    if path.suffix.casefold() == ".jsonl":
        for line_no, line in enumerate(decoded.text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ParseError(f"JSONL 第 {line_no} 行解析失败：{exc}") from exc
            _walk_json(value, builder)
    else:
        try:
            value = json.loads(decoded.text)
        except json.JSONDecodeError as exc:
            raise ParseError(f"JSON 解析失败：{exc}") from exc
        _walk_json(value, builder)
    return finalize(
        path,
        parser="json",
        encoding=decoded.encoding,
        segments=builder.segments,
    )


def parse_csv(path: Path) -> ParsedDocument:
    decoded = read_text_file(path)
    text = decoded.text
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",\t;|")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(io.StringIO(text), dialect))
    if not rows:
        return finalize(path, parser="csv", encoding=decoded.encoding, segments=[])

    header = [cell.strip().casefold().lstrip("\ufeff") for cell in rows[0]]
    text_col = next((i for i, name in enumerate(header) if name in _TEXT_KEYS), None)
    speaker_col = next((i for i, name in enumerate(header) if name in _SPEAKER_KEYS), None)

    if text_col is not None:
        data_rows = rows[1:]
    else:
        data_rows = rows
        if len(header) >= 2:
            speaker_col = 0
            text_col = len(header) - 1
        else:
            text_col = 0

    segments: list[Segment] = []
    cursor = 0
    for row in data_rows:
        if not row or all(not cell.strip() for cell in row):
            continue
        raw_text = row[text_col] if text_col < len(row) else ""
        speaker = None
        if speaker_col is not None and speaker_col < len(row):
            candidate = clean_line(row[speaker_col])
            if candidate and valid_speaker(candidate):
                speaker = candidate
        for line in raw_text.splitlines():
            cursor += 1
            segment = make_segment(cursor, line)
            if segment is None:
                continue
            if speaker:
                segment.kind = "dialogue"
                segment.speaker = speaker
            segments.append(segment)

    return finalize(
        path,
        parser="csv",
        encoding=decoded.encoding,
        segments=segments,
    )


_TJS_STRING_RE = re.compile(r'"(?:\\.|[^"\\])*"')
_TJS_PATH_RE = re.compile(
    r"(?:\.(?:png|jpg|jpeg|bmp|gif|webp|ogg|wav|mp3|mp4|avi|ttf|otf|tjs|ks|scn|rpy|json|txt)\b|^https?://|^[A-Za-z]:\\)",
    re.IGNORECASE,
)
_CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]")


def _unescape_tjs(value: str) -> str:
    return (
        value.replace("\\n", "\n")
        .replace("\\t", "\t")
        .replace('\\"', '"')
        .replace("\\\\", "\\")
    )


def parse_tjs(path: Path) -> ParsedDocument:
    """Best-effort extraction of displayable string literals from TJS code.

    TJS is a programming language, not a dialogue format.  This parser keeps
    CJK-bearing literals and longer human-readable strings, and deliberately
    ignores paths and asset names.  It is useful for text mining, but a game
    with strings generated at runtime may need a custom parser.
    """
    decoded = read_text_file(path)
    segments: list[Segment] = []
    for line_no, line in enumerate(decoded.text.splitlines(), start=1):
        stripped = line.lstrip()
        if stripped.startswith("//"):
            continue
        for match in _TJS_STRING_RE.finditer(line):
            value = _unescape_tjs(match.group(0)[1:-1])
            value = value.strip()
            if not value or _TJS_PATH_RE.search(value):
                continue
            has_cjk = bool(_CJK_RE.search(value))
            if not has_cjk and not (len(value) >= 15 and " " in value):
                continue
            if value.startswith(("%", "#", "$")):
                continue
            for chunk in value.splitlines():
                segment = make_segment(line_no, chunk)
                if segment is not None:
                    if not has_cjk:
                        segment.kind = "narration"
                    segments.append(segment)
    return finalize(
        path,
        parser="tjs",
        encoding=decoded.encoding,
        segments=segments,
    )
