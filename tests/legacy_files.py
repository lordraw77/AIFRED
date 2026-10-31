"""Small Office 97-2003, mail and EPUB files built in memory: an OLE2 writer, a Word piece table, PowerPoint text atoms."""

from __future__ import annotations

import struct
from email.message import EmailMessage

from tests.rag_files import zipped

END, FREE, FATSECT = 0xFFFFFFFE, 0xFFFFFFFF, 0xFFFFFFFD


def make_ole(streams: dict[str, bytes]) -> bytes:
    """A version 3 compound file with ``streams`` in the root storage (small ones go through the mini stream)."""
    small = {n: d for n, d in streams.items() if len(d) < 4096}
    big = {n: d for n, d in streams.items() if len(d) >= 4096}
    mini, minifat, starts = b"", [], {}
    for name, body in small.items():
        starts[name] = len(mini) // 64
        padded = body.ljust(-(-len(body) // 64) * 64, b"\0")
        count = len(padded) // 64
        minifat += [starts[name] + k + 1 for k in range(count - 1)] + [END]
        mini += padded
    mini_sectors = -(-len(mini) // 512)
    minifat_sectors = -(-len(minifat) * 4 // 512) if minifat else 0
    dir_sectors = -(-(len(streams) + 1) // 4)
    big_sectors = {n: -(-len(d) // 512) for n, d in big.items()}
    other = dir_sectors + minifat_sectors + mini_sectors + sum(big_sectors.values())
    fat_sectors = 1
    while fat_sectors * 128 < other + fat_sectors:
        fat_sectors += 1
    fat = [FREE] * (fat_sectors * 128)
    cursor = 0
    for _ in range(fat_sectors):
        fat[cursor] = FATSECT
        cursor += 1

    def chain(count: int) -> int:
        nonlocal cursor
        first = cursor
        for k in range(count):
            fat[cursor] = cursor + 1 if k < count - 1 else END
            cursor += 1
        return first

    dir_start = chain(dir_sectors)
    minifat_start = chain(minifat_sectors) if minifat_sectors else END
    mini_start = chain(mini_sectors) if mini_sectors else END
    big_start = {n: chain(c) for n, c in big_sectors.items()}

    def entry(name: str, kind: int, start: int, size: int, right: int = FREE, child: int = FREE) -> bytes:
        raw = name.encode("utf-16-le")
        return (raw.ljust(64, b"\0") + struct.pack("<HBBIII", len(raw) + 2, kind, 1, FREE, right, child) + b"\0" * 36
                + struct.pack("<IQ", start, size)).ljust(128, b"\0")

    names = list(streams)
    entries = [entry("Root Entry", 5, mini_start, len(mini), child=1 if names else FREE)]
    for i, name in enumerate(names):
        start = starts[name] if name in small else big_start[name]
        entries.append(entry(name, 2, start, len(streams[name]), right=i + 2 if i + 1 < len(names) else FREE))
    directory = b"".join(entries).ljust(dir_sectors * 512, b"\0")
    body = struct.pack(f"<{len(fat)}I", *fat) + directory
    if minifat_sectors:
        body += struct.pack(f"<{len(minifat)}I", *minifat).ljust(minifat_sectors * 512, b"\xff")
    body += mini.ljust(mini_sectors * 512, b"\0")
    for name, data in big.items():
        body += data.ljust(big_sectors[name] * 512, b"\0")
    difat = list(range(fat_sectors)) + [FREE] * (109 - fat_sectors)
    header = (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 16 + struct.pack("<HHHHH", 0x3E, 3, 0xFFFE, 9, 6) + b"\0" * 6
              + struct.pack("<IIIIIIIII", 0, fat_sectors, dir_start, 0, 4096, minifat_start, minifat_sectors, END, 0)
              + struct.pack("<109I", *difat))
    return header.ljust(512, b"\0") + body


# Word 97-2003 ---------------------------------------------------------------------------------------------


def make_doc(pieces: list[str], encrypted: bool = False, two_byte: bool = True) -> bytes:
    """A document whose text is the given pieces joined: the first as 8-bit (cp1252), the others as UTF-16."""
    word = bytearray(0x400)
    base = 0x400
    plc_cps, pcds, text_bytes = [0], [], b""
    for i, piece in enumerate(pieces):
        compressed = i == 0 or not two_byte
        raw = piece.encode("cp1252") if compressed else piece.encode("utf-16-le")
        offset = base + len(text_bytes)
        fc = (offset * 2) | 0x40000000 if compressed else offset
        pcds.append(struct.pack("<HIH", 0, fc, 0))
        plc_cps.append(plc_cps[-1] + len(piece))
        text_bytes += raw
    plc = struct.pack(f"<{len(plc_cps)}I", *plc_cps) + b"".join(pcds)
    prc = b"\x01" + struct.pack("<H", 4) + b"\0" * 4
    clx = prc + b"\x02" + struct.pack("<I", len(plc)) + plc
    struct.pack_into("<H", word, 0, 0xA5EC)
    struct.pack_into("<H", word, 0x0A, 0x0200 | (0x0100 if encrypted else 0))
    struct.pack_into("<I", word, 0x4C, plc_cps[-1])
    struct.pack_into("<II", word, 0x1A2, 0, len(clx))
    word += text_bytes
    return make_ole({"WordDocument": bytes(word).ljust(4096, b"\0"), "1Table": clx})


# PowerPoint 97-2003 ---------------------------------------------------------------------------------------


def _atom(rtype: int, body: bytes, version: int = 0, instance: int = 0) -> bytes:
    return struct.pack("<HHI", version | (instance << 4), rtype, len(body)) + body


def make_ppt(slides: list[list[str]], textboxes: list[list[str]] | None = None) -> bytes:
    """``slides``: placeholder text per slide (in the slide list, alternating 16-bit and 8-bit atoms); ``textboxes``: text in the slide itself."""
    listing = b""
    for index, texts in enumerate(slides):
        listing += _atom(0x03F3, struct.pack("<IIII", index + 256, 0, index + 1, 0))
        for k, text in enumerate(texts):
            listing += _atom(0x0F9F, struct.pack("<I", k))
            listing += _atom(0x0FA0, text.encode("utf-16-le")) if k % 2 == 0 else _atom(0x0FA8, text.encode("cp1252"))
    stream = _atom(0x0FF0, listing, version=0xF, instance=0)
    for texts in textboxes or []:
        stream += _atom(0x03EE, _atom(0x0F00, b"".join(_atom(0x0FA0, t.encode("utf-16-le")) for t in texts), version=0xF), version=0xF)
    return make_ole({"PowerPoint Document": stream.ljust(4096, b"\0")})


# Mail and EPUB --------------------------------------------------------------------------------------------


def make_eml(subject: str, text: str, html: str | None = None, attachment: tuple[str, bytes] | None = None, html_only: bool = False) -> bytes:
    message = EmailMessage()
    message["From"], message["To"], message["Date"], message["Subject"] = "Anna <anna@example.org>", "bob@example.org", "Fri, 30 Oct 2026 10:00:00 +0000", subject
    if html_only:
        message.set_content(html or text, subtype="html")
        return message.as_bytes()
    message.set_content(text)
    if html:
        message.add_alternative(html, subtype="html")
    if attachment:
        message.add_attachment(attachment[1], maintype="application", subtype="octet-stream", filename=attachment[0])
    return message.as_bytes()


def make_epub(chapters: dict[str, str], title: str = "Manual") -> bytes:
    items = "".join(f'<item id="c{i}" href="text/{name}" media-type="application/xhtml+xml"/>' for i, name in enumerate(chapters))
    spine = "".join(f'<itemref idref="c{i}"/>' for i in range(len(chapters)))
    opf = (f'<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="2.0"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
           f"<dc:title>{title}</dc:title></metadata><manifest>{items}</manifest><spine>{spine}</spine></package>")
    container = ('<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0"><rootfiles>'
                 '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
    parts: dict[str, str | bytes] = {"META-INF/container.xml": container, "OEBPS/content.opf": opf}
    parts.update({f"OEBPS/text/{name}": body for name, body in chapters.items()})
    return zipped(parts, first=("mimetype", b"application/epub+zip"))
