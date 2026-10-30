"""Text cleanup, language detection and dialogue heuristics."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from .models import Segment

_ZERO_WIDTH_RE = re.compile(r"[\u200b\u200c\u200d\u2060\ufeff]")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SPACE_RE = re.compile(r"[ \t\u00a0\u3000]+")
_TAG_RE = re.compile(r"\[[^\[\]\r\n]{0,300}\]")
_ASS_TAG_RE = re.compile(r"\{[^{}]*\}")

_QUOTE_PAIRS: tuple[tuple[str, str], ...] = (
    ("「", "」"),
    ("『", "』"),
    ("“", "”"),
    ('"', '"'),
    ("‘", "’"),
)

_HEADING_RE = re.compile(r"^(?P<hashes>#{1,6})\s+(?P<title>.+)$")
_BRACKET_SPEAKER_RE = re.compile(r"^[【\[](?P<name>[^】\]]{1,24})[】\]]\s*(?P<text>.+)$")
_COLON_RE = re.compile(r"^(?P<name>[^\s:：]{1,24})\s*[：:]\s*(?P<text>.+)$")

_INVALID_SPEAKERS = {
    "http",
    "https",
    "www",
    "name",
    "text",
    "voice",
    "sound",
    "chapter",
    "scene",
    "label",
    "他说",
    "她说",
    "我说",
    "他说道",
    "她说道",
    "我说道",
    "他想",
    "她想",
    "他想道",
    "她想道",
    "旁白",
    "narrator",
    "作者",
    "译者",
    "声明",
    "简介",
    "内容简介",
    "目录",
    "序言",
    "后记",
    "正文",
    "注释",
    "翻译",
    "校对",
    "标题",
    "时间",
    "地点",
    "参考资料",
    "来源",
    "出处",
    "链接",
    "网址",
    "摘要",
    "关键词",
    "标签",
    "分类",
    "备注",
    "说明",
}

# Long colon prefixes in prose are almost always narrative clauses:
# “岛村心想：……”, “据她说：……”, “起先人们到处高声谈论：……”.
_NARRATION_SUFFIXES = (
    "心想",
    "寻思",
    "思忖",
    "猜测",
    "猜想",
    "自语",
    "喃喃",
    "嘟囔",
    "嘀咕",
    "补充",
    "解释",
    "回答",
    "追问",
    "告诉",
    "提醒",
    "抱怨",
    "表示",
    "指出",
    "强调",
    "发现",
    "觉得",
    "看到",
    "听到",
    "望着",
    "看着",
    "摇头",
    "点头",
    "说道",
    "问道",
    "答道",
    "喊道",
    "叫道",
    "写道",
    "说",
    "问",
    "答",
    "喊",
    "叫",
    "讲",
    "谈",
    "写",
    "念",
    "唱",
    "笑",
    "哭",
    "叹",
    "骂",
    "道",
)
_NARRATION_PREFIXES = (
    "还有",
    "起先",
    "此时",
    "这时",
    "后来",
    "于是",
    "因此",
    "然而",
    "不过",
    "其中",
    "另外",
    "总之",
    "据说",
    "听说",
    "看到",
    "想到",
    "原来",
    "其实",
    "如果",
    "因为",
    "所以",
    "但是",
    "可是",
    "只是",
    "不是",
    "就是",
    "这种",
    "那种",
    "一个",
    "一位",
    "字条",
    "白纸",
)

_NAME_INVALID_RE = re.compile(r"[。！？!?，,；;、/\\|=<>《》〈〉【】\[\]:：]")
_SENTENCE_TAIL_RE = re.compile(r"[。！？!?…」』”\"）)]\s*$")


@dataclass(frozen=True, slots=True)
class LineInfo:
    kind: str
    speaker: str | None
    text: str


def clean_line(line: str) -> str:
    """Clean one source line without destroying meaningful CJK punctuation."""
    line = unicodedata.normalize("NFC", line)
    line = _ZERO_WIDTH_RE.sub("", line)
    line = _CONTROL_RE.sub("", line)
    line = line.replace("\u00a0", " ").replace("\u3000", " ")
    line = _SPACE_RE.sub(" ", line)
    return line.strip()


def strip_script_tags(line: str) -> str:
    """Remove KiriKiri/KAG-style ``[...]`` tags that are not visible text."""
    # A few KAG tags explicitly represent visual breaks.
    line = re.sub(r"\[(?:r|l|p|br|er)\b[^\]]*\]", " ", line, flags=re.IGNORECASE)
    return _TAG_RE.sub("", line).strip()


def strip_ass_tags(line: str) -> str:
    return _ASS_TAG_RE.sub("", line)


def normalize_for_search(text: str) -> str:
    """Normalize for matching while keeping CJK code points searchable."""
    text = unicodedata.normalize("NFKC", text)
    text = _ZERO_WIDTH_RE.sub("", text)
    text = text.casefold()
    text = text.replace("\u00a0", " ").replace("\u3000", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def valid_speaker(name: str) -> bool:
    name = name.strip().strip("【】[]")
    if not (1 <= len(name) <= 24):
        return False
    if name.casefold() in _INVALID_SPEAKERS:
        return False
    if name.isdigit():
        return False
    if _NAME_INVALID_RE.search(name):
        return False
    if name.startswith(("http://", "https://", "www.")):
        return False
    # Long sentence-like prefixes are usually narration, not names.
    if len(name) > 10 and _SENTENCE_TAIL_RE.search(name):
        return False
    if name.endswith(("说道", "问道", "答道", "喊道", "叫道")):
        return False
    return True


def valid_colon_speaker(name: str) -> bool:
    """Stricter check for the ambiguous ``名字：台词`` style."""
    name = name.strip().strip(":： ")
    if not valid_speaker(name):
        return False
    if len(name) > 8:
        return False
    if name.startswith(_NARRATION_PREFIXES):
        return False
    if name.endswith(_NARRATION_SUFFIXES):
        return False
    return True


def _extract_quoted(line: str) -> tuple[str, str, str] | None:
    for opening, closing in _QUOTE_PAIRS:
        start = line.find(opening)
        if start < 0:
            continue
        end = line.rfind(closing)
        if end <= start:
            continue
        prefix = line[:start].strip()
        suffix = line[end + len(closing) :].strip()
        # Only trailing punctuation may follow the closing quote.
        if suffix and not re.fullmatch(r"[。．.!！?？…~～、,，;；:：]+", suffix):
            continue
        return prefix, line[start + len(opening) : end], suffix
    return None


def _strip_outer_quotes(text: str) -> str:
    text = text.strip()
    for opening, closing in _QUOTE_PAIRS:
        if text.startswith(opening) and text.endswith(closing) and len(text) >= 2:
            return text[len(opening) : -len(closing)].strip()
    return text


def classify_line(line: str) -> LineInfo | None:
    """Classify a plain-text/script line as dialogue, narration or heading."""
    raw = clean_line(line)
    if not raw:
        return None

    heading = _HEADING_RE.match(raw)
    if heading:
        title = clean_line(heading.group("title"))
        return LineInfo("heading", None, title) if title else None

    bracket = _BRACKET_SPEAKER_RE.match(raw)
    if (
        bracket
        and not bracket.group("text").startswith("(")
        and valid_speaker(bracket.group("name"))
    ):
        text = _strip_outer_quotes(bracket.group("text"))
        if text:
            return LineInfo("dialogue", bracket.group("name").strip(), text)

    quoted = _extract_quoted(raw)
    if quoted is not None:
        prefix, inner, _suffix = quoted
        inner = clean_line(inner)
        if inner:
            # Patterns such as ``Alice（微笑）「...」`` and ``Alice：「...」``.
            speaker = prefix.rstrip(":：").strip()
            expression = re.match(r"^(?P<name>.+?)\s*[（(][^）)]{0,30}[）)]\s*$", speaker)
            if expression:
                speaker = expression.group("name").strip()
            if valid_colon_speaker(speaker):
                return LineInfo("dialogue", speaker, inner)
            if not prefix or prefix in {"*", "-", "—"}:
                return LineInfo("dialogue", None, inner)
            # A quoted clause inside narration stays narration.
            return LineInfo("narration", None, raw)

    colon = _COLON_RE.match(raw)
    if colon and valid_colon_speaker(colon.group("name")):
        text = _strip_outer_quotes(colon.group("text"))
        if text:
            return LineInfo("dialogue", colon.group("name").strip(), text)

    # A line that is only a quote has no known speaker but is still dialogue.
    if raw.startswith(tuple(pair[0] for pair in _QUOTE_PAIRS)):
        stripped = _strip_outer_quotes(raw)
        if stripped and stripped != raw:
            return LineInfo("dialogue", None, stripped)

    return LineInfo("narration", None, raw)


def make_segment(line_no: int, line: str, *, kind: str | None = None, speaker: str | None = None) -> Segment | None:
    """Build a segment from a raw line, applying the dialogue heuristics."""
    if kind is not None:
        text = clean_line(line)
        if not text:
            return None
        return Segment(line_no=line_no, text=text, kind=kind, speaker=speaker)
    info = classify_line(line)
    if info is None:
        return None
    return Segment(line_no=line_no, text=info.text, kind=info.kind, speaker=info.speaker)


def detect_language(text: str) -> str:
    """Return a coarse language tag: ``zh``, ``ja``, ``ko`` or ``en``."""
    sample = text[:30000]
    if not sample:
        return "unknown"
    han = kana = hangul = latin = 0
    for ch in sample:
        code = ord(ch)
        if 0x4E00 <= code <= 0x9FFF or 0x3400 <= code <= 0x4DBF:
            han += 1
        elif 0x3040 <= code <= 0x30FF:
            kana += 1
        elif 0xAC00 <= code <= 0xD7AF:
            hangul += 1
        elif ch.isascii() and ch.isalpha():
            latin += 1
    if hangul > max(10, han * 0.2):
        return "ko"
    if kana >= 5 and kana > han * 0.06:
        return "ja"
    if han > 10 and han >= latin * 0.35:
        return "zh"
    if latin > 10:
        return "en"
    return "unknown"


def collapse_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def display_width(text: str) -> int:
    """Approximate terminal columns, counting wide CJK characters as two."""
    return sum(2 if unicodedata.east_asian_width(ch) in {"W", "F"} else 1 for ch in text)


def pad_display(text: str, width: int) -> str:
    return text + " " * max(0, width - display_width(text))


def snippet(text: str, terms: list[str], width: int = 150, *, regex: str | None = None) -> str:
    """Return a readable window around the first match."""
    flat = collapse_whitespace(text)
    if not flat:
        return ""
    folded = flat.casefold()
    positions: list[int] = []
    if regex:
        try:
            match = re.search(regex, flat, flags=re.IGNORECASE)
        except re.error:
            match = None
        if match:
            positions.append(match.start())
    else:
        for term in terms:
            needle = normalize_for_search(term)
            if needle:
                pos = folded.find(needle)
                if pos >= 0:
                    positions.append(pos)
    if not positions:
        return flat if len(flat) <= width else flat[:width].rstrip() + "…"
    pos = min(positions)
    half = max(20, (width - 20) // 2)
    start = max(0, pos - half)
    end = min(len(flat), pos + width - (pos - start))
    result = flat[start:end].strip()
    if start > 0:
        result = "…" + result
    if end < len(flat):
        result += "…"
    return result
