from __future__ import annotations

import json
import struct
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from textlens.archives import open_archive
from textlens.archives.ypf import extract_ypf, ystb_text


def _xor(data: bytes, key: bytes) -> bytes:
    return bytes(value ^ key[index & 3] for index, value in enumerate(data))


def make_ypf(path: Path, text: str = "テスト「こんにちは」") -> bytes:
    key = b"\x11\x22\x33\x44"
    encoded = text.encode("cp932")
    # YSTB V5: header, one instruction, one type-0 argument.
    inst = _xor(b"\x00\x00\x00\x00", key)
    idx = _xor(struct.pack("<HHII", 1, 0, len(encoded), 0), key)
    args = _xor(encoded, key)
    ystb = struct.pack("<4s7I", b"YSTB", 555, 1, 4, 12, len(encoded), 0, 0) + inst + idx + args

    name = b"test.ybn"
    name_key = 0x5A
    enc_name = bytes(value ^ name_key for value in name)
    enc_len = len(name) ^ 0xFF
    entry_size = 5 + len(name) + 0x12 + 4
    data_offset = 0x20 + entry_size
    header = struct.pack("<4sIII", b"YPF\0", 0x1F4, 1, entry_size) + b"\0" * 16
    entry = (
        struct.pack("<IB", 0, enc_len)
        + enc_name
        + struct.pack("<BBIIII", 0, 0, len(ystb), len(ystb), data_offset, 0)
        + b"\0" * 4
    )
    path.write_bytes(header + entry + ystb)
    return key


class ArchiveTests(unittest.TestCase):
    def test_ypf_list_and_extract(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            archive_path = root / "game.ypf"
            key = make_ypf(archive_path)
            archive = open_archive(archive_path)
            self.assertEqual(len(archive.entries), 1)
            self.assertEqual(archive.entries[0].name, "test.ybn")
            self.assertEqual(archive.entries[0].unpacked_size > 32, True)

            decoded = ystb_text(archive.read_entry(archive.entries[0]), key)
            self.assertEqual(len(decoded.segments), 1)
            self.assertEqual(decoded.segments[0].speaker, "テスト")
            self.assertEqual(decoded.segments[0].kind, "dialogue")

            output = root / "out"
            report = extract_ypf(
                archive,
                output,
                key_hex=key.hex(),
                force=True,
            )
            self.assertEqual(report.text_entries, 1)
            self.assertEqual(report.text_segments, 1)
            self.assertTrue((output / "corpus.jsonl").exists())
            record = json.loads((output / "corpus.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(record["speaker"], "テスト")
            self.assertIn("こんにちは", record["text"])
            text_file = output / "text" / "test.txt"
            self.assertEqual(text_file.read_text(encoding="utf-8").strip(), "テスト：こんにちは")

    def test_non_ypf_is_reported(self) -> None:
        with TemporaryDirectory() as temp:
            path = Path(temp) / "unknown.dat"
            path.write_bytes(b"\xd5\x50\x96\x00" + b"\0" * 64)
            with self.assertRaises(Exception) as context:
                open_archive(path)
            self.assertIn(".DAT", str(context.exception))


if __name__ == "__main__":
    unittest.main()
