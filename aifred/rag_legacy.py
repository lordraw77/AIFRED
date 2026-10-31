"""Parsers for the legacy and mail formats of the knowledge base: ``.doc``, ``.xls``, ``.ppt``, ``.eml`` and ``.epub``.

Like ``rag_parse`` this handles hostile input and runs only inside the sandboxed child process. The binary Office formats are
read with ``struct`` alone: an OLE2 (compound file) container reader, the piece table of a Word 97+ document, the BIFF8 records of
an Excel 97+ workbook and the text atoms of a PowerPoint 97+ presentation. Only the text is extracted (no formatting, no
pictures, no macros, no embedded objects). Files made before Office 97 and encrypted files are refused with a clear message.
"""

from __future__ import annotations

import html
import re
import struct
from email import policy
from email.parser import BytesParser
from typing import Any
from urllib.parse import unquote

from aifred.rag_parse import (
    HEADING_SEPARATOR,
    MAX_COLUMNS,
    MAX_ROWS,
    ParseError,
    _Blocks,
    _local,
    _row_blocks,
    open_archive,
    parse_xml,
    read_part,
)

OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
FREE, END_OF_CHAIN = 0xFFFFFFFF, 0xFFFFFFFE
MAX_STREAM = 40 * 1024 * 1024
MAX_DIRECTORY_ENTRIES = 4096


# OLE2 container ---------------------------------------------------------------------------------------


