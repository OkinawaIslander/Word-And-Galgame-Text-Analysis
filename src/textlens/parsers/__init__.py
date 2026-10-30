"""Format registry and file discovery."""

from __future__ import annotations

import fnmatch
import os
from pathlib import Path
from typing import Callable

from ..errors import ParseError, TextLensError, UnsupportedFormat
from ..models import ParsedDocument
from ..text import display_width, pad_display
from .docx import parse_docx
from .engines import parse_kirikiri, parse_nscripter, parse_renpy
from .plain import parse_ass, parse_csv, parse_json, parse_plain, parse_srt, parse_tjs

Parser = Callable[[Path], ParsedDocument]

PARSERS: dict[str, Parser] = {
    ".docx": parse_docx,
    ".docm": parse_docx,
    ".dotx": parse_docx,
    ".dotm": parse_docx,
    ".txt": parse_plain,
    ".text": parse_plain,
    ".md": parse_plain,
    ".markdown": parse_plain,
    ".ks": parse_kirikiri,
    ".scn": parse_nscripter,
    ".rpy": parse_renpy,
    ".tjs": parse_tjs,
    ".json": parse_json,
    ".jsonl": parse_json,
    ".csv": parse_csv,
    ".tsv": parse_csv,
    ".srt": parse_srt,
    ".ass": parse_ass,
    ".ssa": parse_ass,
}

LEGACY_EXTENSIONS = frozenset({".doc", ".dot"})

# Containers and compiled assets.  They are not text and usually need an
# engine-specific unpacker/decompiler before TextLens can read them.
ARCHIVE_EXTENSIONS: dict[str, str] = {
    ".arc": "CLOCKUP/自定义 ARC 归档",
    ".dat": "游戏数据归档",
    ".xp3": "KiriKiri XP3 归档",
    ".nsa": "NScripter NSA 归档",
    ".rpa": "Ren'Py RPA 归档",
    ".pak": "通用 PAK 归档",
    ".pck": "游戏资源包",
    ".asb": "编译后的脚本",
    ".sdat": "加密数据文件",
}

SUPPORTED_EXTENSIONS = frozenset(PARSERS) | LEGACY_EXTENSIONS
ARCHIVE_EXTENSION_SET = frozenset(ARCHIVE_EXTENSIONS)

_IGNORED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".textlens",
}
_DOC_LOCK_PREFIX = "~$"


def normalize_extension(value: str) -> str:
    value = value.strip().casefold()
    if not value:
        return ""
    return value if value.startswith(".") else f".{value}"


def parse_file(path: str | Path) -> ParsedDocument:
    """Parse one file, raising a user-presentable :class:`TextLensError`."""
    path = Path(path)
    if not path.is_file():
        raise ParseError(f"文件不存在：{path}")
    suffix = path.suffix.casefold()
    if suffix in ARCHIVE_EXTENSIONS:
        raise UnsupportedFormat(
            f"{ARCHIVE_EXTENSIONS[suffix]}（{suffix}）不是可直接读取的文本。"
            "请先用 GARbro、krkrextract、nsaextract 等工具解包/反编译，"
            "再把导出的 .ks/.rpy/.scn/.txt 交给 TextLens。"
        )
    if suffix in LEGACY_EXTENSIONS:
        raise UnsupportedFormat(
            "旧版 Word 二进制格式（.doc/.dot）暂不支持。请用 Word 另存为 .docx，"
            "或用 LibreOffice 转换：soffice --headless --convert-to docx <文件>"
        )
    parser = PARSERS.get(suffix)
    if parser is None:
        raise UnsupportedFormat(
            f"不支持的格式 {suffix or '(无扩展名)'}；可用 --include 指定支持的扩展名。"
        )
    try:
        return parser(path)
    except TextLensError:
        raise
    except Exception as exc:  # keep one bad file from stopping a batch
        raise ParseError(f"{type(exc).__name__}: {exc}") from exc


def _matches_exclude(path: Path, root: Path, patterns: list[str]) -> bool:
    if not patterns:
        return False
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError:
        relative = path.as_posix()
    candidates = (path.name, relative, path.as_posix())
    return any(
        fnmatch.fnmatchcase(candidate, pattern)
        for pattern in patterns
        for candidate in candidates
    )


def _discover(
    paths: list[str | Path],
    include_set: set[str],
    excludes: list[str] | None,
    *,
    explicit_unfiltered: bool,
) -> list[Path]:
    exclude_patterns = [pattern for pattern in (excludes or []) if pattern]
    found: list[Path] = []
    seen: set[str] = set()

    def add(candidate: Path) -> None:
        try:
            resolved = candidate.resolve()
        except OSError:
            resolved = candidate.absolute()
        key = os.path.normcase(str(resolved))
        if key not in seen:
            seen.add(key)
            found.append(resolved)

    for raw in paths:
        path = Path(raw).expanduser()
        if path.is_file():
            if path.name.startswith(_DOC_LOCK_PREFIX):
                continue
            if explicit_unfiltered or path.suffix.casefold() in include_set:
                add(path)
            continue
        if not path.is_dir():
            continue
        for root_text, dirs, files in os.walk(path, followlinks=False):
            root = Path(root_text)
            dirs[:] = sorted(
                (
                    name
                    for name in dirs
                    if not name.startswith(".")
                    and name.casefold() not in _IGNORED_DIRS
                    and not _matches_exclude(root / name, path, exclude_patterns)
                ),
                key=str.casefold,
            )
            for name in sorted(files, key=str.casefold):
                if name.startswith(_DOC_LOCK_PREFIX):
                    continue
                candidate = root / name
                if candidate.suffix.casefold() not in include_set:
                    continue
                if _matches_exclude(candidate, path, exclude_patterns):
                    continue
                add(candidate)

    found.sort(key=lambda item: os.path.normcase(str(item)))
    return found


def discover_files(
    paths: list[str | Path],
    *,
    includes: list[str] | None = None,
    excludes: list[str] | None = None,
) -> list[Path]:
    """Expand files/directories into a sorted, de-duplicated text file list."""
    include_set = (
        {normalize_extension(item) for item in includes if normalize_extension(item)}
        if includes
        else set(SUPPORTED_EXTENSIONS)
    )
    return _discover(paths, include_set, excludes, explicit_unfiltered=True)


def discover_archives(
    paths: list[str | Path],
    *,
    excludes: list[str] | None = None,
) -> list[Path]:
    """Find game containers that need unpacking before they can be searched."""
    return _discover(
        paths,
        set(ARCHIVE_EXTENSION_SET),
        excludes,
        explicit_unfiltered=False,
    )


def format_support_table() -> str:
    groups = [
        ("Word", ".docx .docm .dotx .dotm"),
        ("纯文本", ".txt .md .markdown"),
        ("KiriKiri/KAG", ".ks"),
        ("Ren'Py", ".rpy"),
        ("NScripter", ".scn"),
        ("TJS", ".tjs（尽力提取字符串）"),
        ("结构化", ".json .jsonl .csv .tsv"),
        ("字幕", ".srt .ass .ssa"),
        ("旧版 Word", ".doc .dot（需先转换）"),
        ("游戏归档", ".arc .dat .xp3 .nsa .rpa（需先解包）"),
    ]
    width = max(display_width(name) for name, _ in groups)
    return "\n".join(f"  {pad_display(name, width)}  {exts}" for name, exts in groups)
