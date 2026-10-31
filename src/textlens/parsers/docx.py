"""Dependency-free .docx reader.

Only the visible document body, tables and footnotes are extracted.  The reader
walks OOXML in document order, skips tracked deletions and field instructions,
and turns table rows into searchable ``cell | cell`` segments.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from ..errors import ParseError, UnsupportedFormat
from ..models import Segment
from ..text import clean_line, make_segment
from .base import finalize

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
DC = "{http://purl.org/dc/elements/1.1/}"
CP = "{http://schemas.openxmlformats.org/package/2006/metadata/core-properties}"
DCTERMS = "{http://purl.org/dc/terms/}"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _paragraph_text(paragraph: ET.Element) -> str:
    parts: list[str] = []

    def walk(node: ET.Element, *, hidden: bool = False) -> None:
        tag = _local(node.tag)
        if tag in {"del", "moveFrom", "instrText", "delText", "pPr", "rPr", "sectPr"}:
            return
        if tag == "t":
            parts.append(node.text or "")
            return
        if tag == "tab":
            parts.append("\t")
            return
        if tag in {"br", "cr"}:
            parts.append("\n")
            return
        if tag == "noBreakHyphen":
            parts.append("-")
            return
        if tag == "softHyphen":
            return
        if tag == "txbxContent":
            # Text boxes can contain paragraphs; extract their runs once.
            for child in node:
                walk(child)
            return
        for child in node:
            walk(child)

    walk(paragraph)
    return "".join(parts)


def _paragraph_is_heading(paragraph: ET.Element) -> bool:
    style = paragraph.find(f"{W}pPr/{W}pStyle")
    if style is None:
        return False
    value = (style.get(f"{W}val") or "").casefold().replace(" ", "")
    return value.startswith("heading") or value.startswith("标题")


def _table_rows(table: ET.Element) -> list[list[str]]:
    rows: list[list[str]] = []
    for row in table.findall(f"{W}tr"):
        cells: list[str] = []
        for cell in row.findall(f"{W}tc"):
            paragraphs = [
                clean_line(_paragraph_text(p)) for p in cell.findall(f"{W}p")
            ]
            # Nested tables are uncommon in galgame material but easy to include.
            for nested in cell.findall(f"{W}tbl"):
                for nested_row in _table_rows(nested):
                    paragraphs.append(" | ".join(nested_row))
            cells.append(" ".join(part for part in paragraphs if part))
        rows.append(cells)
    return rows


class _DocxWalker:
    def __init__(self) -> None:
        self.segments: list[Segment] = []
        self.line_no = 0

    def add(self, text: str, *, kind: str, speaker: str | None = None, meta: dict | None = None) -> None:
        text = clean_line(text)
        if not text:
            return
        self.line_no += 1
        self.segments.append(
            Segment(
                line_no=self.line_no,
                text=text,
                kind=kind,
                speaker=speaker,
                meta=meta or {},
            )
        )

    def blocks(self, container: ET.Element) -> None:
        for child in container:
            tag = _local(child.tag)
            if tag == "p":
                text = _paragraph_text(child)
                if not text:
                    continue
                chunks = [clean_line(part) for part in text.splitlines()]
                chunks = [chunk for chunk in chunks if chunk]
                if not chunks:
                    continue
                if _paragraph_is_heading(child):
                    self.add(" ".join(chunks), kind="heading")
                else:
                    for chunk in chunks:
                        self.line_no += 1
                        segment = make_segment(self.line_no, chunk)
                        if segment is not None:
                            self.segments.append(segment)
            elif tag == "tbl":
                for row in _table_rows(child):
                    cells = [cell for cell in row if cell]
                    if cells:
                        self.add(" | ".join(cells), kind="table")
            elif tag in {"sdt", "sdtContent", "customXml", "body"}:
                self.blocks(child)


def _body(root: ET.Element) -> ET.Element | None:
    body = root.find(f"{W}body")
    return body


def _read_xml(archive: zipfile.ZipFile, name: str) -> ET.Element | None:
    try:
        raw = archive.read(name)
    except KeyError:
        return None
    try:
        return ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ParseError(f"OOXML 内容损坏（{name}）：{exc}") from exc


def _core_properties(archive: zipfile.ZipFile) -> dict[str, str]:
    root = _read_xml(archive, "docProps/core.xml")
    if root is None:
        return {}
    result: dict[str, str] = {}
    fields = {
        "title": f"{DC}title",
        "creator": f"{DC}creator",
        "last_modified_by": f"{CP}lastModifiedBy",
        "created": f"{DCTERMS}created",
        "modified": f"{DCTERMS}modified",
    }
    for key, tag in fields.items():
        node = root.find(tag)
        if node is not None and node.text:
            result[key] = node.text.strip()
    return result


def _footnotes(archive: zipfile.ZipFile) -> list[Segment]:
    root = _read_xml(archive, "word/footnotes.xml")
    if root is None:
        return []
    result: list[Segment] = []
    for footnote in root.findall(f"{W}footnote"):
        note_id = footnote.get(f"{W}id") or ""
        note_type = footnote.get(f"{W}type")
        if note_type in {"separator", "continuationSeparator", "continuationNotice"}:
            continue
        if note_id in {"-1", "0"}:
            continue
        text = " ".join(
            clean_line(_paragraph_text(p)) for p in footnote.findall(f"{W}p")
        ).strip()
        if text:
            result.append(
                Segment(
                    line_no=0,
                    text=text,
                    kind="footnote",
                    speaker=None,
                    meta={"footnote_id": note_id},
                )
            )
    return result


def parse_docx(path: Path) -> ParsedDocument:
    if path.suffix.casefold() in {".doc", ".dot"}:
        raise UnsupportedFormat(
            "旧版 Word 二进制格式（.doc）暂不支持。请用 Word 另存为 .docx，"
            "或用 LibreOffice 转换：soffice --headless --convert-to docx <文件>"
        )
    try:
        with zipfile.ZipFile(path) as archive:
            if "word/document.xml" not in archive.namelist():
                raise ParseError("不是有效的 .docx（缺少 word/document.xml）")
            root = _read_xml(archive, "word/document.xml")
            if root is None:
                raise ParseError("无法读取 word/document.xml")
            body = _body(root)
            if body is None:
                raise ParseError("文档缺少 body 节点")
            walker = _DocxWalker()
            walker.blocks(body)
            for footnote in _footnotes(archive):
                walker.line_no += 1
                footnote.line_no = walker.line_no
                walker.segments.append(footnote)
            properties = _core_properties(archive)
    except zipfile.BadZipFile as exc:
        raise ParseError(
            "无法作为 .docx 打开；如果是旧版 .doc，请先另存为 .docx。"
        ) from exc

    return finalize(
        path,
        parser="docx",
        encoding="ooxml",
        segments=walker.segments,
        metadata=properties,
        title=properties.get("title"),
    )
