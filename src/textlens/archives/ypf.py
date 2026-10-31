"""YU-RIS YPF archive reader and YSTB text extractor.

The YPF container and the YSTB V5 layout are documented by the YU-RIS
community tooling (notably RxYuris and GARbro).  This module implements only
the read path needed for text mining: list entries, inflate them, recover the
YSTB XOR key from script structure, and pull out Japanese text arguments.
"""

from __future__ import annotations

import json
import re
import shutil
import struct
import zlib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from ..models import Segment
from ..text import clean_line, make_segment
from . import ArchiveError, ArchiveReport, UnsupportedArchive

_SWAP_TABLE_00 = (
    0x03, 0x48, 0x06, 0x35,
    0x0C, 0x10, 0x11, 0x19, 0x1C, 0x1E,
    0x09, 0x0B, 0x0D, 0x13, 0x15, 0x1B, 0x20, 0x23, 0x26, 0x29,
    0x2C, 0x2F, 0x2E, 0x32,
)
_SWAP_TABLE_04 = (
    0x0C, 0x10, 0x11, 0x19, 0x1C, 0x1E,
    0x09, 0x0B, 0x0D, 0x13, 0x15, 0x1B, 0x20, 0x23, 0x26, 0x29,
    0x2C, 0x2F, 0x2E, 0x32,
)
_SWAP_TABLE_10 = (
    0x09, 0x0B, 0x0D, 0x13, 0x15, 0x1B, 0x20, 0x23, 0x26, 0x29,
    0x2C, 0x2F, 0x2E, 0x32,
)

_JP_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")
_ASCII_RE = re.compile(r"[A-Za-z0-9]")
_PATH_RE = re.compile(
    r"(?:^[A-Za-z0-9_./\\:-]+$|\.(?:png|jpg|jpeg|bmp|gif|ogg|wav|mp3|ybn|tjs|dat|txt|bin)$)",
    re.IGNORECASE,
)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SPEAKER_START_RE = re.compile(r"^[^\s「」]{1,20}「")
_SENTENCE_END_RE = re.compile(r"[。！？!?…」』”\"]$")


def is_ypf(path: str | Path) -> bool:
    try:
        with open(path, "rb") as handle:
            return handle.read(4) == b"YPF\0"
    except OSError:
        return False


def _xor(data: bytes, key: bytes) -> bytes:
    return bytes(value ^ key[index & 3] for index, value in enumerate(data))


def _swap_table(version: int) -> tuple[int, ...]:
    if version < 0x100:
        return _SWAP_TABLE_04
    if 0x12C <= version < 0x196:
        return _SWAP_TABLE_10
    return _SWAP_TABLE_00


def _decrypt_length(table: tuple[int, ...], value: int) -> int:
    try:
        position = table.index(value)
    except ValueError:
        return value
    if position & 1:
        return table[position - 1]
    return table[position + 1]


@dataclass(slots=True)
class YpfEntry:
    name: str
    file_type: int
    packed: bool
    unpacked_size: int
    size: int
    offset: int
    checksum: int


@dataclass(slots=True)
class YstbText:
    segments: list[Segment]
    header_size: int
    args_entries: int
    version: int


