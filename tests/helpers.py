from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def write_text(path: Path, text: str, encoding: str = "utf-8") -> Path:
    path.write_bytes(text.encode(encoding))
    return path


def _paragraph(text: str, *, heading: bool = False) -> str:
    style = '<w:pPr><w:pStyle w:val="Heading1"/></w:pPr>' if heading else ""
    return (
        f"<w:p>{style}<w:r><w:t xml:space=\"preserve\">{escape(text)}</w:t></w:r></w:p>"
    )


def make_docx(
    path: Path,
    paragraphs: list[str],
    *,
    heading_indices: set[int] | None = None,
    table: list[list[str]] | None = None,
    footnote: str | None = None,
) -> Path:
    heading_indices = heading_indices or set()
    body: list[str] = []
    for index, text in enumerate(paragraphs):
        body.append(_paragraph(text, heading=index in heading_indices))
    if table:
        rows = "".join(
            "<w:tr>"
            + "".join(
                f"<w:tc>{_paragraph(cell)}</w:tc>" for cell in row
            )
            + "</w:tr>"
            for row in table
        )
        body.append(f"<w:tbl>{rows}</w:tbl>")
    body.append("<w:sectPr/>")
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{W}"><w:body>{"".join(body)}</w:body></w:document>'
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    relationships = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/>'
        "</Relationships>"
    )
    core = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<cp:coreProperties '
        'xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:dcterms="http://purl.org/dc/terms/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        "<dc:title>测试文档</dc:title><dc:creator>TextLens</dc:creator>"
        "</cp:coreProperties>"
    )
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", relationships)
        archive.writestr("word/document.xml", document)
        archive.writestr("docProps/core.xml", core)
        if footnote:
            notes = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                f'<w:footnotes xmlns:w="{W}">'
                '<w:footnote w:id="1"><w:p><w:r><w:t>'
                f"{escape(footnote)}"
                "</w:t></w:r></w:p></w:footnote>"
                "</w:footnotes>"
            )
            archive.writestr("word/footnotes.xml", notes)
    return path
