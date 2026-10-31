from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from textlens.parsers import parse_file
from textlens.parsers.engines import parse_kirikiri, parse_nscripter, parse_renpy
from textlens.parsers.plain import parse_ass, parse_csv, parse_json, parse_srt, parse_tjs

from .helpers import make_docx, write_text


class ParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_docx_body_table_and_footnote(self) -> None:
        path = make_docx(
            self.root / "doc.docx",
            ["第一章", "诗音：「我们约定好了。」", "风吹过车站。"],
            heading_indices={0},
            table=[["角色", "台词"], ["诗音", "明天见"]],
            footnote="译者注：此处为双关。",
        )
        document = parse_file(path)
        self.assertEqual(document.title, "测试文档")
        self.assertEqual(document.parser, "docx")
        kinds = [segment.kind for segment in document.segments]
        self.assertEqual(kinds[0], "heading")
        self.assertIn("dialogue", kinds)
        self.assertIn("table", kinds)
        self.assertIn("footnote", kinds)
        dialogue = next(segment for segment in document.segments if segment.kind == "dialogue")
        self.assertEqual(dialogue.speaker, "诗音")
        self.assertEqual(dialogue.text, "我们约定好了。")
        table = next(segment for segment in document.segments if segment.kind == "table")
        self.assertTrue(any("明天见" in segment.text for segment in document.segments if segment.kind == "table"))
        self.assertIn("|", table.text)

    def test_kirikiri(self) -> None:
        path = write_text(
            self.root / "scene.ks",
            "*start\n; comment\n[name text=\"诗音\"]\n[r]我们约定好了。[p]\n"
            "[ruby text=\"やくそく\"]約束[/ruby]だよ。\n",
            encoding="utf-16",
        )
        document = parse_kirikiri(path)
        self.assertEqual(document.parser, "kirikiri")
        self.assertEqual(document.segments[0].kind, "dialogue")
        self.assertEqual(document.segments[0].speaker, "诗音")
        self.assertEqual(document.segments[0].text, "我们约定好了。")
        self.assertIn("約束だよ", document.text)
        self.assertNotIn("[ruby", document.text)

    def test_nscripter(self) -> None:
        path = write_text(
            self.root / "scene.scn",
            '*define\nbg "black",1\n; comment\n*start\n詩音「また明日。」\\n私はうなずいた。\n',
            encoding="cp932",
        )
        document = parse_nscripter(path)
        self.assertTrue(any(segment.speaker == "詩音" for segment in document.segments))
        self.assertTrue(any("私はうなずいた" in segment.text for segment in document.segments))
        self.assertFalse(any(segment.text.startswith("bg ") for segment in document.segments))

        named = write_text(
            self.root / "named.scn",
            'name "詩音"\nまた明日。\n',
            encoding="cp932",
        )
        named_doc = parse_nscripter(named)
        self.assertEqual(named_doc.segments[0].speaker, "詩音")
        self.assertEqual(named_doc.segments[0].kind, "dialogue")

    def test_renpy(self) -> None:
        path = write_text(
            self.root / "script.rpy",
            '''
define e = Character("Eileen", color="#c8ffc8")
define narrator = Character(None)

label start:
    scene bg room
    e "Hello, [name]!"
    "A quiet room."
    menu:
        "Stay here":
            e "Okay."
''',
            encoding="utf-8",
        )
        document = parse_renpy(path)
        dialogue = [segment for segment in document.segments if segment.kind == "dialogue"]
        self.assertEqual(dialogue[0].speaker, "Eileen")
        self.assertIn("Hello", dialogue[0].text)
        self.assertTrue(any(segment.kind == "choice" and "Stay here" in segment.text for segment in document.segments))
        self.assertFalse(any("scene bg" in segment.text for segment in document.segments))

    def test_srt_and_ass(self) -> None:
        srt = write_text(
            self.root / "sub.srt",
            "1\n00:00:01,000 --> 00:00:03,000\n诗音：我们约定好了。\n\n"
            "2\n00:00:04,000 --> 00:00:06,000\n「不要忘记。」\n",
            encoding="utf-8",
        )
        document = parse_srt(srt)
        self.assertEqual(document.segments[0].speaker, "诗音")
        self.assertEqual(document.segments[1].kind, "dialogue")

        ass = write_text(
            self.root / "sub.ass",
            "[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
            "Dialogue: 0,0:00:01.00,0:00:03.00,Default,诗音,0,0,0,,{\\i1}明天见。{\\i0}\n",
            encoding="utf-8",
        )
        ass_doc = parse_ass(ass)
        self.assertEqual(ass_doc.segments[0].speaker, "诗音")
        self.assertEqual(ass_doc.segments[0].text, "明天见。")

    def test_json_csv_and_tjs(self) -> None:
        json_path = write_text(
            self.root / "lines.json",
            '[{"speaker": "诗音", "text": "约定好了。"}, {"text": "风吹过。"}]',
            encoding="utf-8",
        )
        json_doc = parse_json(json_path)
        self.assertEqual(json_doc.segments[0].speaker, "诗音")
        self.assertEqual(json_doc.segments[0].kind, "dialogue")

        csv_path = write_text(
            self.root / "lines.csv",
            "speaker,text\n诗音,明天见。\n我,嗯。\n",
            encoding="utf-8",
        )
        csv_doc = parse_csv(csv_path)
        self.assertEqual(csv_doc.segments[0].speaker, "诗音")
        self.assertEqual(csv_doc.segments[0].text, "明天见。")

        tjs_path = write_text(
            self.root / "scene.tjs",
            'var path = "bg/room.png";\nvar msg = "また明日。";\n',
            encoding="utf-8",
        )
        tjs_doc = parse_tjs(tjs_path)
        self.assertEqual(len(tjs_doc.segments), 1)
        self.assertIn("また明日", tjs_doc.segments[0].text)

    def test_legacy_doc_is_actionable(self) -> None:
        path = self.root / "old.doc"
        path.write_bytes(b"\xd0\xcf\x11\xe0")
        with self.assertRaises(Exception) as context:
            parse_file(path)
        self.assertIn("docx", str(context.exception))


if __name__ == "__main__":
    unittest.main()
