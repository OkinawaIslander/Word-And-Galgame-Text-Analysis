"""Game archive readers.

Currently implemented: YU-RIS YPF containing YSTB compiled scripts.
ARC, CPZ, DAT, XP3, NSA, RPA and similar containers are future work.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..errors import TextLensError


class ArchiveError(TextLensError):
    """A container could not be opened or decoded."""


class UnsupportedArchive(ArchiveError):
    """The container format is recognised but not implemented."""


@dataclass(slots=True)
class ArchiveReport:
    archive: str
    format: str = ""
    output: str = ""
    entries: int = 0
    text_entries: int = 0
    text_segments: int = 0
    chars: int = 0
    raw_entries: int = 0
    key: str = ""
    errors: list[tuple[str, str]] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


def open_archive(path: str | Path):
    """Return a reader for *path* based on its signature."""
    from .ypf import YpfArchive, is_ypf

    path = Path(path)
    if is_ypf(path):
        return YpfArchive(path)
    with path.open("rb") as handle:
        magic = handle.read(16)
    suffix = path.suffix.casefold()
    if suffix in {".arc", ".cpz", ".dat", ".xp3", ".nsa", ".rpa", ".pak", ".pck"}:
        raise UnsupportedArchive(
            f"{suffix} 归档包的解包功能待开发；当前只支持 YU-RIS .ypf。"
            "请先用对应引擎工具解包，或把文本导出后再索引。"
        )
    raise UnsupportedArchive(
        f"不支持的归档格式：{suffix or '(无扩展名)'}，magic {magic[:8].hex()}。"
        "当前只实现了 YU-RIS .ypf 解包。"
    )
