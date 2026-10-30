"""Best-effort text decoding for Chinese/Japanese game scripts and documents.

Galgame scripts are frequently stored as UTF-16LE (KiriKiri), Shift-JIS/CP932
(older Japanese engines), or GB18030/UTF-8 (Chinese translations).  There is no
magic byte for most of these, so we try a small set of candidates and rank the
decoded results by how plausible their characters look.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from pathlib import Path

from .errors import ParseError

try:  # optional, but it is already a common transitive dependency
    from charset_normalizer import from_bytes as _normalizer_from_bytes
except Exception:  # pragma: no cover - depends on the environment
    _normalizer_from_bytes = None


@dataclass(frozen=True, slots=True)
class DecodedText:
    text: str
    encoding: str


_BOMS: tuple[tuple[bytes, str], ...] = (
    (b"\xef\xbb\xbf", "utf-8-sig"),
    (b"\xff\xfe\x00\x00", "utf-32"),
    (b"\x00\x00\xfe\xff", "utf-32"),
    (b"\xff\xfe", "utf-16"),
    (b"\xfe\xff", "utf-16"),
)

_CANDIDATE_ENCODINGS = ("gb18030", "cp932", "big5", "utf-16-le", "utf-16-be")

# Frequent simplified + traditional Chinese characters.  A wrong single-byte
# decoding often yields valid but rare CJK code points, so this table is a
# useful tie-breaker between GB18030 and Big5.
_COMMON_HAN = set(
    "的一是不了在人有我他这为之大来以个中上们到说国和地也子时道出而要于就下得可你年生"
    "自会那后能对着事其里所去行过家十用发天如然作方成者多日都三小军二无同么经法当起"
    "与好看学进种将还分此心前面又定见只主没公从很真最意想声情话问间手点头面走开"
    "這們來說時會後裡為與國學進種將還發經過見問間頭點開聲情話沒麼個中上到"
    "約定明天車站見面風吹過旁白角色台詞開始結束"
    "雨雪夜光暗黑白紅藍綠春夏秋冬海山川城市村町道空"
    "日日本語文章彼女彼氏私僕君今何処名前"
)


def _normalize_encoding_name(name: str) -> str:
    return name.lower().replace("_", "-")


def _plausibility(text: str) -> float:
    """Score a decoded string; higher is more likely to be correct."""
    if not text:
        return -1000.0
    total = len(text)
    controls = 0
    private = 0
    replacement = 0
    han = kana = halfwidth_kana = ascii_letters = hangul = bopomofo = 0
    common_han = 0
    for ch in text:
        category = unicodedata.category(ch)
        if category == "Cc" and ch not in "\n\r\t":
            controls += 1
        elif category == "Co":
            private += 1
        if ch == "\ufffd":
            replacement += 1
        code = ord(ch)
        if 0x4E00 <= code <= 0x9FFF or 0x3400 <= code <= 0x4DBF:
            han += 1
            if ch in _COMMON_HAN:
                common_han += 1
        elif 0x3040 <= code <= 0x30FF or 0x31F0 <= code <= 0x31FF:
            kana += 1
        elif 0xFF66 <= code <= 0xFF9D:
            halfwidth_kana += 1
        elif 0x3100 <= code <= 0x312F:  # Bopomofo: a strong Big5 misdecode signal
            bopomofo += 1
        elif 0xAC00 <= code <= 0xD7AF:
            hangul += 1
        elif ch.isascii() and ch.isalpha():
            ascii_letters += 1

    printable = sum(1 for ch in text if ch.isprintable() or ch in "\n\r\t")
    score = (printable / total) * 100.0
    score += min(han / total, 0.35) * 18.0
    # Kana is the strongest signal that CP932/Shift-JIS was decoded correctly.
    score += min(kana / total, 0.35) * 38.0
    score += min(common_han / total, 0.45) * 42.0
    score += min(hangul / total, 0.35) * 20.0
    score += min(ascii_letters / total, 0.4) * 8.0
    score -= (controls / total) * 300.0
    score -= (private / total) * 40.0
    score -= (replacement / total) * 600.0
    score -= (halfwidth_kana / total) * 45.0
    score -= (bopomofo / total) * 80.0
    mojibake = text.count("Ã") + text.count("Â") + text.count("â€") + text.count("ï¿½")
    score -= min(mojibake, 50) * 1.5
    return score


def _try_decode(raw: bytes, encoding: str) -> str | None:
    try:
        return raw.decode(encoding)
    except (UnicodeDecodeError, LookupError):
        return None


def decode_bytes(raw: bytes, *, source: str = "<bytes>") -> DecodedText:
    """Decode *raw* into text and report the encoding that was used."""
    if not raw:
        return DecodedText("", "utf-8")

    for bom, encoding in _BOMS:
        if raw.startswith(bom):
            text = _try_decode(raw, encoding)
            if text is not None:
                return DecodedText(text.lstrip("\ufeff"), encoding)

    # Valid UTF-8 is unambiguous enough to accept immediately.
    utf8 = _try_decode(raw, "utf-8")
    if utf8 is not None:
        return DecodedText(utf8, "utf-8")

    candidates: dict[str, str] = {}

    def add(encoding: str | None, text: str | None) -> None:
        if encoding and text is not None:
            candidates.setdefault(_normalize_encoding_name(encoding), text)

    normalizer_best: str | None = None
    if _normalizer_from_bytes is not None:
        try:
            match = _normalizer_from_bytes(raw).best()
            if match is not None and match.encoding:
                # ``str(match)`` already applies the detected decoder.
                add(match.encoding, str(match))
                normalizer_best = _normalize_encoding_name(match.encoding)
        except Exception:
            pass

    for encoding in _CANDIDATE_ENCODINGS:
        add(encoding, _try_decode(raw, encoding))

    if not candidates:
        return DecodedText(raw.decode("utf-8", errors="replace"), "utf-8-replaced")

    scored: list[tuple[float, str, str]] = []
    for encoding, text in candidates.items():
        if encoding.startswith("utf-16") and b"\x00" not in raw[:4096]:
            continue
        score = _plausibility(text)
        if encoding == normalizer_best:
            score += 3.0
        scored.append((score, encoding, text))
    scored.sort(key=lambda item: item[0], reverse=True)
    score, encoding, text = scored[0]

    if score < 5.0:
        return DecodedText(raw.decode("utf-8", errors="replace"), "utf-8-replaced")
    return DecodedText(text, encoding)


def read_text_file(path: str | Path) -> DecodedText:
    path = Path(path)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ParseError(f"无法读取文件：{exc}") from exc
    return decode_bytes(raw, source=str(path))


def decode_file(path: str | Path) -> DecodedText:
    """Alias kept for a friendly public API."""
    return read_text_file(path)
