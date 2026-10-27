"""Small files of every format the second step of the knowledge base reads, built in memory from the standard library."""

from __future__ import annotations

import io
import struct
import zipfile
import zlib
from xml.sax.saxutils import escape

from aifred.testimage import digits_png

GPS_MARK = b"GPSLatitude=45.4642N GPSLongitude=9.1900E"
SECRET_COMMENT = b"taken at the private address"


def zipped(parts: dict[str, str | bytes], first: tuple[str, bytes] | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        if first:
            zf.writestr(zipfile.ZipInfo(first[0]), first[1], compress_type=zipfile.ZIP_STORED)
        for name, body in parts.items():
            zf.writestr(name, body)
    return buffer.getvalue()


# Office ---------------------------------------------------------------------------------------------------

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def make_docx(items: list[tuple[str, str]], table: list[list[str]] | None = None, title: str = "") -> bytes:
    """``items`` are ``(style, text)``; styles like ``Heading1`` make headings."""
    body = []
    for style, text in items:
        ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
        body.append(f"<w:p>{ppr}<w:r><w:t>{escape(text)}</w:t></w:r></w:p>")
    if table:
        rows = "".join("<w:tr>" + "".join(f"<w:tc><w:p><w:r><w:t>{escape(c)}</w:t></w:r></w:p></w:tc>" for c in row) + "</w:tr>" for row in table)
        body.append(f"<w:tbl>{rows}</w:tbl>")
    document = f'<?xml version="1.0" encoding="UTF-8"?><w:document {W}><w:body>{"".join(body)}</w:body></w:document>'
    parts: dict[str, str | bytes] = {"[Content_Types].xml": "<Types/>", "word/document.xml": document}
    if title:
        parts["docProps/core.xml"] = f'<cp:coreProperties xmlns:cp="x" xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>{escape(title)}</dc:title></cp:coreProperties>'
    return zipped(parts)


S = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'


def make_xlsx(sheets: dict[str, list[list[object]]], formulas: bool = False) -> bytes:
    """Cells that are strings go through the shared string table; numbers are stored as values."""
    shared: list[str] = []
    sheet_xml, workbook, rels = {}, [], []
    for index, (name, rows) in enumerate(sheets.items(), start=1):
        out = []
        for r, row in enumerate(rows, start=1):
            cells = []
            for c, value in enumerate(row):
                ref = f"{chr(65 + c)}{r}"
                if isinstance(value, str):
                    shared.append(value)
                    cells.append(f'<c r="{ref}" t="s"><v>{len(shared) - 1}</v></c>')
                elif value is not None:
                    cells.append(f'<c r="{ref}"><v>{value}</v></c>')
            if formulas and r == len(rows):
                cells.append(f'<c r="{chr(65 + len(row))}{r}"><f>SUM(A1:A{r - 1})</f><v>42</v></c>')
            out.append(f'<row r="{r}">{"".join(cells)}</row>')
        sheet_xml[f"xl/worksheets/sheet{index}.xml"] = f'<worksheet {S}><sheetData>{"".join(out)}</sheetData></worksheet>'
        workbook.append(f'<sheet name="{escape(name)}" sheetId="{index}" r:id="rId{index}"/>')
        rels.append(f'<Relationship Id="rId{index}" Type="x/worksheet" Target="worksheets/sheet{index}.xml"/>')
    parts: dict[str, str | bytes] = {
        "[Content_Types].xml": "<Types/>", "xl/workbook.xml": f'<workbook {S}><sheets>{"".join(workbook)}</sheets></workbook>',
        "xl/_rels/workbook.xml.rels": f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{"".join(rels)}</Relationships>',
        "xl/sharedStrings.xml": f'<sst {S}>' + "".join(f"<si><t>{escape(t)}</t></si>" for t in shared) + "</sst>",
        **sheet_xml,
    }
    return zipped(parts)


A = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'


def make_pptx(slides: list[dict[str, str]]) -> bytes:
    """Each slide: ``title``, ``body`` and optionally ``notes``."""
    parts: dict[str, str | bytes] = {"[Content_Types].xml": "<Types/>", "ppt/presentation.xml": f"<p:presentation {A}/>"}
    for n, slide in enumerate(slides, start=1):
        title = f'<p:sp><p:nvSpPr><p:cNvPr id="1" name="t"/><p:cNvSpPr/><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr><p:txBody><a:p><a:r><a:t>{escape(slide["title"])}</a:t></a:r></a:p></p:txBody></p:sp>'
        body = f'<p:sp><p:nvSpPr><p:cNvPr id="2" name="b"/><p:cNvSpPr/><p:nvPr><p:ph type="body"/></p:nvPr></p:nvSpPr><p:txBody><a:p><a:r><a:t>{escape(slide["body"])}</a:t></a:r></a:p></p:txBody></p:sp>'
        parts[f"ppt/slides/slide{n}.xml"] = f"<p:sld {A}><p:cSld><p:spTree>{title}{body}</p:spTree></p:cSld></p:sld>"
        if slide.get("notes"):
            parts[f"ppt/slides/_rels/slide{n}.xml.rels"] = (
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                f'<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesSlide" Target="../notesSlides/notesSlide{n}.xml"/></Relationships>'
            )
            parts[f"ppt/notesSlides/notesSlide{n}.xml"] = (
                f'<p:notes {A}><p:cSld><p:spTree><p:sp><p:nvSpPr><p:cNvPr id="1" name="n"/><p:cNvSpPr/><p:nvPr><p:ph type="body"/></p:nvPr></p:nvSpPr>'
                f'<p:txBody><a:p><a:r><a:t>{escape(slide["notes"])}</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:notes>'
            )
    return zipped(parts)


# OpenDocument ---------------------------------------------------------------------------------------------

ODF = ('xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
       'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" '
       'xmlns:presentation="urn:oasis:names:tc:opendocument:xmlns:presentation:1.0"')


def _odf(mime: str, body: str, title: str = "") -> bytes:
    meta = f'<office:document-meta {ODF} xmlns:dc="http://purl.org/dc/elements/1.1/"><office:meta><dc:title>{escape(title)}</dc:title></office:meta></office:document-meta>' if title else ""
    parts: dict[str, str | bytes] = {"content.xml": f"<office:document-content {ODF}><office:body>{body}</office:body></office:document-content>"}
    if meta:
        parts["meta.xml"] = meta
    return zipped(parts, first=("mimetype", mime.encode()))


def make_odt(items: list[tuple[str, str]], table: list[list[str]] | None = None, title: str = "") -> bytes:
    """``items`` are ``(kind, text)`` with kind ``h1``/``h2`` or ``p``."""
    out = []
    for kind, text in items:
        out.append(f'<text:h text:outline-level="{kind[1:]}">{escape(text)}</text:h>' if kind.startswith("h") else f"<text:p>{escape(text)}</text:p>")
    if table:
        rows = "".join("<table:table-row>" + "".join(f"<table:table-cell><text:p>{escape(c)}</text:p></table:table-cell>" for c in row) + "</table:table-row>" for row in table)
        out.append(f'<table:table table:name="t">{rows}</table:table>')
    return _odf("application/vnd.oasis.opendocument.text", f"<office:text>{''.join(out)}</office:text>", title)


def make_ods(sheets: dict[str, list[list[str]]]) -> bytes:
    tables = ""
    for name, rows in sheets.items():
        body = "".join("<table:table-row>" + "".join(f"<table:table-cell><text:p>{escape(c)}</text:p></table:table-cell>" for c in row) + "</table:table-row>" for row in rows)
        tables += f'<table:table table:name="{escape(name)}">{body}</table:table>'
    return _odf("application/vnd.oasis.opendocument.spreadsheet", f"<office:spreadsheet>{tables}</office:spreadsheet>")


def make_odp(slides: list[dict[str, str]]) -> bytes:
    pages = ""
    for n, slide in enumerate(slides, start=1):
        notes = f'<presentation:notes><draw:frame><draw:text-box><text:p>{escape(slide["notes"])}</text:p></draw:text-box></draw:frame></presentation:notes>' if slide.get("notes") else ""
        pages += f'<draw:page draw:name="{escape(slide.get("name", f"page{n}"))}"><draw:frame><draw:text-box><text:p>{escape(slide["body"])}</text:p></draw:text-box></draw:frame>{notes}</draw:page>'
    return _odf("application/vnd.oasis.opendocument.presentation", f"<office:presentation>{pages}</office:presentation>")


# PDF ------------------------------------------------------------------------------------------------------


def make_pdf(pages: list[str | None]) -> bytes:
    """A PDF with one page per item; ``None`` is a page without any text layer (a scan)."""
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    add(b"")  # catalog placeholder (object 1)
    add(b"")  # pages placeholder (object 2)
    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    page_ids = []
    for number, text in enumerate(pages):
        if text is None:  # no text layer, but each scan draws a different mark so the rendered pages differ
            stream = f"{20 + number * 40} 600 30 30 re f".encode()
            content = add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        else:
            stream = f"BT /F1 12 Tf 72 720 Td ({text.replace(chr(92), chr(92) * 2).replace('(', chr(92) + '(').replace(')', chr(92) + ')')}) Tj ET".encode("latin-1")
            content = add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        page_ids.append(add(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {content} 0 R /Resources << /Font << /F1 {font} 0 R >> >> >>".encode()))
    objects[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(f'{i} 0 R' for i in page_ids)}] /Count {len(page_ids)} >>".encode()
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


# Images with private metadata -------------------------------------------------------------------------------


def make_jpeg() -> bytes:
    """A structurally valid JPEG (JFIF, EXIF with a GPS position, XMP, a comment, one frame and one scan) with a recognisable payload."""
    def seg(marker: int, body: bytes) -> bytes:
        return bytes([0xFF, marker]) + struct.pack(">H", len(body) + 2) + body

    exif = b"Exif\x00\x00II*\x00" + GPS_MARK
    return (b"\xff\xd8" + seg(0xE0, b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00") + seg(0xE1, exif) + seg(0xE1, b"http://ns.adobe.com/xap/1.0/\x00<x:xmpmeta>owner</x:xmpmeta>")
            + seg(0xFE, SECRET_COMMENT) + seg(0xDB, b"\x00" + bytes(range(64))) + seg(0xC0, b"\x08\x00\x10\x00\x10\x01\x01\x11\x00")
            + seg(0xDA, b"\x01\x01\x00\x00\x3f\x00") + b"\x12\x34\x56\x78PIXELS\xff\xd9")


def _png_chunk(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)


def make_png(number: str = "4821", with_metadata: bool = True) -> bytes:
    """The digits picture of the vision test, with text, EXIF and time chunks inserted before the image data."""
    base = digits_png(number)
    if not with_metadata:
        return base
    head, rest = base[:33], base[33:]  # signature + IHDR
    extra = _png_chunk(b"tEXt", b"Location\x00" + GPS_MARK) + _png_chunk(b"eXIf", b"MM\x00*" + GPS_MARK) + _png_chunk(b"tIME", b"\x07\xea\x0a\x1a\x0c\x00\x00")
    return head + extra + rest


def make_webp() -> bytes:
    """A WebP container with EXIF and XMP chunks and the matching VP8X flags."""
    def chunk(kind: bytes, body: bytes) -> bytes:
        return kind + struct.pack("<I", len(body)) + body + (b"\x00" if len(body) & 1 else b"")

    vp8x = chunk(b"VP8X", bytes([0x20 | 0x08 | 0x04 | 0x10]) + b"\x00\x00\x00" + b"\x0f\x00\x00" + b"\x0f\x00\x00")
    payload = b"WEBP" + vp8x + chunk(b"VP8 ", b"PIXELDATA!") + chunk(b"EXIF", b"II*\x00" + GPS_MARK) + chunk(b"XMP ", b"<x:xmpmeta>owner</x:xmpmeta>")
    return b"RIFF" + struct.pack("<I", len(payload)) + payload
