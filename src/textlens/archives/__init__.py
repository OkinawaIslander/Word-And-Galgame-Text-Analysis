"""Game archive readers.

Currently implemented: YU-RIS YPF containing YSTB compiled scripts.
Other containers are detected and reported rather than guessed at.
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
    if path.suffix.casefold() == ".arc" and magic.startswith(b"ARC\x1a"):
        raise UnsupportedArchive(
            "这是 CLOCKUP/自定义 ARC 容器，条目为编译后的 .asb，且数据经过加密；"
            "当前版本不能直接反编译。请先用对应引擎工具解包/反编译。"
        )
    if path.suffix.casefold() == ".dat":
        raise UnsupportedArchive(
            f"暂时无法识别这个 .DAT 容器（magic {magic[:4].hex()}，高熵）。"
            "它不是 YU-RIS YPF，也没有可直接读取的明文索引；"
            "请提供该补丁/引擎的解包工具，或先导出脚本。"
        )
    raise UnsupportedArchive(
        f"不支持的归档格式：{path.suffix or '(无扩展名)'}，magic {magic[:8].hex()}。"
    )
