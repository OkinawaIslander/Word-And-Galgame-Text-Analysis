"""Parsers for common visual-novel script engines."""

from __future__ import annotations

import re
from pathlib import Path

from ..encoding import read_text_file
from ..models import Segment
from ..text import clean_line, make_segment, strip_script_tags, valid_speaker
from .base import finalize

# ---------------------------------------------------------------------------
# KiriKiri / KAG3 (.ks)
# ---------------------------------------------------------------------------

_KAG_NAME_TAG_RE = re.compile(
    r"\[(?:name|chara|character)\b[^\]]*?(?:text|name)\s*=\s*[\"']([^\"']+)[\"']",
    re.IGNORECASE,
)


def parse_kirikiri(path: Path) -> ParsedDocument:
    decoded = read_text_file(path)
    segments: list[Segment] = []
    pending_speaker: str | None = None

    for line_no, line in enumerate(decoded.text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith(";") or stripped.startswith("//"):
            continue
        if stripped.startswith("*"):
            # Labels are navigation metadata, not story text.
            continue
        if stripped.startswith("@"):
            continue

        tag_speaker = _KAG_NAME_TAG_RE.search(stripped)
        if tag_speaker and valid_speaker(tag_speaker.group(1)):
            pending_speaker = tag_speaker.group(1).strip()

        visible = clean_line(strip_script_tags(stripped))
        if not visible:
            continue

        segment = make_segment(line_no, visible)
        if segment is None:
            continue
        if pending_speaker:
            segment.kind = "dialogue"
            segment.speaker = segment.speaker or pending_speaker
            pending_speaker = None
        segments.append(segment)

    return finalize(
        path,
        parser="kirikiri",
        encoding=decoded.encoding,
        segments=segments,
        metadata={"engine": "KiriKiri/KAG3"},
    )


# ---------------------------------------------------------------------------
# NScripter / ONScripter (.scn)
# ---------------------------------------------------------------------------

_NS_COMMANDS = {
    "add",
    "atoi",
    "avi",
    "bg",
    "bgm",
    "br",
    "caption",
    "cl",
    "click",
    "csp",
    "dec",
    "defsub",
    "delay",
    "dwaveloop",
    "dwave",
    "end",
    "er",
    "fileexist",
    "font",
    "game",
    "getini",
    "getparam",
    "gettext",
    "getversion",
    "goto",
    "humanz",
    "if",
    "inc",
    "itoa",
    "jump",
    "labellog",
    "ld",
    "load",
    "locale",
    "lookback",
    "menuselect",
    "monocro",
    "mov",
    "mp3",
    "mp3loop",
    "name",
    "nsa",
    "numalias",
    "quake",
    "resettimer",
    "return",
    "ruby",
    "save",
    "se",
    "select",
    "setwindow",
    "shell",
    "skip",
    "strsp",
    "sub",
    "systemcall",
    "text",
    "wa",
    "wait",
    "wave",
    "window",
}


def parse_nscripter(path: Path) -> ParsedDocument:
    decoded = read_text_file(path)
    segments: list[Segment] = []
    pending_speaker: str | None = None
    for line_no, line in enumerate(decoded.text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith(("*", "@", ";", "//")):
            continue
        first = re.split(r"[\s,]", stripped, maxsplit=1)[0].casefold()
        if first == "name":
            name_match = re.search(r'["\']([^"\']+)["\']', stripped)
            if name_match and valid_speaker(name_match.group(1)):
                pending_speaker = name_match.group(1).strip()
            continue
        if first in _NS_COMMANDS and not stripped.startswith(("「", "『", '"', "“")):
            continue
        # NScripter uses the two characters ``\n`` as an in-message line break.
        for chunk in stripped.replace("\\n", "\n").splitlines():
            segment = make_segment(line_no, chunk)
            if segment is None:
                continue
            if pending_speaker:
                segment.kind = "dialogue"
                segment.speaker = segment.speaker or pending_speaker
                pending_speaker = None
            segments.append(segment)
    return finalize(
        path,
        parser="nscripter",
        encoding=decoded.encoding,
        segments=segments,
        metadata={"engine": "NScripter/ONScripter"},
    )


# ---------------------------------------------------------------------------
# Ren'Py (.rpy)
# ---------------------------------------------------------------------------

_RENPY_CHARACTER_RE = re.compile(
    r"^\s*(?:define|default)\s+(?P<var>[A-Za-z_]\w*)\s*=\s*Character\s*\((?P<args>.*)\)\s*$",
    re.IGNORECASE,
)
_RENPY_NAME_RE = re.compile(r'^\s*(?:_\s*\(\s*)?["\']([^"\']+)["\']')
_RENPY_LABEL_RE = re.compile(r"^\s*label\s+([\w.]+)")
_RENPY_DIALOGUE_RE = re.compile(
    r'^\s*(?P<who>[A-Za-z_]\w*)(?:\s+[A-Za-z_]\w*)*\s+(?P<quote>["\'])(?P<text>.*)(?P=quote)\s*$'
)
_RENPY_NARRATION_RE = re.compile(r'^\s*(?P<quote>["\'])(?P<text>.*)(?P=quote)\s*$')
_RENPY_CHOICE_RE = re.compile(r'^\s*(?P<quote>["\'])(?P<text>.*)(?P=quote)\s*:\s*$')
_RENPY_TAG_RE = re.compile(r"\{[^{}]*\}")
_RENPY_SKIP_PREFIXES = (
    "$",
    "python",
    "init",
    "image",
    "define",
    "default",
    "transform",
    "style",
    "screen",
    "show",
    "scene",
    "hide",
    "play",
    "stop",
    "queue",
    "voice",
    "with",
    "pause",
    "jump",
    "call",
    "return",
    "if ",
    "elif ",
    "else",
    "while ",
    "for ",
    "menu",
    "label ",
    "window ",
    "nvl ",
    "centered",
    "extend ",
    "translate ",
    "old ",
    "new ",
)


def _strip_renpy_comment(line: str) -> str:
    """Remove a Ren'Py comment while preserving ``#`` inside strings."""
    quote: str | None = None
    escaped = False
    for index, char in enumerate(line):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = None
            continue
        if char in {'"', "'"}:
            quote = char
        elif char == "#":
            return line[:index]
    return line


def _unescape_renpy(value: str) -> str:
    return (
        value.replace("\\n", "\n")
        .replace("\\t", "\t")
        .replace('\\"', '"')
        .replace("\\'", "'")
        .replace("\\\\", "\\")
    )


def _clean_renpy_text(value: str) -> str:
    value = _RENPY_TAG_RE.sub("", value)
    value = value.replace("[[", "[").replace("]]", "]")
    return clean_line(_unescape_renpy(value))


def parse_renpy(path: Path) -> ParsedDocument:
    decoded = read_text_file(path)
    characters: dict[str, str | None] = {}
    segments: list[Segment] = []

    for line in decoded.text.splitlines():
        clean = _strip_renpy_comment(line)
        match = _RENPY_CHARACTER_RE.match(clean)
        if not match:
            continue
        args = match.group("args")
        name_match = _RENPY_NAME_RE.search(args)
        characters[match.group("var")] = name_match.group(1) if name_match else None
    # Character definitions are collected first, so forward references work.

    for line_no, line in enumerate(decoded.text.splitlines(), start=1):
        line = _strip_renpy_comment(line).rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        if _RENPY_CHARACTER_RE.match(line) or _RENPY_LABEL_RE.match(line):
            continue
        first = stripped.split(None, 1)[0].casefold()
        if stripped.startswith(_RENPY_SKIP_PREFIXES) or first in {
            "menu",
            "label",
            "define",
            "default",
            "init",
            "python",
            "screen",
            "transform",
            "style",
        }:
            # ``extend`` appends to the previous line and is still story text.
            extend = re.match(
                r'^\s*extend\s+(?P<quote>["\'])(?P<text>.*)(?P=quote)\s*$', line
            )
            if extend:
                text = _clean_renpy_text(extend.group("text"))
                if text:
                    segments.append(Segment(line_no=line_no, text=text, kind="narration"))
            continue

        choice = _RENPY_CHOICE_RE.match(line)
        if choice:
            text = _clean_renpy_text(choice.group("text"))
            if text:
                segments.append(Segment(line_no=line_no, text=text, kind="choice"))
            continue

        # ``e "text" with dissolve`` is a common compact form.
        candidate = re.sub(r"\s+with\s+[A-Za-z_]\w*\s*$", "", line)
        dialogue = _RENPY_DIALOGUE_RE.match(candidate)
        if dialogue:
            who = dialogue.group("who")
            if who in {"show", "scene", "play", "voice", "with", "jump", "call"}:
                continue
            text = _clean_renpy_text(dialogue.group("text"))
            if not text:
                continue
            speaker = characters.get(who, who)
            if speaker and not valid_speaker(speaker):
                speaker = None
            segments.append(
                Segment(
                    line_no=line_no,
                    text=text,
                    kind="dialogue" if speaker else "narration",
                    speaker=speaker,
                )
            )
            continue

        narration = _RENPY_NARRATION_RE.match(candidate)
        if narration:
            text = _clean_renpy_text(narration.group("text"))
            if text:
                segments.append(Segment(line_no=line_no, text=text, kind="narration"))

    return finalize(
        path,
        parser="renpy",
        encoding=decoded.encoding,
        segments=segments,
        metadata={"engine": "Ren'Py", "characters": {k: v for k, v in characters.items() if v}},
    )


def parser_stats(segments: list[Segment]) -> dict[str, int]:
    """Small helper used in tests and debugging."""
    return {
        "segments": len(segments),
        "dialogue": sum(1 for s in segments if s.kind == "dialogue"),
        "narration": sum(1 for s in segments if s.kind == "narration"),
        "headings": sum(1 for s in segments if s.kind == "heading"),
        "speakers": len({s.speaker for s in segments if s.speaker}),
    }