class YpfArchive:
    """Read-only YPF archive."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        try:
            self.data = self.path.read_bytes()
        except OSError as exc:
            raise ArchiveError(f"无法读取归档：{exc}") from exc
        if self.data[:4] != b"YPF\0":
            raise ArchiveError("不是 YPF 归档（缺少 YPF 文件头）。")
        if len(self.data) < 0x20:
            raise ArchiveError("YPF 文件头不完整。")
        self.version, self.count, self.dir_size = struct.unpack_from("<III", self.data, 4)
        if not (0 < self.count <= 100000):
            raise ArchiveError(f"YPF 条目数量异常：{self.count}")
        if self.dir_size < self.count * 0x17 or 0x20 + self.dir_size > len(self.data):
            raise ArchiveError("YPF 目录大小异常。")
        self.entries = self._read_directory()

    def _read_directory(self) -> list[YpfEntry]:
        table = _swap_table(self.version)
        extra = 4 if self.version >= 0x1D9 else 8 if self.version == 0xDE else 0
        entry_extra = 0x12 + extra
        position = 0x20
        entries: list[YpfEntry] = []
        for _ in range(self.count):
            if position + 5 > len(self.data):
                raise ArchiveError("YPF 目录被截断。")
            name_hash, encrypted_length = struct.unpack_from("<IB", self.data, position)
            del name_hash
            position += 5
            name_length = _decrypt_length(table, encrypted_length ^ 0xFF)
            if not (0 < name_length <= 0x200) or position + name_length + entry_extra > len(self.data):
                raise ArchiveError("YPF 文件名长度异常。")
            raw_name = self.data[position:position + name_length]
            position += name_length
            # All entries end with a three-letter extension, so the last four
            # encrypted bytes reveal the per-archive name XOR byte.
            name_key = raw_name[-4] ^ ord(".") if name_length >= 4 else 0
            name = bytes(value ^ name_key for value in raw_name).decode("cp932", errors="replace")
            file_type = self.data[position]
            packed = self.data[position + 1] != 0
            unpacked_size, size, offset, checksum = struct.unpack_from(
                "<IIII", self.data, position + 2
            )
            position += entry_extra
            if offset + size > len(self.data):
                raise ArchiveError(f"YPF 条目越界：{name}")
            entries.append(
                YpfEntry(
                    name=name,
                    file_type=file_type,
                    packed=packed,
                    unpacked_size=unpacked_size,
                    size=size,
                    offset=offset,
                    checksum=checksum,
                )
            )
        return entries

    def read_entry(self, entry: YpfEntry) -> bytes:
        raw = self.data[entry.offset:entry.offset + entry.size]
        if not entry.packed:
            return raw
        try:
            return zlib.decompress(raw)
        except zlib.error as exc:
            raise ArchiveError(f"解压失败：{entry.name}：{exc}") from exc

    def guess_ystb_key(self) -> bytes:
        """Recover the four-byte YSTB V5 XOR key from script structure.

        The key is game-specific.  Small scripts keep the search fast: candidate
        key high bytes come from the instruction stream, the remaining byte is
        brute-forced and scored by arguments-index validity and Japanese text.
        """
        candidates = sorted(
            (
                entry
                for entry in self.entries
                if entry.name.casefold().endswith(".ybn")
                and 0x20 < entry.unpacked_size < 2_000_000
            ),
            key=lambda item: item.unpacked_size,
        )

        # A tiny UI script is enough to learn the three high key bytes, but it
        # may contain no story text.  Verify candidate low bytes against a
        # medium-sized script that actually has text arguments.
        seed_parts = None
        seed_raw = None
        for entry in candidates:
            try:
                blob = self.read_entry(entry)
            except ArchiveError:
                continue
            parts = _ystb_parts(blob)
            if parts is not None and len(parts[2]) >= 24 and len(parts[3]) >= 128:
                seed_parts = parts
                seed_raw = blob
                break
        if seed_parts is None:
            return None
        _, seed_inst, seed_idx, _ = seed_parts
        if len(seed_inst) < 8:
            return None
        key3_candidates = [byte for byte, _ in Counter(seed_inst[3::4]).most_common(2)] or [0]
        key2_candidates = [byte for byte, _ in Counter(seed_inst[2::4]).most_common(2)] or [0]
        key1_candidates: list[int] = []
        for counts in (Counter(seed_idx[1::12]), Counter(seed_idx[5::12])):
            for byte, _ in counts.most_common(2):
                if byte not in key1_candidates:
                    key1_candidates.append(byte)
        if not key1_candidates:
            key1_candidates = [0]

        # Rank low-byte candidates structurally on the seed, then verify only
        # the best few against real story scripts.  Checking all 256 keys on a
        # 30k-argument story is needlessly slow.
        candidate_keys: list[bytes] = []
        for key3 in key3_candidates:
            for key2 in key2_candidates:
                for key1 in key1_candidates:
                    ranked: list[tuple[int, int]] = []
                    for key0 in range(256):
                        key = bytes((key0, key1, key2, key3))
                        score = _score_key(seed_idx, key, seed_parts)
                        if score is not None:
                            ranked.append((score, key0))
                    ranked.sort(reverse=True)
                    for _, key0 in ranked[:10]:
                        key = bytes((key0, key1, key2, key3))
                        if key not in candidate_keys:
                            candidate_keys.append(key)

        verifications: list[bytes] = []
        for entry in candidates:
            if not (20_000 <= entry.unpacked_size <= 180_000):
                continue
            try:
                blob = self.read_entry(entry)
            except ArchiveError:
                continue
            parts = _ystb_parts(blob)
            if parts is not None and len(parts[2]) >= 3500 and parts[0] == seed_parts[0]:
                verifications.append(blob)
            if len(verifications) >= 6:
                break

        for blob in verifications:
            best: tuple[int, bytes] | None = None
            for key in candidate_keys:
                score = _score_story_key(blob, key)
                if score is None or score < 200:
                    continue
                if best is None or score > best[0]:
                    best = (score, key)
            if best is not None:
                return best[1]

        best = None
        for key in candidate_keys:
            score = _score_key(seed_idx, key, seed_parts)
            if score is not None and (best is None or score > best[0]):
                best = (score, key)
        if best is not None and best[0] >= 20:
            return best[1]
        # Fall back to the old structural scan for small archives.
        key = _guess_key_from_ystb(seed_raw) if seed_raw is not None else None
        if key is not None:
            return key
        raise ArchiveError(
            "无法自动恢复 YSTB 密钥。可以用 --key 指定 8 位十六进制密钥，"
            "或把游戏 EXE/完整安装目录提供给维护者分析。"
        )

    def list_entries(self) -> list[dict[str, object]]:
        return [
            {
                "name": entry.name,
                "type": entry.file_type,
                "packed": entry.packed,
                "size": entry.size,
                "unpacked_size": entry.unpacked_size,
            }
            for entry in self.entries
        ]


def _ystb_parts(blob: bytes) -> tuple[int, bytes, bytes, bytes] | None:
    if len(blob) < 0x20 or blob[:4] != b"YSTB":
        return None
    _, version, inst_count, inst_size, args_idx_size, args_data_size, _, _ = struct.unpack_from(
        "<4s7I", blob, 0
    )
    if version < 300:
        return None
    if inst_size != inst_count * 4 or args_idx_size % 12:
        return None
    inst_start = 0x20
    args_idx_start = inst_start + inst_size
    args_data_start = args_idx_start + args_idx_size
    if args_data_start + args_data_size > len(blob):
        return None
    return (
        version,
        blob[inst_start:args_idx_start],
        blob[args_idx_start:args_data_start],
        blob[args_data_start:args_data_start + args_data_size],
    )


def _guess_key_from_ystb(blob: bytes) -> bytes | None:
    parts = _ystb_parts(blob)
    if parts is None:
        return None
    _, inst_raw, idx_raw, _ = parts
    if len(inst_raw) < 8 or len(idx_raw) < 24:
        return None
    key3_candidates = [byte for byte, _ in Counter(inst_raw[3::4]).most_common(2)] or [0]
    key2_candidates = [byte for byte, _ in Counter(inst_raw[2::4]).most_common(2)] or [0]
    key1_candidates: list[int] = []
    for counts in (Counter(idx_raw[1::12]), Counter(idx_raw[5::12])):
        for byte, _ in counts.most_common(2):
            if byte not in key1_candidates:
                key1_candidates.append(byte)
    if not key1_candidates:
        key1_candidates = [0]

    best: tuple[int, bytes] | None = None
    for key3 in key3_candidates:
        for key2 in key2_candidates:
            for key1 in key1_candidates:
                for key0 in range(256):
                    key = bytes((key0, key1, key2, key3))
                    score = _score_key(idx_raw, key, parts)
                    if score is None:
                        continue
                    if best is None or score > best[0]:
                        best = (score, key)
    if best is None or best[0] < 20:
        return None
    return best[1]


def _score_story_key(blob: bytes, key: bytes) -> int | None:
    """Score a key by how many strict text arguments it yields in a story."""
    parts = _ystb_parts(blob)
    if parts is None:
        return None
    _, _, idx_raw, args_raw = parts
    idx = _xor(idx_raw, key)
    args = _xor(args_raw, key)
    lines = 0
    jp = 0
    for offset in range(0, len(idx) - 11, 12):
        _, arg_type, size, arg_offset = struct.unpack_from("<HHII", idx, offset)
        if arg_type not in (0, 3) or size < 2 or size > 4000 or arg_offset + size > len(args):
            continue
        text = _text_from_arg(args[arg_offset:arg_offset + size], arg_type)
        if text is None:
            continue
        lines += 1
        jp += len(_JP_RE.findall(text))
    return lines * 10 + min(jp, 1000)


def _score_key(idx_raw: bytes, key: bytes, parts: tuple[int, bytes, bytes, bytes]) -> int | None:
    _, _, _, args = parts
    decoded = _xor(idx_raw, key)
    if len(decoded) < 12:
        return None
    valid = 0
    text_chars = 0
    text_seen = 0
    total = len(decoded) // 12
    step = max(1, total // 300)
    for entry_no, offset in enumerate(range(0, total * 12, 12)):
        _, arg_type, size, arg_offset = struct.unpack_from("<HHII", decoded, offset)
        if arg_type not in (0, 1, 2, 3):
            continue
        if size > len(args) or arg_offset + size > len(args):
            continue
        valid += 1
        if arg_type in (0, 3) and 4 <= size <= 2000 and entry_no % step == 0:
            raw = args[arg_offset:arg_offset + size]
            if arg_type == 3:
                if raw[3:4] != b'"':
                    continue
                raw = raw[4:]
            if any(byte < 0x20 and byte not in (9, 10, 13) for byte in raw):
                continue
            try:
                text = raw.decode("cp932")
            except UnicodeDecodeError:
                continue
            text_chars += len(_JP_RE.findall(text))
            text_seen += 1
    if total and valid < total * 0.95:
        return None
    return valid * 2 + min(text_chars, 500) * 4 + min(text_seen, 30)


def _text_from_arg(raw: bytes, arg_type: int) -> str | None:
    if arg_type == 3:
        if len(raw) < 4 or raw[3:4] != b'"':
            return None
        raw = raw[4:]
    if any(byte < 0x20 and byte not in (9, 10, 13) for byte in raw):
        return None
    try:
        text = raw.decode("cp932")
    except UnicodeDecodeError:
        return None
    text = _CONTROL_RE.sub("", text).replace("\ufeff", "").strip()
    if len(text) < 2 or _PATH_RE.search(text):
        return None
    if '"' in text:
        return None
    jp = len(_JP_RE.findall(text))
    if jp < 2:
        return None
    ascii_count = len(_ASCII_RE.findall(text))
    if ascii_count > len(text) * 0.45:
        return None
    if any(char in "$%\\{}[]<>" for char in text):
        return None
    return text


def ystb_text(blob: bytes, key: bytes) -> YstbText:
    """Decrypt a YSTB V5 script and return dialogue/narration lines."""
    parts = _ystb_parts(blob)
    if parts is None:
        raise UnsupportedArchive(
            "这个 .ybn 不是受支持的 YSTB V5 脚本（可能是 YSTB V2、YSCF 或资源文件）。"
        )
    version, inst_raw, idx_raw, args_raw = parts
    inst = _xor(inst_raw, key)
    idx = _xor(idx_raw, key)
    args = _xor(args_raw, key)
    chunks: list[str] = []
    for offset in range(0, len(idx) - 11, 12):
        _, arg_type, size, arg_offset = struct.unpack_from("<HHII", idx, offset)
        if arg_type not in (0, 3) or size < 2 or size > 8000 or arg_offset + size > len(args):
            continue
        text = _text_from_arg(args[arg_offset:arg_offset + size], arg_type)
        if text is None:
            continue
        for line in text.splitlines():
            line = clean_line(line)
            if line:
                chunks.append(line)

    merged: list[str] = []
    for text in chunks:
        if merged:
            previous = merged[-1]
            open_quote = previous.count("「") > previous.count("」")
            if (open_quote or not _SENTENCE_END_RE.search(previous)) and not _SPEAKER_START_RE.match(text):
                merged[-1] = previous + text
                continue
        merged.append(text)

    segments: list[Segment] = []
    for number, text in enumerate(merged, start=1):
        segment = make_segment(number, text)
        if segment is not None:
            segments.append(segment)
    return YstbText(
        segments=segments,
        header_size=0x20,
        args_entries=len(idx) // 12,
        version=version,
    )


def _safe_relative(name: str) -> Path:
    parts = [
        part
        for part in name.replace("\\", "/").split("/")
        if part not in {"", ".", ".."} and not part.endswith(":")
    ]
    return Path(*parts) if parts else Path("unnamed")


def _write_text_file(path: Path, segments: list[Segment]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for segment in segments:
        if segment.speaker:
            lines.append(f"{segment.speaker}：{segment.text}")
        elif segment.kind == "dialogue":
            # Preserve unattributed dialogue when the text file is re-indexed.
            lines.append(f"「{segment.text}」")
        else:
            lines.append(segment.text)
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def extract_ypf(
    archive: YpfArchive,
    output: str | Path,
    *,
    mode: str = "text",
    key_hex: str | None = None,
    force: bool = False,
) -> ArchiveReport:
    key: bytes | None = None
    if key_hex:
        try:
            key = bytes.fromhex(key_hex)
        except ValueError as exc:
            raise ArchiveError("--key 必须是 8 位十六进制，例如 9b093929。") from exc
        if len(key) != 4:
            raise ArchiveError("--key 必须是 4 字节（8 位十六进制）。")
    key_error: str | None = None
    if mode in {"text", "all"} and key is None:
        try:
            key = archive.guess_ystb_key()
        except ArchiveError as exc:
            if mode == "text":
                raise
            key_error = str(exc)
            mode = "raw"

    target = Path(output).expanduser()
    if target.exists() and not force:
        raise ArchiveError(f"输出目录已存在：{target}（加 --force 覆盖）")
    if target.exists() and force:
        # Only remove files this command owns; leave unrelated user files.
        for name in ("text", "raw"):
            child = target / name
            if child.exists():
                shutil.rmtree(child)
        for name in ("corpus.jsonl", "manifest.json"):
            child = target / name
            if child.exists():
                child.unlink()
    target.mkdir(parents=True, exist_ok=True)

    report = ArchiveReport(
        archive=str(archive.path),
        format=f"YU-RIS YPF v{archive.version:#x}",
        output=str(target),
        entries=len(archive.entries),
        key=key.hex() if key else "",
    )
    if key_error:
        report.errors.append(("YSTB", key_error))
    manifest_entries: list[dict[str, object]] = []
    jsonl_path = target / "corpus.jsonl"
    jsonl = jsonl_path.open("w", encoding="utf-8", newline="\n")
    try:
        for entry in archive.entries:
            record: dict[str, object] = {
                "name": entry.name,
                "size": entry.size,
                "unpacked_size": entry.unpacked_size,
            }
            try:
                blob = archive.read_entry(entry)
            except ArchiveError as exc:
                report.errors.append((entry.name, str(exc)))
                manifest_entries.append({**record, "error": str(exc)})
                continue

            if mode in {"raw", "all"}:
                raw_path = target / "raw" / _safe_relative(entry.name)
                raw_path.parent.mkdir(parents=True, exist_ok=True)
                raw_path.write_bytes(blob)
                report.raw_entries += 1

            if mode in {"text", "all"} and entry.name.casefold().endswith(".ybn"):
                if not blob.startswith(b"YSTB"):
                    manifest_entries.append({**record, "skipped": "not YSTB"})
                    continue
                try:
                    assert key is not None
                    decoded = ystb_text(blob, key)
                except ArchiveError as exc:
                    report.errors.append((entry.name, str(exc)))
                    manifest_entries.append({**record, "error": str(exc)})
                    continue
                if decoded.segments:
                    text_path = target / "text" / _safe_relative(entry.name).with_suffix(".txt")
                    _write_text_file(text_path, decoded.segments)
                    report.text_entries += 1
                    report.text_segments += len(decoded.segments)
                    report.chars += sum(len(segment.text) for segment in decoded.segments)
                    for segment in decoded.segments:
                        jsonl.write(
                            json.dumps(
                                {
                                    "entry": entry.name,
                                    "line": segment.line_no,
                                    "kind": segment.kind,
                                    "speaker": segment.speaker,
                                    "text": segment.text,
                                },
                                ensure_ascii=False,
                                separators=(",", ":"),
                            )
                            + "\n"
                        )
                    record.update(
                        {
                            "text_file": str(text_path.relative_to(target)),
                            "segments": len(decoded.segments),
                            "version": decoded.version,
                        }
                    )
            manifest_entries.append(record)
    finally:
        jsonl.close()

    manifest = {
        "archive": str(archive.path),
        "format": report.format,
        "version": archive.version,
        "key": report.key,
        "entries": len(archive.entries),
        "text_entries": report.text_entries,
        "text_segments": report.text_segments,
        "chars": report.chars,
        "raw_entries": report.raw_entries,
        "errors": report.errors,
        "files": manifest_entries,
    }
    (target / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report
