"""Corpus statistics and keyword analysis."""

from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable

from .index import Index
from .parsers import normalize_extension
from .text import pad_display

try:  # optional, but strongly recommended for Chinese corpora
    import jieba as _jieba

    try:
        _jieba.setLogLevel(logging.ERROR)
    except Exception:
        pass
except Exception:  # pragma: no cover - environment dependent
    _jieba = None


_ZH_STOPWORDS = {
    "的", "了", "是", "我", "你", "他", "她", "它", "我们", "你们", "他们", "她们",
    "这", "那", "这个", "那个", "这些", "那些", "一个", "没有", "什么", "怎么",
    "因为", "所以", "但是", "可是", "不过", "如果", "虽然", "然后", "于是", "而且",
    "就", "都", "也", "还", "又", "很", "太", "在", "有", "和", "与", "及", "或",
    "而", "被", "把", "给", "让", "对", "从", "到", "说", "道", "着", "过", "地",
    "得", "吗", "呢", "吧", "啊", "呀", "哦", "嗯", "哈", "嘛", "啦", "么", "自己",
    "这样", "那样", "知道", "觉得", "现在", "已经", "可以", "应该", "只是", "还是",
    "就是", "不是", "这里", "那里", "时候", "一样", "一直", "一起", "不会", "不能",
    "没有", "真的", "似乎", "仿佛", "一样", "这种", "那种", "东西", "事情", "地方",
}
_EN_STOPWORDS = {
    "the", "and", "or", "but", "if", "then", "than", "that", "this", "these", "those",
    "a", "an", "of", "to", "in", "on", "at", "for", "from", "by", "with", "as", "is",
    "are", "was", "were", "be", "been", "being", "it", "its", "i", "you", "he", "she",
    "we", "they", "me", "him", "her", "us", "them", "my", "your", "his", "their",
    "our", "do", "does", "did", "have", "has", "had", "not", "no", "so", "just",
    "can", "could", "would", "should", "will", "shall", "may", "might", "must",
    "there", "here", "what", "which", "who", "whom", "when", "where", "why", "how",
}
_JA_STOPWORDS = {
    "これ", "それ", "あれ", "この", "その", "あの", "ここ", "そこ", "あそこ",
    "わたし", "あなた", "かれ", "かのじょ", "たち", "です", "ます", "でした",
    "ました", "ません", "から", "まで", "より", "けど", "でも", "そして",
    "しかし", "だから", "そう", "どう", "なに", "何", "こと", "もの", "ため",
}

_ASCII_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9'’-]{1,}")
_JA_TOKEN_RE = re.compile(r"[\u4e00-\u9fff々〆ヶ]{2,12}|[\u30a1-\u30f6ー]{2,}")
_KO_TOKEN_RE = re.compile(r"[\uac00-\ud7af]{2,}")
_TOKEN_CLEAN_RE = re.compile(r"^[\W_]+|[\W_]+$", re.UNICODE)


def _extra_tokens(text: str) -> Iterable[str]:
    yield from _ASCII_WORD_RE.findall(text)
    yield from _JA_TOKEN_RE.findall(text)
    yield from _KO_TOKEN_RE.findall(text)


def tokenize(text: str, language: str, extra_stopwords: set[str] | None = None) -> Iterable[str]:
    """Yield plausible content words from a segment."""
    extra_stopwords = extra_stopwords or set()
    if language == "zh" and _jieba is not None:
        raw_tokens: Iterable[str] = _jieba.cut(text, cut_all=False)
    elif language == "ja":
        raw_tokens = _extra_tokens(text)
    elif language == "ko":
        raw_tokens = _KO_TOKEN_RE.findall(text)
    else:
        raw_tokens = _ASCII_WORD_RE.findall(text)
        # Mixed-language documents still benefit from CJK terms.
        raw_tokens = list(raw_tokens) + list(_JA_TOKEN_RE.findall(text))

    for token in raw_tokens:
        token = _TOKEN_CLEAN_RE.sub("", token).strip()
        if not token:
            continue
        token = token.casefold() if token.isascii() else token
        if token in extra_stopwords:
            continue
        if token in _ZH_STOPWORDS or token in _EN_STOPWORDS or token in _JA_STOPWORDS:
            continue
        if token.isdigit() or not any(ch.isalpha() for ch in token):
            continue
        if token.isascii() and len(token) < 2:
            continue
        if not token.isascii() and len(token) < 2:
            continue
        yield token