class Ole:
    """A read-only OLE2 compound file: the streams of the root storage by name."""

    def __init__(self, data: bytes) -> None:
        if len(data) < 512 or not data.startswith(OLE_MAGIC):
            raise ParseError("the file is not a valid Office 97-2003 document")
        self._data = data
        shift, mini_shift = struct.unpack_from("<HH", data, 0x1E)
        if shift not in (9, 12) or mini_shift != 6:
            raise ParseError("the file is not a valid Office 97-2003 document")
        self._ss, self._mss = 1 << shift, 1 << mini_shift
        fat_count, dir_start, _, cutoff, minifat_start, minifat_count, difat_start, difat_count = struct.unpack_from("<IIIIIIII", data, 0x2C)
        self._cutoff = cutoff
        self._sector_count = max(0, (len(data) - 512) // self._ss)
        difat = list(struct.unpack_from("<109I", data, 0x4C))
        sector, seen = difat_start, 0
        while sector not in (END_OF_CHAIN, FREE) and seen < min(difat_count, 4096):
            body = self._sector(sector)
            difat += struct.unpack_from(f"<{self._ss // 4 - 1}I", body)
            sector = struct.unpack_from("<I", body, self._ss - 4)[0]
            seen += 1
        self._fat: list[int] = []
        for sector in difat[:fat_count]:
            if sector not in (FREE, END_OF_CHAIN):
                self._fat += struct.unpack_from(f"<{self._ss // 4}I", self._sector(sector))
        directory = self._chain(dir_start)
        if len(directory) > MAX_DIRECTORY_ENTRIES * 128:
            raise ParseError("the document directory is too large")
        self._entries: dict[str, tuple[int, int]] = {}
        root_start = root_size = 0
        for offset in range(0, len(directory) - 127, 128):
            name_len, kind = struct.unpack_from("<HB", directory, offset + 64)
            if kind not in (1, 2, 5) or not 2 <= name_len <= 64:
                continue
            name = directory[offset:offset + name_len - 2].decode("utf-16-le", "replace")
            start, size = struct.unpack_from("<IQ", directory, offset + 116)
            if self._ss == 512:
                size &= 0xFFFFFFFF
            if kind == 5 and not root_size:
                root_start, root_size = start, size
            elif kind == 2:
                self._entries.setdefault(name, (start, size))
        self._mini: bytes | None = None
        self._minifat: list[int] = []
        if minifat_count and minifat_start not in (END_OF_CHAIN, FREE):
            raw = self._chain(minifat_start)
            self._minifat = list(struct.unpack_from(f"<{len(raw) // 4}I", raw))
            self._mini = self._chain(root_start, root_size) if root_size else b""

    def _sector(self, index: int) -> bytes:
        if index >= self._sector_count:
            raise ParseError("the document is truncated or damaged")
        start = 512 + index * self._ss
        return self._data[start:start + self._ss]

    def _chain(self, start: int, size: int | None = None) -> bytes:
        parts, seen, sector = [], set(), start
        while sector not in (END_OF_CHAIN, FREE):
            if sector in seen or sector >= len(self._fat):
                raise ParseError("the document is damaged")
            seen.add(sector)
            parts.append(self._sector(sector))
            if len(seen) * self._ss > MAX_STREAM + self._ss:
                raise ParseError("the document stream is too large")
            sector = self._fat[sector]
        body = b"".join(parts)
        return body if size is None else body[:size]

    def names(self) -> set[str]:
        return set(self._entries)

    def stream(self, name: str) -> bytes | None:
        """The content of a root-level stream, or ``None`` when there is none by that name."""
        entry = self._entries.get(name)
        if entry is None:
            return None
        start, size = entry
        if size > MAX_STREAM:
            raise ParseError("the document stream is too large")
        if size >= self._cutoff or self._mini is None:
            return self._chain(start, size)
        parts, seen, sector = [], set(), start
        while sector not in (END_OF_CHAIN, FREE):
            if sector in seen or sector >= len(self._minifat):
                raise ParseError("the document is damaged")
            seen.add(sector)
            parts.append(self._mini[sector * self._mss:(sector + 1) * self._mss])
            sector = self._minifat[sector]
        return b"".join(parts)[:size]


def ole_kind(data: bytes) -> str | None:
    """``doc``, ``xls`` or ``ppt`` for an OLE2 file, by the streams it holds; ``None`` for any other container."""
    try:
        names = Ole(data).names()
    except (ParseError, struct.error):
        return None
    if "WordDocument" in names:
        return "doc"
    if "Workbook" in names or "Book" in names:
        return "xls"
    if "PowerPoint Document" in names:
        return "ppt"
    return None


# doc --------------------------------------------------------------------------------------------------

_FIELD_CODE = re.compile(r"\x13[^\x14\x15]*\x14?")
_CONTROL = re.compile(r"[\x00-\x06\x08\x0e-\x1f]")


def _doc_text(word: bytes, table: bytes) -> str:
    if len(word) < 0x1A6 or struct.unpack_from("<H", word, 0)[0] not in (0xA5EC, 0xA5DC):
        raise ParseError("the file is not a Word 97-2003 document (older Word formats are not supported)")
    if struct.unpack_from("<H", word, 0x0A)[0] & 0x0100:
        raise ParseError("the document is encrypted; remove the password first")
    ccp_text = struct.unpack_from("<I", word, 0x4C)[0]
    fc_clx, lcb_clx = struct.unpack_from("<II", word, 0x1A2)
    clx = table[fc_clx:fc_clx + lcb_clx]
    pos = 0
    while pos + 3 <= len(clx) and clx[pos] == 0x01:           # Prc: skip the property modifiers
        pos += 3 + struct.unpack_from("<H", clx, pos + 1)[0]
    if pos + 5 > len(clx) or clx[pos] != 0x02:
        raise ParseError("the document has no readable text")
    size = struct.unpack_from("<I", clx, pos + 1)[0]
    plc = clx[pos + 5:pos + 5 + size]
    pieces = (len(plc) - 4) // 12
    if pieces < 1 or pieces > 100_000:
        raise ParseError("the document has no readable text")
    cps = struct.unpack_from(f"<{pieces + 1}I", plc)
    out: list[str] = []
    total = 0
    for i in range(pieces):
        fc = struct.unpack_from("<I", plc, (pieces + 1) * 4 + i * 8 + 2)[0]
        count = min(cps[i + 1] - cps[i], ccp_text - total) if cps[i + 1] >= cps[i] else 0
        if count <= 0:
            break
        if fc & 0x40000000:
            start = (fc & 0x3FFFFFFF) // 2
            out.append(word[start:start + count].decode("cp1252", "replace"))
        else:
            start = fc & 0x3FFFFFFF
            out.append(word[start:start + count * 2].decode("utf-16-le", "replace"))
        total += count
    return "".join(out)


def parse_doc(data: bytes, limits: dict[str, Any]) -> dict[str, Any]:
    ole = Ole(data)
    word = ole.stream("WordDocument")
    if word is None or len(word) < 0x0C:
        raise ParseError("the file is not a Word 97-2003 document")
    table = ole.stream("1Table" if struct.unpack_from("<H", word, 0x0A)[0] & 0x0200 else "0Table")
    if table is None:
        raise ParseError("the document has no readable text")
    text = _FIELD_CODE.sub("", _doc_text(word, table)).replace("\x07", " | ").replace("\x0b", "\n").replace("\x0c", "\n").replace("\x15", "")
    blocks = _Blocks()
    for paragraph in re.split(r"\r+", _CONTROL.sub("", text)):
        blocks.add("\n".join(" ".join(line.split()).strip(" |") for line in paragraph.split("\n")))
    blocks.flush()
    return {"kind": "doc", "title": "", "blocks": blocks.blocks, "scan_pages": [], "pages": None}


# xls --------------------------------------------------------------------------------------------------

_BOF, _EOF, _BOUNDSHEET, _SST, _CONTINUE, _FILEPASS = 0x0809, 0x000A, 0x0085, 0x00FC, 0x003C, 0x002F
_LABELSST, _LABEL, _NUMBER, _RK, _MULRK, _FORMULA, _STRING, _BOOLERR = 0x00FD, 0x0204, 0x0203, 0x027E, 0x00BD, 0x0006, 0x0207, 0x0205


def _records(stream: bytes):
    pos = 0
    while pos + 4 <= len(stream):
        rid, length = struct.unpack_from("<HH", stream, pos)
        yield pos, rid, stream[pos + 4:pos + 4 + length]
        pos += 4 + length


def _number(value: float) -> str:
    if value != value or value in (float("inf"), float("-inf")):
        return ""
    return str(int(value)) if value == int(value) and abs(value) < 1e15 else f"{value:.15g}"


def _rk(raw: int) -> float:
    if raw & 2:
        value = float(struct.unpack("<i", struct.pack("<I", raw & 0xFFFFFFFC))[0] >> 2)
    else:
        value = struct.unpack("<d", struct.pack("<Q", (raw & 0xFFFFFFFC) << 32))[0]
    return value / 100 if raw & 1 else value


class _Chunks:
    """Reads the shared string table, which a CONTINUE record may cut in the middle of a string."""

    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks, self.i, self.p = chunks, 0, 0

    def _room(self) -> int:
        return len(self.chunks[self.i]) - self.p if self.i < len(self.chunks) else 0

    def take(self, n: int) -> bytes:
        out = b""
        while n > 0 and self.i < len(self.chunks):
            piece = self.chunks[self.i][self.p:self.p + n]
            out, n, self.p = out + piece, n - len(piece), self.p + len(piece)
            if self.p >= len(self.chunks[self.i]):
                self.i, self.p = self.i + 1, 0
        return out

    def more(self) -> bool:
        return self._room() > 0

    def string(self) -> str:
        head = self.take(3)
        if len(head) < 3:
            return ""
        count, flags = struct.unpack("<HB", head)
        runs = struct.unpack("<H", self.take(2).ljust(2, b"\0"))[0] if flags & 0x08 else 0
        phonetic = struct.unpack("<I", self.take(4).ljust(4, b"\0"))[0] if flags & 0x04 else 0
        wide, text, remaining = bool(flags & 0x01), [], count
        while remaining > 0 and self.i < len(self.chunks):
            width = 2 if wide else 1
            fit = min(remaining, self._room() // width)
            text.append(self.take(fit * width).decode("utf-16-le" if wide else "latin-1", "replace"))
            remaining -= fit
            if remaining > 0:                                # a CONTINUE record repeats the encoding flag
                flag = self.take(1)
                wide = bool(flag and flag[0] & 0x01)
        self.take(runs * 4 + min(phonetic, 1 << 20))
        return "".join(text)


def _shared_strings(chunks: list[bytes]) -> list[str]:
    reader = _Chunks(chunks)
    head = reader.take(8)
    if len(head) < 8:
        return []
    unique = struct.unpack("<II", head)[1]
    out: list[str] = []
    while len(out) < min(unique, 2_000_000) and reader.more():
        out.append(reader.string())
    return out


def _unicode_string(raw: bytes, offset: int, long_count: bool = False) -> str:
    size = 2 if long_count else 1
    count = int.from_bytes(raw[offset:offset + size], "little")
    flags = raw[offset + size] if len(raw) > offset + size else 0
    body = raw[offset + size + 1:]
    return body[:count * 2].decode("utf-16-le", "replace") if flags & 1 else body[:count].decode("latin-1", "replace")


def parse_xls(data: bytes, limits: dict[str, Any]) -> dict[str, Any]:
    ole = Ole(data)
    book = ole.stream("Workbook") or ole.stream("Book")
    if book is None or len(book) < 8:
        raise ParseError("the file is not an Excel 97-2003 workbook")
    if struct.unpack_from("<H", book, 0)[0] != _BOF or struct.unpack_from("<H", book, 4)[0] < 0x0600:
        raise ParseError("the workbook was made before Excel 97 and is not supported")
    names: dict[int, str] = {}
    sst: list[bytes] = []
    strings: list[str] = []
    in_sst = False
    sheets: list[tuple[str, dict[tuple[int, int], str]]] = []
    cells: dict[tuple[int, int], str] | None = None
    depth = 0
    pending: tuple[int, int] | None = None
    for pos, rid, body in _records(book):
        if rid == _FILEPASS:
            raise ParseError("the workbook is encrypted; remove the password first")
        if in_sst and rid == _CONTINUE:
            sst.append(body)
            continue
        if in_sst:
            in_sst, strings = False, _shared_strings(sst)
        if rid == _SST:
            in_sst, sst = True, [body]
        elif rid == _BOUNDSHEET and len(body) >= 8:
            names[struct.unpack_from("<I", body, 0)[0]] = _unicode_string(body, 6)
        elif rid == _BOF and len(body) >= 4:
            if depth == 0 and struct.unpack_from("<H", body, 2)[0] == 0x0010:
                cells = {}
                sheets.append((names.get(pos) or f"Sheet{len(sheets) + 1}", cells))
            elif depth == 0:
                cells = None
            depth += 1
        elif rid == _EOF:
            depth = max(0, depth - 1)
            if depth == 0:
                cells = None
        elif cells is not None and len(cells) < MAX_ROWS * MAX_COLUMNS:
            if rid == _LABELSST and len(body) >= 10:
                row, col, _, index = struct.unpack_from("<HHHI", body)
                if index < len(strings):
                    cells[(row, col)] = strings[index]
            elif rid == _LABEL and len(body) >= 8:
                row, col = struct.unpack_from("<HH", body)
                cells[(row, col)] = _unicode_string(body, 6, long_count=True)
            elif rid == _NUMBER and len(body) >= 14:
                row, col = struct.unpack_from("<HH", body)
                cells[(row, col)] = _number(struct.unpack_from("<d", body, 6)[0])
            elif rid == _RK and len(body) >= 10:
                row, col = struct.unpack_from("<HH", body)
                cells[(row, col)] = _number(_rk(struct.unpack_from("<I", body, 6)[0]))
            elif rid == _MULRK and len(body) >= 6:
                row, first = struct.unpack_from("<HH", body)
                for k in range((len(body) - 6) // 6):
                    cells[(row, first + k)] = _number(_rk(struct.unpack_from("<I", body, 6 + k * 6 + 2)[0]))
            elif rid == _BOOLERR and len(body) >= 8:
                row, col = struct.unpack_from("<HH", body)
                cells[(row, col)] = ("TRUE" if body[6] else "FALSE") if body[7] == 0 else "#ERROR"
            elif rid == _FORMULA and len(body) >= 14:
                row, col = struct.unpack_from("<HH", body)
                if body[12:14] == b"\xff\xff":
                    pending = (row, col) if body[6] == 0 else None    # a string result follows in a STRING record
                else:
                    cells[(row, col)] = _number(struct.unpack_from("<d", body, 6)[0])
                    pending = None
            elif rid == _STRING and pending is not None:
                cells[pending] = _unicode_string(body, 0, long_count=True)
                pending = None
    blocks: list[dict[str, Any]] = []
    for name, grid in sheets:
        by_row: dict[int, dict[int, str]] = {}
        for (r, c), value in grid.items():
            if c < MAX_COLUMNS:
                by_row.setdefault(r, {})[c] = " ".join(value.split())
        table = [[row.get(c, "") for c in range(max(row) + 1)] for _, row in sorted(by_row.items())[:MAX_ROWS]]
        if table:
            blocks += _row_blocks(name, table)
    return {"kind": "xls", "title": "", "blocks": blocks, "scan_pages": [], "pages": None}


# ppt --------------------------------------------------------------------------------------------------

_SLIDE, _SLWT, _PERSIST, _TEXT_CHARS, _TEXT_BYTES = 0x03EE, 0x0FF0, 0x03F3, 0x0FA0, 0x0FA8


def _ppt_walk(stream: bytes, start: int, end: int, depth: int, out: dict[str, Any], mode: str) -> None:
    if depth > 24:
        raise ParseError("the presentation is nested too deeply to be read")
    pos = start
    while pos + 8 <= end:
        ver_inst, rtype, length = struct.unpack_from("<HHI", stream, pos)
        body, stop = pos + 8, min(pos + 8 + length, end)
        if rtype == _PERSIST and mode == "list":
            out["list"].append([])
        elif rtype in (_TEXT_CHARS, _TEXT_BYTES) and mode in ("list", "slide"):
            raw = stream[body:stop]
            text = raw.decode("utf-16-le", "replace") if rtype == _TEXT_CHARS else raw.decode("cp1252", "replace")
            text = "\n".join(line.strip() for line in re.split(r"[\r\x0b\n]+", text) if line.strip())
            target = out["list"] if mode == "list" else out["slides"]
            if text and target:
                target[-1].append(text)
        if ver_inst & 0x000F == 0x000F:                       # a container
            if rtype == _SLIDE:
                out["slides"].append([])
                _ppt_walk(stream, body, stop, depth + 1, out, "slide")
            elif rtype == _SLWT:
                _ppt_walk(stream, body, stop, depth + 1, out, "list" if (ver_inst >> 4) == 0 else "notes")
            else:
                _ppt_walk(stream, body, stop, depth + 1, out, mode)
        pos = stop if stop > pos else pos + 8


def parse_ppt(data: bytes, limits: dict[str, Any]) -> dict[str, Any]:
    stream = Ole(data).stream("PowerPoint Document")
    if stream is None:
        raise ParseError("the file is not a PowerPoint 97-2003 presentation")
    out: dict[str, Any] = {"list": [], "slides": []}
    _ppt_walk(stream, 0, len(stream), 0, out, "none")
    limit = int(limits.get("max_pages", 500))
    blocks: list[dict[str, Any]] = []
    total = max(len(out["list"]), len(out["slides"]))
    for index in range(min(total, limit)):
        lines: list[str] = []
        for text in (out["list"][index] if index < len(out["list"]) else []) + (out["slides"][index] if index < len(out["slides"]) else []):
            if text not in lines:
                lines.append(text)
        if lines:
            blocks.append({"page": index + 1, "heading": f"Slide {index + 1}", "text": "\n".join(lines)})
    return {"kind": "ppt", "title": "", "blocks": blocks, "scan_pages": [], "pages": total, "truncated": total > limit}


# eml --------------------------------------------------------------------------------------------------

_HEADER_LINE = re.compile(rb"^(From|To|Subject|Date|Message-ID|Received|MIME-Version|Return-Path|Content-Type):", re.I | re.M)
MAX_PARTS = 200


def looks_like_eml(data: bytes) -> bool:
    """True for a message: an RFC 5322 header block at the start with at least two well-known header lines."""
    head = data[:4096].split(b"\n\n", 1)[0].split(b"\r\n\r\n", 1)[0]
    return len(_HEADER_LINE.findall(head)) >= 2 and not head.lstrip().startswith(b"<")


def _mail_text(message: Any) -> str:
    plain: list[str] = []
    rich: list[str] = []
    for count, part in enumerate(message.walk(), start=1):
        if count > MAX_PARTS:
            break
        kind = part.get_content_type()
        if part.is_multipart() or part.get_content_disposition() == "attachment" or kind not in ("text/plain", "text/html"):
            continue
        try:
            text = part.get_content()
        except (LookupError, ValueError, UnicodeError):
            continue
        if kind == "text/plain":
            plain.append(text)
        else:
            from aifred.tools.web import html_to_text

            rich.append(html_to_text(text)[1])
    return "\n\n".join(plain or rich)


def parse_eml(data: bytes, limits: dict[str, Any]) -> dict[str, Any]:
    try:
        message = BytesParser(policy=policy.default).parsebytes(data)
        subject = " ".join(str(message.get("subject", "")).split())[:200]
        header = [f"{name}: {' '.join(str(message.get(name, '')).split())}" for name in ("From", "To", "Cc", "Date", "Subject") if message.get(name)]
        body = _mail_text(message)
        names = [str(p.get_filename()) for p in message.walk() if p.get_content_disposition() == "attachment" and p.get_filename()][:20]
    except (ValueError, LookupError, UnicodeError, AttributeError, TypeError, KeyError):
        raise ParseError("the file is not a readable e-mail message") from None
    blocks = [{"page": None, "heading": subject, "text": "\n".join(header)}] if header else []
    if body.strip():
        blocks.append({"page": None, "heading": subject, "text": body.strip()})
    if names:
        blocks.append({"page": None, "heading": f"{subject}{HEADING_SEPARATOR}Attachments" if subject else "Attachments", "text": "\n".join(names)})
    return {"kind": "eml", "title": subject, "blocks": blocks, "scan_pages": [], "pages": None}


# epub -------------------------------------------------------------------------------------------------

_BLOCK_TAGS = {"p", "li", "blockquote", "pre", "td", "th", "dd", "dt", "figcaption"}


def _epub_spine(zf) -> tuple[list[str], str]:
    container = parse_xml(read_part(zf, "META-INF/container.xml") or b"", "container.xml")
    path = next((el.get("full-path") for el in container.iter() if _local(el.tag) == "rootfile" and el.get("full-path")), None)
    if not path:
        raise ParseError("the EPUB has no package file")
    package = parse_xml(read_part(zf, path) or b"", path)
    base = path.rsplit("/", 1)[0] + "/" if "/" in path else ""
    manifest = {el.get("id"): el.get("href") for el in package.iter() if _local(el.tag) == "item" and el.get("href")}
    title = next(((el.text or "").strip() for el in package.iter() if _local(el.tag) == "title"), "")
    order = [manifest[el.get("idref")] for el in package.iter() if _local(el.tag) == "itemref" and el.get("idref") in manifest]
    return [base + unquote(href.split("#")[0]) for href in order], " ".join(title.split())[:200]


_DOCTYPE = re.compile(rb"<!DOCTYPE[^>\[]*>", re.I)
_NAMED_ENTITY = re.compile(rb"&(?!(?:amp|lt|gt|quot|apos);)([A-Za-z][A-Za-z0-9]{1,31});")


def _xhtml(raw: bytes, what: str):
    """Parse a chapter: EPUB 2 chapters carry a plain DOCTYPE and HTML entities such as ``&nbsp;``, an internal subset is refused."""
    raw = _DOCTYPE.sub(b"", raw, count=1)
    raw = _NAMED_ENTITY.sub(lambda m: html.unescape(m.group(0).decode("ascii")).encode("utf-8") if html.unescape(m.group(0).decode("ascii")) != m.group(0).decode("ascii") else b"", raw)
    return parse_xml(raw, what)


def parse_epub(data: bytes, limits: dict[str, Any]) -> dict[str, Any]:
    zf = open_archive(data)
    spine, title = _epub_spine(zf)
    blocks = _Blocks()
    limit = int(limits.get("max_pages", 500))
    for number, href in enumerate(spine[:limit], start=1):
        raw = read_part(zf, href, required=False)
        if not raw:
            continue
        try:
            root = _xhtml(raw, href)
        except ParseError:
            continue
        blocks.page = number
        for el in root.iter():
            tag = _local(el.tag).lower()
            if re.fullmatch(r"h[1-6]", tag):
                text = " ".join("".join(el.itertext()).split())
                if text:
                    blocks.heading(int(tag[1]), text)
            elif tag in _BLOCK_TAGS and not any(_local(c.tag).lower() in _BLOCK_TAGS for c in el):
                blocks.add(" ".join(html.unescape("".join(el.itertext())).split()))
        blocks.flush()
    return {"kind": "epub", "title": title, "blocks": blocks.blocks, "scan_pages": [], "pages": len(spine), "truncated": len(spine) > limit}


PARSERS = {"doc": parse_doc, "xls": parse_xls, "ppt": parse_ppt, "eml": parse_eml, "epub": parse_epub}
