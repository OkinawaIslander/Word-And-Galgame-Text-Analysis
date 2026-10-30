from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from textlens.encoding import decode_bytes, read_text_file


class EncodingTests(unittest.TestCase):
    def test_utf8_is_preferred(self) -> None:
        text = "诗音：「明天见。」"
        decoded = decode_bytes(text.encode("utf-8"))
        self.assertEqual(decoded.encoding, "utf-8")
        self.assertEqual(decoded.text, text)

    def test_gb18030_chinese(self) -> None:
        text = "我们约定好了，明天在车站见面。"
        decoded = decode_bytes(text.encode("gb18030"))
        self.assertEqual(decoded.text, text)
        self.assertIn(decoded.encoding, {"gb18030", "gbk", "gb2312"})

    def test_big5_traditional_chinese(self) -> None:
        text = "我們約定好了，明天在車站見面。"
        decoded = decode_bytes(text.encode("big5"))
        self.assertEqual(decoded.text, text)
        self.assertEqual(decoded.encoding, "big5")

    def test_cp932_japanese(self) -> None:
        text = "明日、また会いましょう。約束だよ。"
        decoded = decode_bytes(text.encode("cp932"))
        self.assertEqual(decoded.text, text)
        self.assertIn(decoded.encoding, {"cp932", "shift-jis", "shift_jis", "932"})

    def test_utf16_with_bom(self) -> None:
        text = "[name text=\"詩音\"]\r\nまたね。"
        decoded = decode_bytes(text.encode("utf-16"))
        self.assertEqual(decoded.text, text)
        self.assertIn("utf-16", decoded.encoding)

    def test_read_file(self) -> None:
        with TemporaryDirectory() as temp:
            path = Path(temp) / "sample.txt"
            path.write_bytes("第一章\n雨が降っている。".encode("cp932"))
            decoded = read_text_file(path)
            self.assertIn("雨が降っている", decoded.text)


if __name__ == "__main__":
    unittest.main()