@dataclass(slots=True)
class CorpusStats:
    generated_at: str
    totals: dict[str, Any] = field(default_factory=dict)
    languages: list[dict[str, Any]] = field(default_factory=list)
    speakers: list[dict[str, Any]] = field(default_factory=list)
    terms: list[dict[str, Any]] = field(default_factory=list)
    files: list[dict[str, Any]] = field(default_factory=list)
    reading_minutes: float = 0.0
    filters: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "filters": self.filters,
            "totals": self.totals,
            "reading_minutes": round(self.reading_minutes, 1),
            "languages": self.languages,
            "speakers": self.speakers,
            "terms": self.terms,
            "files": self.files,
        }


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _where(
    file_pattern: str | None,
    extensions: list[str] | None,
) -> tuple[str, list[object]]:
    clauses = ["1 = 1"]
    params: list[object] = []
    if file_pattern:
        clauses.append("(d.path LIKE ? ESCAPE '\\' OR d.filename LIKE ? ESCAPE '\\')")
        needle = f"%{_escape_like(file_pattern)}%"
        params.extend([needle, needle])
    if extensions:
        normalized = sorted({normalize_extension(ext) for ext in extensions if ext.strip()})
        if normalized:
            placeholders = ",".join("?" for _ in normalized)
            clauses.append(f"d.ext IN ({placeholders})")
            params.extend(normalized)
    return " AND ".join(clauses), params


