"""SQLite metadata store plus FTS5 index."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .models import IndexReport, ParsedDocument
from .parsers import ARCHIVE_EXTENSIONS, discover_archives, discover_files, parse_file
from .text import normalize_for_search

SCHEMA_VERSION = 1
DEFAULT_DB = Path(".textlens") / "index.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL,
    path_key TEXT NOT NULL UNIQUE,
    filename TEXT NOT NULL,
    ext TEXT NOT NULL,
    title TEXT,
    parser TEXT,
    language TEXT,
    encoding TEXT,
    size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    sha1 TEXT NOT NULL,
    char_count INTEGER NOT NULL DEFAULT 0,
    dialogue_count INTEGER NOT NULL DEFAULT 0,
    segment_count INTEGER NOT NULL DEFAULT 0,
    indexed_at TEXT NOT NULL,
    metadata_json TEXT
);

CREATE INDEX IF NOT EXISTS documents_ext_idx ON documents(ext);
CREATE INDEX IF NOT EXISTS documents_language_idx ON documents(language);

CREATE TABLE IF NOT EXISTS segments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    line_no INTEGER NOT NULL,
    kind TEXT NOT NULL,
    speaker TEXT,
    text TEXT NOT NULL,
    norm_text TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS segments_doc_line_idx ON segments(doc_id, line_no);
CREATE INDEX IF NOT EXISTS segments_speaker_idx ON segments(speaker);
"""

_FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS segments_fts USING fts5(
    norm_text,
    content='segments',
    content_rowid='id',
    tokenize='trigram'
);

CREATE TRIGGER IF NOT EXISTS segments_ai AFTER INSERT ON segments BEGIN
    INSERT INTO segments_fts(rowid, norm_text) VALUES (new.id, new.norm_text);
END;

CREATE TRIGGER IF NOT EXISTS segments_ad AFTER DELETE ON segments BEGIN
    INSERT INTO segments_fts(segments_fts, rowid, norm_text)
    VALUES ('delete', old.id, old.norm_text);
END;

CREATE TRIGGER IF NOT EXISTS segments_au AFTER UPDATE ON segments BEGIN
    INSERT INTO segments_fts(segments_fts, rowid, norm_text)
    VALUES ('delete', old.id, old.norm_text);
    INSERT INTO segments_fts(rowid, norm_text) VALUES (new.id, new.norm_text);
