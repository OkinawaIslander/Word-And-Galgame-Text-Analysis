from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from textlens.cli import main

from .helpers import write_text
from .test_archives import make_ypf


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / "index.db"
        write_text(
            self.root / "story.txt",
            "诗音：「我们约定好了。」\n风吹过车站。\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_cli(self, *args: str) -> tuple[int, str, str]:
        out = StringIO()
        err = StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(list(args))
        return code, out.getvalue(), err.getvalue()

    def test_index_search_stats(self) -> None:
        code, out, err = self.run_cli("index", str(self.root), "--db", str(self.db), "--json")
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual(payload["indexed"], 1)

        code, out, err = self.run_cli(
            "search", "约定", "--db", str(self.db), "--json"
        )
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["results"][0]["speaker"], "诗音")

        code, out, err = self.run_cli(
            "stats", "--db", str(self.db), "--format", "json"
        )
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual(payload["totals"]["documents"], 1)
        self.assertEqual(payload["speakers"][0]["speaker"], "诗音")

    def test_missing_index_is_friendly(self) -> None:
        code, _out, err = self.run_cli(
            "search", "约定", "--db", str(self.root / "missing.db")
        )
        self.assertEqual(code, 2)
        self.assertIn("索引不存在", err)

    def test_context_and_files(self) -> None:
        self.run_cli("index", str(self.root), "--db", str(self.db))
        code, out, err = self.run_cli(
            "context", "约定", "--db", str(self.db), "--before", "1", "--after", "1"
        )
        self.assertEqual(code, 0, err)
        self.assertIn("风吹过车站", out)

        code, out, err = self.run_cli("files", "--db", str(self.db), "--json")
        self.assertEqual(code, 0, err)
        self.assertEqual(len(json.loads(out)), 1)

    def test_archive_only_index_is_actionable(self) -> None:
        archive_dir = self.root / "archives"
        archive_dir.mkdir()
        (archive_dir / "script.arc").write_bytes(b"ARC\x1a" + b"\x00" * 32)
        code, out, err = self.run_cli("index", str(archive_dir), "--db", str(self.db))
        self.assertEqual(code, 1, err)
        self.assertIn("归档包", out)
        self.assertIn("待开发", out)

    def test_unpack_ypf(self) -> None:
        archive = self.root / "game.ypf"
        key = make_ypf(archive)
        output = self.root / "unpacked"
        code, out, err = self.run_cli(
            "unpack",
            str(archive),
            "-o",
            str(output),
            "--key",
            key.hex(),
            "--force",
        )
        self.assertEqual(code, 0, err)
        self.assertIn("解包完成", out)
        self.assertTrue((output / "text" / "test.txt").exists())

    def test_extract(self) -> None:
        output = self.root / "corpus.md"
        code, out, err = self.run_cli(
            "extract", str(self.root), "-o", str(output), "--format", "md"
        )
        self.assertEqual(code, 0, err)
        self.assertTrue(output.exists())
        self.assertIn("诗音", output.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
