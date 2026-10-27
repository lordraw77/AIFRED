"""Parsers for the second step of the knowledge base: PDF, Office, OpenDocument and images (spec 9.4).

Everything here handles hostile input and runs only inside the sandboxed child process (``rag_sandbox``): no credentials,
a time limit, a CPU limit and a memory limit. The parent never imports this module's parsers for untrusted data.

A parser turns a file into *blocks* (``{"page", "heading", "text"}``); chunking, embedding and search are the same as for text.
Office files are read with ``zipfile`` and ``xml.etree`` after defensive checks; PDF text comes from ``pypdf`` (pure Python) and
scanned pages are rendered with ``pypdfium2``.
"""

from __future__ import annotations

import base64
import io
import re
import struct
import zipfile
import zlib
from typing import Any
from xml.etree import ElementTree as ET

KINDS = ("pdf", "docx", "pptx", "xlsx", "odt", "ods", "odp", "jpeg", "png", "webp")
OFFICE_KINDS = ("docx", "pptx", "xlsx", "odt", "ods", "odp")
IMAGE_KINDS = ("jpeg", "png", "webp")
MIME = {"jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}

MAX_ENTRIES = 2000
MAX_TOTAL_UNCOMPRESSED = 60 * 1024 * 1024
MAX_PART = 25 * 1024 * 1024
MAX_RATIO = 100
RATIO_FLOOR = 1024 * 1024
MAX_ROWS = 5000
MAX_COLUMNS = 50
ROWS_PER_BLOCK = 100
MAX_REPEAT = 50
RENDER_MAX_SIDE = 1600
HEADING_SEPARATOR = " › "


class ParseError(Exception):
    """The file cannot be read; the message is shown to the admin."""


# Type detection ---------------------------------------------------------------------------------------


def sniff(data: bytes) -> str | None:
    """The kind of a file decided by its bytes (never by its name), or ``None`` for anything else."""
    if data.startswith(b"%PDF-"):
        return "pdf"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data.startswith(b"PK\x03\x04"):
        return _sniff_zip(data)
    return None


def magic_kind(head: bytes) -> str | None:
    """The kind suggested by the first bytes alone (``zip`` for any ZIP container): safe to call outside the sandbox."""
    if head.startswith(b"%PDF-"):
        return "pdf"
    if head.startswith(b"\xff\xd8\xff") or head.startswith(b"\x89PNG\r\n\x1a\n") or (head[:4] == b"RIFF" and head[8:12] == b"WEBP"):
        return "image"
    if head.startswith(b"PK\x03\x04"):
        return "zip"
    return None


def _sniff_zip(data: bytes) -> str | None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = set(zf.namelist())
            if "mimetype" in names:
                mime = zf.read("mimetype")[:100].decode("ascii", "replace").strip()
                return {
                    "application/vnd.oasis.opendocument.text": "odt",
                    "application/vnd.oasis.opendocument.spreadsheet": "ods",
                    "application/vnd.oasis.opendocument.presentation": "odp",
                }.get(mime)
            if "word/document.xml" in names:
                return "docx"
            if "xl/workbook.xml" in names:
                return "xlsx"
            if "ppt/presentation.xml" in names:
                return "pptx"
    except (zipfile.BadZipFile, OSError, ValueError, NotImplementedError, RuntimeError):
        return None
    return None


# Archives and XML -------------------------------------------------------------------------------------


def open_archive(data: bytes) -> zipfile.ZipFile:
    """Open an Office or OpenDocument archive after the defensive checks.

    Raises:
        ParseError: For too many entries, too much uncompressed data, a suspicious compression ratio, a path
            that climbs out of the archive or an encrypted entry.
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, OSError, ValueError):
        raise ParseError("the file is not a valid archive") from None
    infos = zf.infolist()
    if len(infos) > MAX_ENTRIES:
        raise ParseError(f"the archive has too many entries ({len(infos):,}; the limit is {MAX_ENTRIES:,})")
    total = 0
    for info in infos:
        if info.flag_bits & 0x1:
            raise ParseError("the archive is encrypted")
        total += info.file_size
        if info.file_size > MAX_PART:
            raise ParseError(f"an entry of the archive is too large ({info.file_size:,} bytes)")
        if info.file_size > RATIO_FLOOR and info.file_size / max(info.compress_size, 1) > MAX_RATIO:
            raise ParseError("the archive compresses suspiciously well (a decompression bomb)")
    if total > MAX_TOTAL_UNCOMPRESSED:
        raise ParseError(f"the archive expands to more than {MAX_TOTAL_UNCOMPRESSED // 1048576} MB")
    return zf


def _safe_name(name: str) -> bool:
    return not (name.startswith("/") or "\\" in name or ".." in name.split("/"))


def read_part(zf: zipfile.ZipFile, name: str, required: bool = True) -> bytes | None:
    """Read one archive entry, bounded, ignoring entries whose path climbs out of the archive."""
    if not _safe_name(name):
        return None
    try:
        info = zf.getinfo(name)
    except KeyError:
        if required:
            raise ParseError(f"the document has no {name}") from None
        return None
    with zf.open(info) as handle:
        body = handle.read(MAX_PART + 1)
    if len(body) > MAX_PART:
        raise ParseError(f"{name} is larger than it says")
    return body


def parse_xml(body: bytes, what: str = "an XML part") -> ET.Element:
    """Parse XML that has no DOCTYPE and no entity declaration (entity-expansion attacks need one).

    Raises:
        ParseError: For a DOCTYPE or ENTITY, a non-UTF-8 byte-order mark or invalid XML.
    """
    if body.startswith((b"\xff\xfe", b"\xfe\xff")) or b"\x00" in body[:200]:
        raise ParseError(f"{what} uses an unsupported text encoding")
    head = body.upper()
    if b"<!DOCTYPE" in head or b"<!ENTITY" in head:
        raise ParseError(f"{what} declares a DOCTYPE or entities, which is refused")
    try:
        return ET.fromstring(body)
    except ET.ParseError:
        raise ParseError(f"{what} is not valid XML") from None


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _attr(el: ET.Element, name: str) -> str | None:
    for key, value in el.attrib.items():
        if _local(key) == name:
            return value
    return None


class _Headings:
    """Builds the heading path (``Guide › Install``) while walking a document."""

    def __init__(self) -> None:
        self.stack: list[tuple[int, str]] = []

    def enter(self, level: int, title: str) -> None:
        while self.stack and self.stack[-1][0] >= level:
            self.stack.pop()
        self.stack.append((level, title))

    @property
    def path(self) -> str:
        return HEADING_SEPARATOR.join(t for _, t in self.stack)


class _Blocks:
    """Collects paragraphs under the current heading into blocks."""

    def __init__(self, page: int | None = None) -> None:
        self.blocks: list[dict[str, Any]] = []
        self.headings = _Headings()
        self.page = page
        self._pending: list[str] = []

    def heading(self, level: int, title: str) -> None:
        self.flush()
        self.headings.enter(level, title)

    def add(self, text: str) -> None:
        text = text.strip()
        if text:
            self._pending.append(text)

    def flush(self) -> None:
        if self._pending:
            self.blocks.append({"page": self.page, "heading": self.headings.path, "text": "\n\n".join(self._pending)})
            self._pending = []


# docx -------------------------------------------------------------------------------------------------

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _w_text(node: ET.Element) -> str:
    out = []
    for el in node.iter():
        tag = _local(el.tag)
        if tag == "t" and el.text:
            out.append(el.text)
        elif tag == "tab":
            out.append("\t")
        elif tag in ("br", "cr"):
            out.append("\n")
    return "".join(out)


def _docx_table(table: ET.Element) -> str:
    rows = []
    for tr in table.iter(f"{_W}tr"):
        cells = [" ".join(" ".join(_w_text(p) for p in tc.iter(f"{_W}p")).split()) for tc in tr.findall(f"{_W}tc")]
        if any(cells):
            rows.append(" | ".join(cells))
    return "\n".join(rows)


def parse_docx(data: bytes, limits: dict[str, Any]) -> dict[str, Any]:
    zf = open_archive(data)
    body = parse_xml(read_part(zf, "word/document.xml") or b"", "word/document.xml")
    blocks = _Blocks()
    root = body.find(f"{_W}body")
    for child in list(root) if root is not None else []:
        tag = _local(child.tag)
        if tag == "p":
            style = child.find(f"{_W}pPr/{_W}pStyle")
            name = (style.get(f"{_W}val") if style is not None else "") or ""
            text = " ".join(_w_text(child).split())
            match = re.fullmatch(r"(?i)heading\s*(\d)", name)
            if text and match:
                blocks.heading(int(match.group(1)), text)
            elif text and name.lower() == "title":
                blocks.heading(1, text)
            else:
                blocks.add(text)
        elif tag == "tbl":
            blocks.add(_docx_table(child))
    blocks.flush()
    title = _core_title(zf)
    return {"kind": "docx", "title": title, "blocks": blocks.blocks, "scan_pages": [], "pages": None}


def _core_title(zf: zipfile.ZipFile) -> str:
    body = read_part(zf, "docProps/core.xml", required=False)
    if not body:
        return ""
    try:
        root = parse_xml(body, "docProps/core.xml")
    except ParseError:
        return ""
    for el in root.iter():
        if _local(el.tag) == "title" and el.text:
            return " ".join(el.text.split())[:200]
    return ""


# pptx -------------------------------------------------------------------------------------------------

_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def _a_paragraphs(node: ET.Element) -> list[str]:
    out = []
    for p in node.iter(f"{_A}p"):
        text = "".join((t.text or "") for t in p.iter(f"{_A}t"))
        if text.strip():
            out.append(" ".join(text.split()))
    return out


def parse_pptx(data: bytes, limits: dict[str, Any]) -> dict[str, Any]:
    zf = open_archive(data)
    names = zf.namelist()
    slides = sorted((int(m.group(1)), n) for n in names if (m := re.fullmatch(r"ppt/slides/slide(\d+)\.xml", n)))
    limit = int(limits.get("max_pages", 500))
    blocks: list[dict[str, Any]] = []
    for number, name in slides[:limit]:
        root = parse_xml(read_part(zf, name) or b"", name)
        title = ""
        body: list[str] = []
        for shape in root.iter(f"{_P}sp"):
            ph = shape.find(f"{_P}nvSpPr/{_P}nvPr/{_P}ph")
            paragraphs = _a_paragraphs(shape)
            if ph is not None and ph.get("type") in ("title", "ctrTitle") and paragraphs:
                title = " ".join(paragraphs)
            else:
                body.extend(paragraphs)
        heading = f"Slide {number}" + (f": {title}" if title else "")
        if body or title:
            blocks.append({"page": number, "heading": heading, "text": "\n".join(body) or title})
        notes = _pptx_notes(zf, number)
        if notes:
            blocks.append({"page": number, "heading": f"{heading}{HEADING_SEPARATOR}Speaker notes", "text": notes})
    return {"kind": "pptx", "title": _core_title(zf), "blocks": blocks, "scan_pages": [], "pages": len(slides), "truncated": len(slides) > limit}


def _pptx_notes(zf: zipfile.ZipFile, number: int) -> str:
    rels = read_part(zf, f"ppt/slides/_rels/slide{number}.xml.rels", required=False)
    if not rels:
        return ""
    for rel in parse_xml(rels, "a relationships part").iter(f"{_REL}Relationship"):
        if rel.get("Type", "").endswith("/notesSlide"):
            target = (rel.get("Target") or "").replace("../", "ppt/")
            body = read_part(zf, target.lstrip("/"), required=False)
            if body:
                root = parse_xml(body, target)
                lines = []
                for shape in root.iter(f"{_P}sp"):
                    ph = shape.find(f"{_P}nvSpPr/{_P}nvPr/{_P}ph")
                    if ph is not None and ph.get("type") == "body":
                        lines.extend(_a_paragraphs(shape))
                return "\n".join(lines)
    return ""


# xlsx -------------------------------------------------------------------------------------------------

_S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def _column_index(ref: str) -> int:
    letters = re.match(r"[A-Za-z]+", ref)
    index = 0
    for ch in (letters.group(0).upper() if letters else "A"):
        index = index * 26 + ord(ch) - 64
    return index - 1


def _row_blocks(name: str, rows: list[list[str]]) -> list[dict[str, Any]]:
    rows = [r for r in ([c for c in row] for row in rows) if any(c.strip() for c in r)]
    if not rows:
        return []
    header = " | ".join(rows[0])
    blocks = []
    body = rows[1:] or []
    if not body:
        return [{"page": None, "heading": name, "text": header}]
    for start in range(0, len(body), ROWS_PER_BLOCK):
        part = body[start:start + ROWS_PER_BLOCK]
        blocks.append({"page": None, "heading": name, "text": "\n".join([header, *(" | ".join(r) for r in part)])})
    return blocks


def parse_xlsx(data: bytes, limits: dict[str, Any]) -> dict[str, Any]:
    zf = open_archive(data)
    shared: list[str] = []
    sst = read_part(zf, "xl/sharedStrings.xml", required=False)
    if sst:
        for si in parse_xml(sst, "xl/sharedStrings.xml").iter(f"{_S}si"):
            shared.append("".join((t.text or "") for t in si.iter(f"{_S}t")))
    book = parse_xml(read_part(zf, "xl/workbook.xml") or b"", "xl/workbook.xml")
    rels = {}
    rel_body = read_part(zf, "xl/_rels/workbook.xml.rels", required=False)
    if rel_body:
        for rel in parse_xml(rel_body, "a relationships part").iter(f"{_REL}Relationship"):
            rels[rel.get("Id")] = (rel.get("Target") or "").lstrip("/")
    blocks: list[dict[str, Any]] = []
    for sheet in book.iter(f"{_S}sheet"):
        name = sheet.get("name") or "Sheet"
        target = rels.get(sheet.get(f"{_R}id"), "")
        path = target if target.startswith("xl/") else f"xl/{target}"
        body = read_part(zf, path, required=False)
        if not body:
            continue
        rows: list[list[str]] = []
        for row in parse_xml(body, path).iter(f"{_S}row"):
            cells: dict[int, str] = {}
            for c in row.findall(f"{_S}c"):
                col = _column_index(c.get("r") or "A1")
                if col >= MAX_COLUMNS:
                    continue
                kind = c.get("t")
                if kind == "inlineStr":
                    value = "".join((t.text or "") for t in c.iter(f"{_S}t"))
                else:
                    v = c.find(f"{_S}v")
                    value = (v.text or "") if v is not None else ""
                    if kind == "s":
                        value = shared[int(value)] if value.isdigit() and int(value) < len(shared) else ""
                    elif kind == "e":
                        value = ""  # formula errors say nothing; formulas themselves are never read
                    elif kind == "b":
                        value = "TRUE" if value == "1" else "FALSE"
                cells[col] = " ".join(value.split())
            if cells:
                rows.append([cells.get(i, "") for i in range(max(cells) + 1)])
            if len(rows) >= MAX_ROWS:
                break
        blocks += _row_blocks(name, rows)
    return {"kind": "xlsx", "title": _core_title(zf), "blocks": blocks, "scan_pages": [], "pages": None}


# OpenDocument -----------------------------------------------------------------------------------------

_T = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
_TABLE = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
_OFFICE = "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}"
_DRAW = "{urn:oasis:names:tc:opendocument:xmlns:drawing:1.0}"
_PRES = "{urn:oasis:names:tc:opendocument:xmlns:presentation:1.0}"


def _odf_text(node: ET.Element) -> str:
    out: list[str] = []

    def walk(el: ET.Element) -> None:
        if el.text:
            out.append(el.text)
        for child in el:
            tag = _local(child.tag)
            if tag == "s":
                out.append(" " * min(int(child.get(f"{_T}c", "1") or 1), 20))
            elif tag == "tab":
                out.append("\t")
            elif tag == "line-break":
                out.append("\n")
            else:
                walk(child)
            if child.tail:
                out.append(child.tail)

    walk(node)
    return "".join(out)


def _odf_title(zf: zipfile.ZipFile) -> str:
    body = read_part(zf, "meta.xml", required=False)
    if not body:
        return ""
    try:
        for el in parse_xml(body, "meta.xml").iter():
            if _local(el.tag) == "title" and el.text:
                return " ".join(el.text.split())[:200]
    except ParseError:
        return ""
    return ""


def _odf_table(table: ET.Element) -> list[list[str]]:
    rows: list[list[str]] = []
    for row in table.iter(f"{_TABLE}table-row"):
        repeat = min(int(row.get(f"{_TABLE}number-rows-repeated", "1") or 1), MAX_REPEAT)
        cells: list[str] = []
        for cell in row:
            if _local(cell.tag) not in ("table-cell", "covered-table-cell"):
                continue
            span = min(int(cell.get(f"{_TABLE}number-columns-repeated", "1") or 1), MAX_REPEAT)
            text = " ".join(" ".join(_odf_text(p).split()) for p in cell.iter() if _local(p.tag) in ("p", "h")).strip()
            cells.extend([text] * span)
            if len(cells) >= MAX_COLUMNS:
                break
        cells = cells[:MAX_COLUMNS]
        while cells and not cells[-1]:
            cells.pop()
        if cells:
            rows.extend([cells] * repeat)
        if len(rows) >= MAX_ROWS:
            break
    return rows[:MAX_ROWS]


def parse_odt(data: bytes, limits: dict[str, Any]) -> dict[str, Any]:
    zf = open_archive(data)
    root = parse_xml(read_part(zf, "content.xml") or b"", "content.xml")
    blocks = _Blocks()
    text_root = root.find(f"{_OFFICE}body/{_OFFICE}text")

    def walk(el: ET.Element) -> None:
        for child in el:
            tag = _local(child.tag)
            if tag == "h":
                title = " ".join(_odf_text(child).split())
                if title:
                    blocks.heading(int(child.get(f"{_T}outline-level", "1") or 1), title)
            elif tag == "p":
                blocks.add(" ".join(_odf_text(child).split()))
            elif tag == "table":
                blocks.add("\n".join(" | ".join(r) for r in _odf_table(child)))
            elif tag in ("list", "list-item", "section", "list-header"):
                walk(child)

    if text_root is not None:
        walk(text_root)
    blocks.flush()
    return {"kind": "odt", "title": _odf_title(zf), "blocks": blocks.blocks, "scan_pages": [], "pages": None}


def parse_ods(data: bytes, limits: dict[str, Any]) -> dict[str, Any]:
    zf = open_archive(data)
    root = parse_xml(read_part(zf, "content.xml") or b"", "content.xml")
    blocks: list[dict[str, Any]] = []
    for table in root.iter(f"{_TABLE}table"):
        blocks += _row_blocks(table.get(f"{_TABLE}name") or "Sheet", _odf_table(table))
    return {"kind": "ods", "title": _odf_title(zf), "blocks": blocks, "scan_pages": [], "pages": None}


def parse_odp(data: bytes, limits: dict[str, Any]) -> dict[str, Any]:
    zf = open_archive(data)
    root = parse_xml(read_part(zf, "content.xml") or b"", "content.xml")
    pages = list(root.iter(f"{_DRAW}page"))
    limit = int(limits.get("max_pages", 500))
    blocks: list[dict[str, Any]] = []
    for number, page in enumerate(pages[:limit], start=1):
        notes: list[str] = []
        lines: list[str] = []
        for node in page.iter():
            if _local(node.tag) in ("p", "h"):
                text = " ".join(_odf_text(node).split())
                if text:
                    lines.append(text)
        for notes_el in page.iter(f"{_PRES}notes"):
            for p in notes_el.iter(f"{_T}p"):
                text = " ".join(_odf_text(p).split())
                if text:
                    notes.append(text)
        body = [ln for ln in lines if ln not in notes]
        heading = f"Slide {number}" + (f": {page.get(f'{_DRAW}name')}" if page.get(f"{_DRAW}name") and not re.fullmatch(r"(?i)(page|slide)\d+", page.get(f"{_DRAW}name", "")) else "")
        if body:
            blocks.append({"page": number, "heading": heading, "text": "\n".join(body)})
        if notes:
            blocks.append({"page": number, "heading": f"{heading}{HEADING_SEPARATOR}Speaker notes", "text": "\n".join(notes)})
    return {"kind": "odp", "title": _odf_title(zf), "blocks": blocks, "scan_pages": [], "pages": len(pages), "truncated": len(pages) > limit}


# PDF --------------------------------------------------------------------------------------------------


def parse_pdf(data: bytes, limits: dict[str, Any]) -> dict[str, Any]:
    """Text of every page; pages with too little text are listed in ``scan_pages`` for the vision model."""
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError

    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            raise ParseError("the PDF is encrypted; remove the password first")
        total = len(reader.pages)
    except ParseError:
        raise
    except (PyPdfError, ValueError, OSError, KeyError, TypeError, AttributeError, RecursionError):
        raise ParseError("the file is not a readable PDF") from None
    limit = int(limits.get("max_pages", 500))
    min_chars = int(limits.get("ocr_min_chars", 40))
    blocks: list[dict[str, Any]] = []
    scans: list[int] = []
    for index in range(min(total, limit)):
        try:
            text = reader.pages[index].extract_text() or ""
        except (PyPdfError, ValueError, KeyError, TypeError, AttributeError, RecursionError, IndexError):
            text = ""
        clean = "\n".join(line.rstrip() for line in text.replace("\x00", "").splitlines()).strip()
        if len(clean) < min_chars:
            scans.append(index + 1)
        if clean:
            blocks.append({"page": index + 1, "heading": "", "text": clean})
    title = ""
    try:
        title = " ".join(str(reader.metadata.title or "").split())[:200] if reader.metadata else ""
    except (PyPdfError, ValueError, KeyError, TypeError, AttributeError):
        title = ""
    return {"kind": "pdf", "title": title, "blocks": blocks, "scan_pages": scans, "pages": total, "truncated": total > limit}


def render_pdf_page(data: bytes, page: int, max_side: int = RENDER_MAX_SIDE) -> dict[str, Any]:
    """Render one page to a PNG (for a page without a text layer)."""
    import pypdfium2 as pdfium

    try:
        pdf = pdfium.PdfDocument(data)
        if page < 1 or page > len(pdf):
            raise ParseError(f"the PDF has no page {page}")
        pdf_page = pdf[page - 1]
        width, height = pdf_page.get_size()
        scale = min(2.0, max_side / max(width, height, 1.0))
        bitmap = pdf_page.render(scale=scale, rev_byteorder=True, fill_color=(255, 255, 255, 255))
        png = encode_png(bytes(bitmap.buffer), bitmap.width, bitmap.height, bitmap.stride, bitmap.n_channels)
    except ParseError:
        raise
    except Exception as exc:  # noqa: BLE001 - pdfium reports many kinds of errors; one page failing is not fatal
        raise ParseError(f"page {page} cannot be rendered ({type(exc).__name__})") from None
    return {"kind": "png", "mime": "image/png", "image_b64": base64.b64encode(png).decode("ascii"), "page": page}


def encode_png(buffer: bytes, width: int, height: int, stride: int, channels: int) -> bytes:
    """A minimal PNG writer (8-bit RGB), enough for rendered pages; no image library needed."""
    if channels not in (3, 4):
        raise ParseError("unexpected pixel format")
    rows = []
    for y in range(height):
        row = buffer[y * stride:y * stride + width * channels]
        if channels == 4:
            row = b"".join(row[i:i + 3] for i in range(0, len(row), 4))
        rows.append(b"\x00" + row)
    raw = zlib.compress(b"".join(rows), 6)

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)

    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IDAT", raw) + chunk(b"IEND", b"")


# Images: metadata removal -----------------------------------------------------------------------------

_JPEG_KEEP_APP = {0xE0, 0xEE}  # JFIF and Adobe (colour transform); EXIF, XMP, ICC, comments and the rest are dropped


def strip_jpeg(data: bytes) -> bytes:
    """Remove EXIF (including GPS), XMP, ICC, comments and other application segments from a JPEG."""
    if not data.startswith(b"\xff\xd8"):
        raise ParseError("not a JPEG")
    out = bytearray(b"\xff\xd8")
    i, seen_frame = 2, False
    while i < len(data):
        if data[i] != 0xFF:
            raise ParseError("the JPEG is damaged")
        while i < len(data) and data[i] == 0xFF:
            i += 1
        if i >= len(data):
            break
        marker = data[i]
        i += 1
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            out += bytes([0xFF, marker])
            continue
        if marker == 0xD9:
            out += b"\xff\xd9"
            break
        if i + 2 > len(data):
            raise ParseError("the JPEG is damaged")
        length = struct.unpack(">H", data[i:i + 2])[0]
        if length < 2 or i + length > len(data):
            raise ParseError("the JPEG is damaged")
        segment = data[i:i + length]
        i += length
        if 0xE0 <= marker <= 0xEF:
            if marker in _JPEG_KEEP_APP:
                out += bytes([0xFF, marker]) + segment
        elif marker == 0xFE:
            continue
        else:
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                seen_frame = True
            out += bytes([0xFF, marker]) + segment
            if marker == 0xDA:  # start of scan: the entropy-coded data and the end marker follow unchanged
                out += data[i:]
                i = len(data)
    if not seen_frame:
        raise ParseError("the JPEG has no image data")
    return bytes(out)


_PNG_KEEP = {b"IHDR", b"PLTE", b"IDAT", b"IEND", b"tRNS", b"gAMA", b"cHRM", b"sRGB", b"sBIT", b"acTL", b"fcTL", b"fdAT"}


def strip_png(data: bytes) -> bytes:
    """Keep only the chunks needed to display a PNG: text, EXIF, time and profile chunks are dropped."""
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ParseError("not a PNG")
    out = bytearray(data[:8])
    i, first, ended = 8, True, False
    while i + 12 <= len(data):
        length = struct.unpack(">I", data[i:i + 4])[0]
        kind = data[i + 4:i + 8]
        end = i + 12 + length
        if end > len(data):
            raise ParseError("the PNG is damaged")
        if first and kind != b"IHDR":
            raise ParseError("the PNG is damaged")
        first = False
        if kind in _PNG_KEEP:
            out += data[i:end]
        i = end
        if kind == b"IEND":
            ended = True
            break
    if not ended:
        raise ParseError("the PNG is damaged")
    return bytes(out)


_WEBP_KEEP = {b"VP8 ", b"VP8L", b"VP8X", b"ALPH", b"ANIM", b"ANMF"}


def strip_webp(data: bytes) -> bytes:
    """Drop the EXIF, XMP and ICC chunks of a WebP and clear their flags."""
    if not (data[:4] == b"RIFF" and data[8:12] == b"WEBP"):
        raise ParseError("not a WebP")
    declared = struct.unpack("<I", data[4:8])[0] + 8
    data = data[:min(declared, len(data))]
    chunks: list[bytes] = []
    i = 12
    while i + 8 <= len(data):
        kind = data[i:i + 4]
        size = struct.unpack("<I", data[i + 4:i + 8])[0]
        end = i + 8 + size + (size & 1)
        if i + 8 + size > len(data):
            raise ParseError("the WebP is damaged")
        if kind in _WEBP_KEEP:
            body = data[i + 8:i + 8 + size]
            if kind == b"VP8X" and body:
                body = bytes([body[0] & ~(0x20 | 0x08 | 0x04) & 0xFF]) + body[1:]
            chunks.append(kind + struct.pack("<I", len(body)) + body + (b"\x00" if len(body) & 1 else b""))
        i = end
    if not chunks:
        raise ParseError("the WebP has no image data")
    payload = b"WEBP" + b"".join(chunks)
    return b"RIFF" + struct.pack("<I", len(payload)) + payload


def parse_image(kind: str, data: bytes, limits: dict[str, Any]) -> dict[str, Any]:
    """The image without metadata, ready to be sent to the vision model."""
    stripped = {"jpeg": strip_jpeg, "png": strip_png, "webp": strip_webp}[kind](data)
    return {"kind": "image", "image_kind": kind, "mime": MIME[kind], "image_b64": base64.b64encode(stripped).decode("ascii"), "title": ""}


# Entry point ------------------------------------------------------------------------------------------

PARSERS = {"pdf": parse_pdf, "docx": parse_docx, "pptx": parse_pptx, "xlsx": parse_xlsx, "odt": parse_odt, "ods": parse_ods, "odp": parse_odp}


def run(command: dict[str, Any], data: bytes) -> dict[str, Any]:
    """Execute one command of the child process: ``parse`` or ``render``."""
    limits = command.get("limits") or {}
    try:
        kind = sniff(data)
        if command.get("cmd") == "render":
            if kind != "pdf":
                raise ParseError("not a PDF")
            return render_pdf_page(data, int(command.get("page", 1)))
        if kind is None:
            raise ParseError("the content is not a PDF, Office, OpenDocument or image file")
        if kind in IMAGE_KINDS:
            return parse_image(kind, data, limits)
        return PARSERS[kind](data, limits)
    except ParseError as exc:
        return {"error": str(exc)}
    except RecursionError:
        return {"error": "the file is nested too deeply to be read"}
    except MemoryError:
        return {"error": "the file needs more memory than the parser may use"}