def analyze(
    index: Index,
    *,
    top: int = 20,
    file_pattern: str | None = None,
    extensions: list[str] | None = None,
    extra_stopwords: set[str] | None = None,
    exclude_speakers: bool = True,
) -> CorpusStats:
    top = max(1, min(top, 200))
    where, params = _where(file_pattern, extensions)
    connection = index.connection

    doc_row = connection.execute(
        f"""
        SELECT COUNT(*) AS documents,
               COALESCE(SUM(char_count), 0) AS chars,
               COALESCE(SUM(dialogue_count), 0) AS dialogues,
               COALESCE(SUM(segment_count), 0) AS segments
        FROM documents d
        WHERE {where}
        """,
        params,
    ).fetchone()

    segment_row = connection.execute(
        f"""
        SELECT COUNT(*) AS lines,
               COALESCE(SUM(LENGTH(s.text)), 0) AS text_chars,
               COALESCE(SUM(CASE WHEN s.kind = 'dialogue' THEN LENGTH(s.text) ELSE 0 END), 0) AS dialogue_chars,
               COALESCE(SUM(CASE WHEN s.kind = 'narration' THEN LENGTH(s.text) ELSE 0 END), 0) AS narration_chars
        FROM segments s
        JOIN documents d ON d.id = s.doc_id
        WHERE {where}
        """,
        params,
    ).fetchone()

    speaker_rows = connection.execute(
        f"""
        SELECT s.speaker AS speaker,
               COUNT(*) AS lines,
               COALESCE(SUM(LENGTH(s.text)), 0) AS chars
        FROM segments s
        JOIN documents d ON d.id = s.doc_id
        WHERE {where}
          AND s.speaker IS NOT NULL AND s.speaker <> ''
          AND s.kind = 'dialogue'
        GROUP BY s.speaker
        ORDER BY chars DESC, lines DESC, s.speaker
        LIMIT ?
        """,
        [*params, top],
    ).fetchall()
    speakers = [
        {"speaker": row["speaker"], "lines": int(row["lines"]), "chars": int(row["chars"])}
        for row in speaker_rows
    ]

    language_rows = connection.execute(
        f"""
        SELECT COALESCE(language, 'unknown') AS language,
               COUNT(*) AS documents,
               COALESCE(SUM(char_count), 0) AS chars
        FROM documents d
        WHERE {where}
        GROUP BY COALESCE(language, 'unknown')
        ORDER BY chars DESC
        """,
        params,
    ).fetchall()
    languages = [
        {
            "language": row["language"],
            "documents": int(row["documents"]),
            "chars": int(row["chars"]),
        }
        for row in language_rows
    ]
    dominant_language = languages[0]["language"] if languages else "unknown"
    if dominant_language == "unknown" and languages:
        dominant_language = "en"

    file_rows = connection.execute(
        f"""
        SELECT d.path, d.title, d.ext, d.language, d.segment_count, d.char_count,
               d.dialogue_count
        FROM documents d
        WHERE {where}
        ORDER BY d.char_count DESC, d.path COLLATE NOCASE
        LIMIT 100
        """,
        params,
    ).fetchall()
    files = [
        {
            "path": row["path"],
            "title": row["title"] or "",
            "ext": row["ext"],
            "language": row["language"],
            "segments": int(row["segment_count"]),
            "chars": int(row["char_count"]),
            "dialogues": int(row["dialogue_count"]),
        }
        for row in file_rows
    ]

    all_speaker_rows = connection.execute(
        f"""
        SELECT DISTINCT s.speaker AS speaker
        FROM segments s
        JOIN documents d ON d.id = s.doc_id
        WHERE {where} AND s.speaker IS NOT NULL AND s.speaker <> ''
        """,
        params,
    ).fetchall()
    speaker_names = {row["speaker"] for row in all_speaker_rows if row["speaker"]}
    stopwords = set(extra_stopwords or set())
    if exclude_speakers:
        stopwords.update(name.casefold() if name.isascii() else name for name in speaker_names)

    counter: Counter[str] = Counter()
    cursor = connection.execute(
        f"""
        SELECT s.text
        FROM segments s
        JOIN documents d ON d.id = s.doc_id
        WHERE {where} AND s.kind IN ('dialogue', 'narration', 'choice')
        """,
        params,
    )
    for row in cursor:
        for token in tokenize(row["text"], dominant_language, stopwords):
            counter[token] += 1

    terms = [
        {"term": term, "count": count}
        for term, count in counter.most_common(top)
    ]

    chars = int(doc_row["chars"] or 0)
    dialogue_chars = int(segment_row["dialogue_chars"] or 0)
    narration_chars = int(segment_row["narration_chars"] or 0)
    total_text_chars = int(segment_row["text_chars"] or 0)
    lines = int(segment_row["lines"] or 0)
    totals = {
        "documents": int(doc_row["documents"] or 0),
        "chars": chars,
        "text_chars": total_text_chars,
        "segments": int(doc_row["segments"] or 0),
        "lines": lines,
        "dialogues": int(doc_row["dialogues"] or 0),
        "dialogue_chars": dialogue_chars,
        "narration_chars": narration_chars,
        "speakers": len(speaker_names),
        "avg_line_chars": round(total_text_chars / lines, 1) if lines else 0.0,
        "dialogue_ratio": round(dialogue_chars / total_text_chars, 4) if total_text_chars else 0.0,
        "dominant_language": dominant_language,
    }
    reading_minutes = (total_text_chars / 400.0) if total_text_chars else 0.0
    filters = {
        "file_pattern": file_pattern,
        "extensions": extensions or [],
    }
    return CorpusStats(
        generated_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        totals=totals,
        languages=languages,
        speakers=speakers,
        terms=terms,
        files=files,
        reading_minutes=reading_minutes,
        filters=filters,
    )


def _fmt_int(value: int) -> str:
    return f"{value:,}"


def _fmt_ratio(value: float) -> str:
    return f"{value * 100:.1f}%"


