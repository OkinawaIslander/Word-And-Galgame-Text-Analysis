from __future__ import annotations

import unittest

from textlens.text import (
    classify_line,
    clean_line,
    detect_language,
    normalize_for_search,
    snippet,
    strip_script_tags,
    valid_speaker,
)


class TextTests(unittest.TestCase):
    def test_dialogue_with_colon_and_quotes(self) -> None:
        info = classify_line("诗音：「我们约定好了。」")
        self.assertIsNotNone(info)
        assert info is not None
        self.assertEqual(info.kind, "dialogue")
        self.assertEqual(info.speaker, "诗音")
        self.assertEqual(info.text, "我们约定好了。")

    def test_dialogue_without_speaker(self) -> None:
        info = classify_line("「不要忘记。」")
        assert info is not None
        self.assertEqual(info.kind, "dialogue")
        self.assertIsNone(info.speaker)
        self.assertEqual(info.text, "不要忘记。")

    def test_narration_quote_is_not_dialogue(self) -> None:
        info = classify_line("他说：「明天见。」")
        assert info is not None
        self.assertEqual(info.kind, "narration")

    def test_prose_colon_is_not_dialogue(self) -> None:
        for line in (
            "岛村心想：已经这么冷了吗？",
            "据她说：今冬在滑雪场上，结识了一帮子男人。",
            "起先人们到处高声谈论：今年雪下得真大。",
            "还有这样一首民歌："
        ):
            info = classify_line(line)
            assert info is not None
            self.assertEqual(info.kind, "narration", line)

    def test_metadata_colon_is_not_dialogue(self) -> None:
        for line in ("作者：川端康成", "第【1】段：作品赏析"):
            info = classify_line(line)
            assert info is not None
            self.assertEqual(info.kind, "narration", line)

    def test_english_speaker(self) -> None:
        info = classify_line('Alice: "Hello there."')
        assert info is not None
        self.assertEqual(info.kind, "dialogue")
        self.assertEqual(info.speaker, "Alice")
        self.assertEqual(info.text, "Hello there.")

    def test_heading(self) -> None:
        info = classify_line("## 第一章")
        assert info is not None
        self.assertEqual(info.kind, "heading")
        self.assertEqual(info.text, "第一章")

    def test_markdown_link_is_not_stripped(self) -> None:
        info = classify_line("参考资料：[官方设定](https://example.com/lore)")
        assert info is not None
        self.assertEqual(info.kind, "narration")
        self.assertIn("[官方设定]", info.text)
        self.assertIn("https://example.com", info.text)

    def test_invalid_speakers(self) -> None:
        self.assertFalse(valid_speaker("12"))
        self.assertFalse(valid_speaker("他说"))
        self.assertFalse(valid_speaker("http"))
        self.assertTrue(valid_speaker("诗音"))

    def test_strip_kag_tags(self) -> None:
        raw = "[name text=\"诗音\"]こんにちは。[r]元気？[p]"
        self.assertEqual(strip_script_tags(raw), "こんにちは。 元気？")

    def test_normalize(self) -> None:
        self.assertEqual(normalize_for_search(" ＨＥＬＬＯ\u3000World "), "hello world")
        self.assertEqual(clean_line("  a\u200b  b  "), "a b")

    def test_language(self) -> None:
        self.assertEqual(detect_language("これは日本語の文章です。"), "ja")
        self.assertEqual(detect_language("这是一个中文句子，用来测试语言检测。"), "zh")
        self.assertEqual(detect_language("This is an English sentence."), "en")

    def test_snippet(self) -> None:
        text = "前面的内容。" * 20 + "我们约定好了。" + "后面的内容。" * 20
        result = snippet(text, ["约定"])
        self.assertIn("约定", result)
        self.assertLess(len(result), len(text))


if __name__ == "__main__":
    unittest.main()