END;
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha1_file(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class Index:
    """A small wrapper around the SQLite corpus database."""

    def __init__(self, db_path: str | Path = DEFAULT_DB) -> None:
        self.path = Path(db_path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(self.path), timeout=30)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.execute("PRAGMA synchronous = NORMAL")
        self.fts_enabled = False
        self._ensure_schema()

    def __enter__(self) -> "Index":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self.connection.close()

    # -- schema ---------------------------------------------------------

    def _ensure_schema(self) -> None:
        self.connection.executescript(_SCHEMA)
        try:
            self.connection.executescript(_FTS_SCHEMA)
            self.fts_enabled = True
            tokenizer = "trigram"
        except sqlite3.OperationalError:
            # Extremely old SQLite builds: LIKE search remains available.
            self.fts_enabled = False
            tokenizer = "none"
        current = self.get_meta("schema_version")
        if current is None:
            self.set_meta("schema_version", str(SCHEMA_VERSION))
            self.set_meta("fts_tokenizer", tokenizer)
        elif int(current) > SCHEMA_VERSION:
            raise RuntimeError(
                f"索引版本 {current} 高于当前程序支持的 {SCHEMA_VERSION}，请升级 TextLens。"
            )
        self.connection.commit()

    # -- metadata -------------------------------------------------------

    def get_meta(self, key: str) -> str | None:
        row = self.connection.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.connection.execute(
            "INSERT INTO meta(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    # -- writes ---------------------------------------------------------

    def _delete_document(self, doc_id: int) -> None:
        # Delete children explicitly so the FTS delete trigger always fires,
        # including on SQLite builds with unusual foreign-key settings.
        self.connection.execute("DELETE FROM segments WHERE doc_id = ?", (doc_id,))
        self.connection.execute("DELETE FROM documents WHERE id = ?", (doc_id,))

    def clear(self) -> None:
        with self.connection:
            self.connection.execute("DELETE FROM segments")
            self.connection.execute("DELETE FROM documents")
        if self.fts_enabled:
            self.connection.execute("INSERT INTO segments_fts(segments_fts) VALUES ('rebuild')")
        self.connection.commit()

    def remove_document(self, doc_id: int) -> None:
        with self.connection:
            self._delete_document(doc_id)
        self.connection.commit()

    def _existing(self, path_key: str) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT id, size, mtime_ns, sha1 FROM documents WHERE path_key = ?",
            (path_key,),
        ).fetchone()

    def upsert(self, document: ParsedDocument, *, force: bool = False) -> str:
        """Insert or replace one document. Returns ``indexed`` or ``skipped``."""
        path = document.path.resolve()
        try:
            stat = path.stat()
        except OSError as exc:
            raise OSError(f"无法读取文件信息：{exc}") from exc
        path_key = os.path.normcase(str(path))
        existing = self._existing(path_key)

        if existing and not force and existing["size"] == stat.st_size and existing["mtime_ns"] == stat.st_mtime_ns:
            return "skipped"

        digest = sha1_file(path)
        if existing and not force and existing["sha1"] == digest:
            self.connection.execute(
                "UPDATE documents SET size = ?, mtime_ns = ?, indexed_at = ? WHERE id = ?",
                (stat.st_size, stat.st_mtime_ns, utc_now(), existing["id"]),
            )
            self.connection.commit()
            return "skipped"

        with self.connection:
            if existing:
                self._delete_document(existing["id"])
            cursor = self.connection.execute(
                """
                INSERT INTO documents (
                    path, path_key, filename, ext, title, parser, language, encoding,
                    size, mtime_ns, sha1, char_count, dialogue_count, segment_count,
                    indexed_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(path),
                    path_key,
                    path.name,
                    path.suffix.casefold(),
                    document.title,
                    document.parser,
                    document.language,
                    document.encoding,
                    stat.st_size,
                    stat.st_mtime_ns,
                    digest,
                    document.char_count,
                    document.dialogue_count,
                    len(document.segments),
                    utc_now(),
                    json.dumps(document.metadata, ensure_ascii=False),
                ),
            )
            doc_id = int(cursor.lastrowid)
            rows = []
            for segment in document.segments:
                # Include the speaker in the search text so a plain search for
                # “诗音” finds her lines, while --speaker remains an exact
                # attribution filter.
                search_text = segment.text
                if segment.speaker:
                    search_text = f"{segment.speaker} {search_text}"
                rows.append(
                    (
                        doc_id,
                        segment.line_no,
                        segment.kind,
                        segment.speaker,
                        segment.text,
                        normalize_for_search(search_text),
                    )
                )
            self.connection.executemany(
                "INSERT INTO segments(doc_id, line_no, kind, speaker, text, norm_text) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                rows,
            )
        return "indexed"

    def prune_missing(self) -> int:
        """Remove indexed documents whose source file no longer exists."""
        removed = 0
        rows = self.connection.execute("SELECT id, path FROM documents").fetchall()
        with self.connection:
            for row in rows:
                if not Path(row["path"]).is_file():
                    self._delete_document(row["id"])
                    removed += 1
        return removed

    def optimize(self) -> None:
        if self.fts_enabled:
            self.connection.execute("INSERT INTO segments_fts(segments_fts) VALUES ('optimize')")
            self.connection.commit()

    # -- reads ----------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        row = self.connection.execute(
            """
            SELECT
                COUNT(*) AS documents,
                COALESCE(SUM(segment_count), 0) AS segments,
                COALESCE(SUM(char_count), 0) AS chars,
                COALESCE(SUM(dialogue_count), 0) AS dialogues,
                COUNT(DISTINCT language) AS languages
            FROM documents
            """
        ).fetchone()
        speakers = self.connection.execute(
            "SELECT COUNT(DISTINCT speaker) FROM segments WHERE speaker IS NOT NULL AND speaker <> ''"
        ).fetchone()[0]
        return {
            "documents": int(row["documents"]),
            "segments": int(row["segments"]),
            "chars": int(row["chars"]),
            "dialogues": int(row["dialogues"]),
            "speakers": int(speakers),
            "languages": int(row["languages"]),
            "db_path": str(self.path.resolve()),
            "db_bytes": self.path.stat().st_size if self.path.exists() else 0,
            "fts_enabled": self.fts_enabled,
        }

    def documents(self) -> list[sqlite3.Row]:
        return self.connection.execute(
            "SELECT * FROM documents ORDER BY path COLLATE NOCASE"
        ).fetchall()

    def document(self, identifier: str) -> sqlite3.Row | None:
        if identifier.isdigit():
            row = self.connection.execute(
                "SELECT * FROM documents WHERE id = ?", (int(identifier),)
            ).fetchone()
            if row:
                return row
        return self.connection.execute(
            "SELECT * FROM documents WHERE path = ? OR path_key = ? OR filename = ?",
            (identifier, os.path.normcase(identifier), identifier),
        ).fetchone()

    def segments_for(self, doc_id: int, *, around: int | None = None, radius: int = 2) -> list[sqlite3.Row]:
        if around is None:
            return self.connection.execute(
                "SELECT * FROM segments WHERE doc_id = ? ORDER BY line_no, id",
                (doc_id,),
            ).fetchall()
        return self.connection.execute(
            """
            SELECT * FROM segments
            WHERE doc_id = ? AND line_no BETWEEN ? AND ?
            ORDER BY line_no, id
            """,
            (doc_id, around - radius, around + radius),
        ).fetchall()

    def segment_by_id(self, segment_id: int) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM segments WHERE id = ?", (segment_id,)
        ).fetchone()

    def iter_segments(self) -> Iterable[sqlite3.Row]:
        yield from self.connection.execute(
            "SELECT id, doc_id, line_no, kind, speaker, text FROM segments ORDER BY id"
        )


def build_index(
    db_path: str | Path,
    paths: list[str | Path],
    *,
    includes: list[str] | None = None,
    excludes: list[str] | None = None,
    force: bool = False,
    rebuild: bool = False,
    prune: bool = False,
) -> IndexReport:
    """Index every supported file under *paths*."""
    started = time.perf_counter()
    files = discover_files(paths, includes=includes, excludes=excludes)
    archives = discover_archives(paths, excludes=excludes)
    archive_keys = {
        os.path.normcase(str(path.resolve())) for path in archives
    }
    files = [
        path
        for path in files
        if os.path.normcase(str(path.resolve())) not in archive_keys
    ]
    skipped_archives = [
        (str(path), ARCHIVE_EXTENSIONS.get(path.suffix.casefold(), "游戏归档"))
        for path in archives
    ]
    report = IndexReport(seen=len(files) + len(archives), skipped_archives=skipped_archives)
    with Index(db_path) as index:
        if rebuild:
            index.clear()
        for path in files:
            try:
                document = parse_file(path)
                result = index.upsert(document, force=force)
                if result == "skipped":
                    report.skipped += 1
                else:
                    report.indexed += 1
                    report.segments += len(document.segments)
                    report.chars += document.char_count
            except Exception as exc:
                report.failed += 1
                message = str(exc) if str(exc) else type(exc).__name__
                report.errors.append((str(path), message))
        if prune:
            report.removed = index.prune_missing()
        index.optimize()
    report.duration = time.perf_counter() - started
    return report
