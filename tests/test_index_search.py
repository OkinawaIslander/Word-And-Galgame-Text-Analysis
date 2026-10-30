from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import json
import os
import unittest

from textlens.analysis import analyze
from textlens.export import extract
from textlens.index import Index, build_index
from textlens.search import context, search

from .helpers import write_text


class IndexSearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / "index.db"
        self.txt = write_text(
            self.root / "story.txt",
            "第一章\n诗音：「我们约定好了。」\n风吹过车站。\n「不要忘记。」\n",
            encoding="utf-8",
        )
        self.ks = write_text(
            self.root / "scene.ks",
            "*start\n[name text=\"诗音\"]\n[r]明天见。[p]\n",
            encoding="utf-16",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def build(self, **kwargs) -> object:
        return build_index(self.db, [self.root], includes=[".txt", ".ks"], **kwargs)

    def test_index_and_summary(self) -> None:
        report = self.build()
        self.assertEqual(report.seen, 2)
        self.assertEqual(report.indexed, 2)
        self.assertEqual(report.failed, 0)
        with Index(self.db) as index:
            summary = index.summary()
            self.assertEqual(summary["documents"], 2)
            self.assertGreater(summary["segments"], 3)
            self.assertEqual(summary["speakers"], 1)

    def test_incremental_skip(self) -> None:
        self.build()
        report = self.build()
        self.assertEqual(report.indexed, 0)
        self.assertEqual(report.skipped, 2)

    def test_search_short_cjk_uses_like(self) -> None:
        self.build()
        with Index(self.db) as index:
            hits = search(index, "约定", limit=10)
            self.assertTrue(hits)
            self.assertTrue(any("约定" in hit.text for hit in hits))

    def test_search_long_cjk_uses_fts(self) -> None:
        self.build()
        with Index(self.db) as index:
            hits = search(index, "约定好", limit=10)
            self.assertTrue(hits)
            self.assertTrue(any(hit.speaker == "诗音" for hit in hits))

    def test_search_by_speaker_name(self) -> None:
        self.build()
        with Index(self.db) as index:
            hits = search(index, "诗音", limit=20)
            self.assertGreaterEqual(len(hits), 2)
            self.assertTrue(all(hit.speaker == "诗音" for hit in hits))

    def test_search_filters(self) -> None:
        self.build()
        with Index(self.db) as index:
            hits = search(index, "明天见", extensions=[".ks"], speaker="诗音", limit=10)
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0].ext, ".ks")
            no_hits = search(index, "明天见", extensions=[".txt"], limit=10)
            self.assertEqual(no_hits, [])

    def test_search_regex_and_phrase(self) -> None:
        self.build()
        with Index(self.db) as index:
            hits = search(index, "约定.*", use_regex=True, limit=10)
            self.assertTrue(hits)
            hits = search(index, "风吹过车站", phrase=True, limit=10)
            self.assertTrue(hits)

    def test_context(self) -> None:
        self.build()
        with Index(self.db) as index:
            blocks = context(index, "约定", before=1, after=1, limit=2)
            self.assertTrue(blocks)
            self.assertTrue(any("风吹过车站" in row["text"] for row in blocks[0].segments))

    def test_stats(self) -> None:
        self.build()
        with Index(self.db) as index:
            stats = analyze(index, top=20)
            self.assertEqual(stats.totals["documents"], 2)
            self.assertEqual(stats.totals["speakers"], 1)
            self.assertTrue(stats.speakers)
            self.assertEqual(stats.speakers[0]["speaker"], "诗音")
            self.assertTrue(stats.terms)
            self.assertIn("约定", [row["term"] for row in stats.terms])

    def test_export_jsonl(self) -> None:
        output = self.root / "corpus.jsonl"
        report = extract([self.root], output, includes=[".txt"], dialogue_only=True, force=True)
        self.assertEqual(report.files, 1)
        self.assertEqual(report.errors, [])
        rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
        self.assertTrue(rows)
        self.assertTrue(all(row["kind"] == "dialogue" for row in rows))
        self.assertEqual(rows[0]["speaker"], "诗音")

    def test_prune(self) -> None:
        self.build()
        os.remove(self.ks)
        report = self.build(prune=True)
        self.assertEqual(report.removed, 1)
        with Index(self.db) as index:
            self.assertEqual(index.summary()["documents"], 1)

    def test_archive_is_skipped_not_parsed(self) -> None:
        archive = self.root / "script.arc"
        archive.write_bytes(b"ARC\x1a" + b"\x00" * 32)
        report = self.build()
        self.assertEqual(report.seen, 3)
        self.assertEqual(report.failed, 0)
        self.assertEqual(len(report.skipped_archives), 1)
        self.assertIn("ARC", report.skipped_archives[0][1])
        with Index(self.db) as index:
            self.assertEqual(index.summary()["documents"], 2)

    def test_fts_integrity_after_reindex(self) -> None:
        self.build()
        self.txt.write_text(
            "第二章\n诗音：「新的约定。」\n",
            encoding="utf-8",
        )
        self.build()
        with Index(self.db) as index:
            hits = search(index, "新的约定", limit=10)
            self.assertTrue(hits)
            index.connection.execute("INSERT INTO segments_fts(segments_fts) VALUES ('integrity-check')")


if __name__ == "__main__":
    unittest.main()
