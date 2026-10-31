"""Full-text search with a LIKE fallback for short CJK queries."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

from .errors import TextLensError
from .index import Index
from .models import SearchHit
from .parsers import normalize_extension
from .text import normalize_for_search


class SearchError(TextLensError):
    pass


@dataclass(slots=True)
class ContextBlock:
    hit: SearchHit
    segments: list[sqlite3.Row]


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _filters(
    *,
    extensions: list[str] | None = None,
    speaker: str | None = None,
    file_pattern: str | None = None,
    kind: str | None = None,
) -> tuple[str, list[object]]:
    clauses = ["1 = 1"]
    params: list[object] = []
    if extensions:
        normalized = sorted({normalize_extension(ext) for ext in extensions if ext.strip()})
        if normalized:
            placeholders = ",".join("?" for _ in normalized)
            clauses.append(f"d.ext IN ({placeholders})")
            params.extend(normalized)
    if speaker:
        clauses.append("s.speaker LIKE ? ESCAPE '\\'")
        params.append(f"%{_escape_like(speaker)}%")
    if file_pattern:
        clauses.append("(d.path LIKE ? ESCAPE '\\' OR d.filename LIKE ? ESCAPE '\\')")
        needle = f"%{_escape_like(file_pattern)}%"
        params.extend([needle, needle])
    if kind:
        clauses.append("s.kind = ?")
        params.append(kind)
    return " AND ".join(clauses), params


def _row_to_hit(row: sqlite3.Row) -> SearchHit:
    rank = row["rank"] if "rank" in row.keys() else 0.0
    return SearchHit(
        doc_id=int(row["doc_id"]),
        path=row["path"],
        title=row["title"] or "",
        ext=row["ext"],
        line_no=int(row["line_no"]),
        kind=row["kind"],
        speaker=row["speaker"],
        text=row["text"],
        rank=float(rank or 0.0),
    )


def _search_like(
    index: Index,
    terms: list[str],
    *,
    any_term: bool,
    limit: int,
    offset: int,
    where: str,
    params: list[object],
) -> list[SearchHit]:
    op = " OR " if any_term else " AND "
    conditions = []
    like_params: list[object] = []
    for term in terms:
        conditions.append("s.norm_text LIKE ? ESCAPE '\\'")
        like_params.append(f"%{_escape_like(term)}%")
    clause = f"({op.join(conditions)})" if conditions else "1 = 0"
    sql = f"""
        SELECT s.doc_id, s.line_no, s.kind, s.speaker, s.text,
               d.path, d.title, d.ext
        FROM segments s
        JOIN documents d ON d.id = s.doc_id
        WHERE {where} AND {clause}
        ORDER BY d.path COLLATE NOCASE, s.line_no, s.id
        LIMIT ? OFFSET ?
    """
    rows = index.connection.execute(
        sql, [*params, *like_params, limit, offset]
    ).fetchall()
    return [_row_to_hit(row) for row in rows]


def _search_regex(
    index: Index,
    pattern: str,
    *,
    limit: int,
    offset: int,
    where: str,
    params: list[object],
) -> list[SearchHit]:
    try:
        compiled = re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        raise SearchError(f"正则表达式无效：{exc}") from exc
    sql = f"""
        SELECT s.doc_id, s.line_no, s.kind, s.speaker, s.text,
               d.path, d.title, d.ext
        FROM segments s
        JOIN documents d ON d.id = s.doc_id
        WHERE {where}
        ORDER BY d.path COLLATE NOCASE, s.line_no, s.id
    """
    hits: list[SearchHit] = []
    matched = 0
    for row in index.connection.execute(sql, params):
        if not compiled.search(row["text"]):
            continue
        matched += 1
        if matched <= offset:
            continue
        hits.append(_row_to_hit(row))
        if len(hits) >= limit:
            break
    return hits


def search(
    index: Index,
    query: str,
    *,
    limit: int = 20,
    offset: int = 0,
    extensions: list[str] | None = None,
    speaker: str | None = None,
    file_pattern: str | None = None,
    kind: str | None = None,
    any_term: bool = False,
    phrase: bool = False,
    use_regex: bool = False,
) -> list[SearchHit]:
    """Search indexed segments.

    CJK terms shorter than three characters cannot use the trigram tokenizer,
    so those queries transparently fall back to a normalized LIKE scan.
    """
    query = query.strip()
    if not query:
        return []
    if limit <= 0:
        return []
    where, params = _filters(
        extensions=extensions,
        speaker=speaker,
        file_pattern=file_pattern,
        kind=kind,
    )

    if use_regex:
        return _search_regex(
            index, query, limit=limit, offset=offset, where=where, params=params
        )

    normalized = normalize_for_search(query)
    if not normalized:
        return []
    terms = [normalized] if phrase else normalized.split()
    if not terms:
        return []

    can_use_fts = (
        index.fts_enabled
        and all(len(term) >= 3 and any(ch.isalnum() for ch in term) for term in terms)
    )
    if can_use_fts:
        def quote(term: str) -> str:
            return '"' + term.replace('"', '""') + '"'

        operator = " OR " if any_term else " AND "
        expression = operator.join(quote(term) for term in terms)
        sql = f"""
            SELECT s.doc_id, s.line_no, s.kind, s.speaker, s.text,
                   d.path, d.title, d.ext,
                   bm25(segments_fts) AS rank
            FROM segments_fts
            JOIN segments s ON s.id = segments_fts.rowid
            JOIN documents d ON d.id = s.doc_id
            WHERE segments_fts MATCH ? AND {where}
            ORDER BY rank, d.path COLLATE NOCASE, s.line_no
            LIMIT ? OFFSET ?
        """
        try:
            rows = index.connection.execute(
                sql, [expression, *params, limit, offset]
            ).fetchall()
            return [_row_to_hit(row) for row in rows]
        except sqlite3.OperationalError:
            # A damaged/absent FTS table should still leave search usable.
            pass

    return _search_like(
        index,
        terms,
        any_term=any_term,
        limit=limit,
        offset=offset,
        where=where,
        params=params,
    )


def context(
    index: Index,
    query: str,
    *,
    before: int = 2,
    after: int = 2,
    limit: int = 5,
    extensions: list[str] | None = None,
    speaker: str | None = None,
    file_pattern: str | None = None,
    kind: str | None = None,
    any_term: bool = False,
    phrase: bool = False,
) -> list[ContextBlock]:
    """Return nearby segments for the top search hits, de-duplicating windows."""
    hits = search(
        index,
        query,
        limit=limit,
        extensions=extensions,
        speaker=speaker,
        file_pattern=file_pattern,
        kind=kind,
        any_term=any_term,
        phrase=phrase,
    )
    blocks: list[ContextBlock] = []
    for hit in hits:
        rows = index.connection.execute(
            """
            SELECT * FROM segments
            WHERE doc_id = ? AND line_no BETWEEN ? AND ?
            ORDER BY line_no, id
            """,
            (hit.doc_id, hit.line_no - max(0, before), hit.line_no + max(0, after)),
        ).fetchall()
        # Skip a hit whose window is already covered by the previous block.
        if blocks:
            previous = blocks[-1]
            if (
                previous.hit.doc_id == hit.doc_id
                and abs(previous.hit.line_no - hit.line_no) <= before + after
            ):
                continue
        blocks.append(ContextBlock(hit=hit, segments=rows))
    return blocks