def render_text(stats: CorpusStats) -> str:
    totals = stats.totals
    lines = []
    lines.append("语料总览")
    lines.append("=" * 60)
    lines.append(
        f"文档 {_fmt_int(totals.get('documents', 0))}  |  "
        f"可检索片段 {_fmt_int(totals.get('segments', 0))}  |  "
        f"字符 {_fmt_int(totals.get('chars', 0))}"
    )
    lines.append(
        f"台词 {_fmt_int(totals.get('dialogues', 0))} 条  |  "
        f"角色 {_fmt_int(totals.get('speakers', 0))} 个  |  "
        f"对白占比 {_fmt_ratio(float(totals.get('dialogue_ratio', 0)))}"
    )
    lines.append(
        f"主要语言 {totals.get('dominant_language', 'unknown')}  |  "
        f"平均每段 {totals.get('avg_line_chars', 0)} 字  |  "
        f"预计阅读 {stats.reading_minutes:.1f} 分钟"
    )
    if not totals.get("documents"):
        lines.append("")
        lines.append("当前范围内没有文档；检查 --file/--ext，或先运行 textlens index。")
        return "\n".join(lines)

    if stats.speakers:
        lines.append("")
        lines.append(f"角色台词 Top {len(stats.speakers)}")
        lines.append("-" * 60)
        lines.append(f"{'#':>3}  {pad_display('角色', 18)} {'台词':>7} {'字符':>9}")
        for index, row in enumerate(stats.speakers, start=1):
            lines.append(
                f"{index:>3}  {pad_display(row['speaker'][:18], 18)} "
                f"{row['lines']:>7,} {row['chars']:>9,}"
            )

    if stats.terms:
        lines.append("")
        lines.append(f"高频词 Top {len(stats.terms)}")
        lines.append("-" * 60)
        lines.append(f"{'#':>3}  {pad_display('词', 24)} {'次数':>7}")
        for index, row in enumerate(stats.terms, start=1):
            lines.append(
                f"{index:>3}  {pad_display(row['term'][:24], 24)} {row['count']:>7,}"
            )

    if stats.files:
        lines.append("")
        lines.append("文件明细")
        lines.append("-" * 60)
        for row in stats.files[:20]:
            lines.append(
                f"  {row['path']}  [{row['language'] or '?'}]  "
                f"{_fmt_int(row['chars'])} 字 / {_fmt_int(row['segments'])} 段"
            )
        if len(stats.files) > 20:
            lines.append(f"  … 另有 {len(stats.files) - 20} 个文件")
    return "\n".join(lines)


def _md_escape(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def render_markdown(stats: CorpusStats) -> str:
    totals = stats.totals
    lines = [
        "# TextLens 语料分析报告",
        "",
        f"生成时间：{stats.generated_at}",
        "",
        "## 总览",
        "",
        "| 指标 | 数值 |",
        "| --- | ---: |",
        f"| 文档数 | {_fmt_int(totals.get('documents', 0))} |",
        f"| 可检索片段 | {_fmt_int(totals.get('segments', 0))} |",
        f"| 总字符数 | {_fmt_int(totals.get('chars', 0))} |",
        f"| 台词条数 | {_fmt_int(totals.get('dialogues', 0))} |",
        f"| 角色数 | {_fmt_int(totals.get('speakers', 0))} |",
        f"| 对白占比 | {_fmt_ratio(float(totals.get('dialogue_ratio', 0)))} |",
        f"| 主要语言 | {totals.get('dominant_language', 'unknown')} |",
        f"| 预计阅读 | {stats.reading_minutes:.1f} 分钟 |",
        "",
    ]
    if stats.speakers:
        lines.extend(
            [
                f"## 角色台词 Top {len(stats.speakers)}",
                "",
                "| # | 角色 | 台词数 | 字符数 |",
                "| ---: | --- | ---: | ---: |",
            ]
        )
        for index, row in enumerate(stats.speakers, start=1):
            lines.append(
                f"| {index} | {_md_escape(row['speaker'])} | "
                f"{row['lines']:,} | {row['chars']:,} |"
            )
        lines.append("")
    if stats.terms:
        lines.extend(
            [
                f"## 高频词 Top {len(stats.terms)}",
                "",
                "| # | 词 | 次数 |",
                "| ---: | --- | ---: |",
            ]
        )
        for index, row in enumerate(stats.terms, start=1):
            lines.append(f"| {index} | {_md_escape(row['term'])} | {row['count']:,} |")
        lines.append("")
    if stats.files:
        lines.extend(
            [
                "## 文件明细",
                "",
                "| 文件 | 语言 | 字符 | 片段 |",
                "| --- | --- | ---: | ---: |",
            ]
        )
        for row in stats.files:
            lines.append(
                f"| {_md_escape(row['path'])} | {row['language'] or '?'} | "
                f"{row['chars']:,} | {row['segments']:,} |"
            )
        lines.append("")
    return "\n".join(lines)
