"""Tests for the parsers (aifred.rag_parse) and the sandbox that runs them (aifred.rag_sandbox)."""

from __future__ import annotations

import base64
import io
import json
import os
import struct
import zipfile
import zlib
from typing import Any

import pypdf
import pytest

from aifred import rag_sandbox
from aifred.rag_parse import (
    MAX_ENTRIES, ParseError, encode_png, magic_kind, open_archive, parse_xml, read_part, run, sniff, strip_jpeg, strip_png, strip_webp,
)
from aifred.rag_sandbox import SandboxError, run_parser
from tests import rag_files as f

LIMITS = {"max_pages": 500, "ocr_min_chars": 40}


def parse(data: bytes, **limits: int) -> dict[str, Any]:
    return run({"cmd": "parse", "limits": {**LIMITS, **limits}}, data)


# Type by bytes


def test_the_type_is_decided_by_the_bytes() -> None:
    assert sniff(f.make_pdf(["x"])) == "pdf" and sniff(f.make_jpeg()) == "jpeg" and sniff(f.make_png()) == "png" and sniff(f.make_webp()) == "webp"
    assert sniff(f.make_docx([("", "x")])) == "docx" and sniff(f.make_xlsx({"S": [["a"]]})) == "xlsx" and sniff(f.make_pptx([{"title": "t", "body": "b"}])) == "pptx"
    assert sniff(f.make_odt([("p", "x")])) == "odt" and sniff(f.make_ods({"S": [["a"]]})) == "ods" and sniff(f.make_odp([{"body": "b"}])) == "odp"
    assert sniff(b"just some text called holiday.png") is None and sniff(b"") is None and sniff(f.zipped({"a.txt": "x"})) is None
    assert magic_kind(f.make_pdf(["x"])[:16]) == "pdf" and magic_kind(f.make_png()[:16]) == "image" and magic_kind(f.make_docx([("", "x")])[:16]) == "zip"
    assert magic_kind(b"plain text") is None
    refused = parse(b"plain text pretending to be a picture")
    assert "not a PDF, Office" in refused["error"]


# Office


def test_docx_headings_tables_and_title() -> None:
    result = parse(f.make_docx([("Heading1", "Guide"), ("", "Intro text."), ("Heading2", "Install"), ("", "Run it."), ("Heading1", "Appendix"), ("", "Extra")], [["host", "ip"], ["nas", "10.0.0.5"]], "Handbook"))
    assert result["title"] == "Handbook"
    assert [(b["heading"], b["text"]) for b in result["blocks"]] == [
        ("Guide", "Intro text."), ("Guide › Install", "Run it."), ("Appendix", "Extra\n\nhost | ip\nnas | 10.0.0.5"),
    ]


def test_xlsx_values_not_formulas_and_header_repeats() -> None:
    rows: list[list[object]] = [["name", "ip"], *[[f"host{i}", f"10.0.0.{i}"] for i in range(250)]]
    result = parse(f.make_xlsx({"Hosts": rows, "Empty": []}, formulas=True))
    texts = [b["text"] for b in result["blocks"]]
    assert all(b["heading"] == "Hosts" for b in result["blocks"]) and len(texts) == 3  # 250 rows in blocks of 100
    assert all(t.startswith("name | ip\n") for t in texts) and "host249 | 10.0.0.249 | 42" in texts[-1]  # the cached value of the formula, never "SUM("
    assert not any("SUM(" in t for t in texts)


def test_pptx_slides_titles_and_speaker_notes() -> None:
    result = parse(f.make_pptx([{"title": "Intro", "body": "Welcome", "notes": "Mention the budget"}, {"title": "Plan", "body": "Phase one"}]))
    assert result["pages"] == 2
    assert [(b["page"], b["heading"], b["text"]) for b in result["blocks"]] == [
        (1, "Slide 1: Intro", "Welcome"), (1, "Slide 1: Intro › Speaker notes", "Mention the budget"), (2, "Slide 2: Plan", "Phase one"),
    ]
    assert parse(f.make_pptx([{"title": f"S{i}", "body": "b"} for i in range(5)]), max_pages=2)["truncated"] is True


def test_opendocument_files() -> None:
    odt = parse(f.make_odt([("h1", "Guide"), ("p", "Text"), ("h2", "Sub"), ("p", "More")], [["x", "y"]], "Doc"))
    assert odt["title"] == "Doc" and [(b["heading"], b["text"]) for b in odt["blocks"]] == [("Guide", "Text"), ("Guide › Sub", "More\n\nx | y")]
    ods = parse(f.make_ods({"Hosts": [["name", "ip"], ["nas", "10.0.0.5"]]}))
    assert ods["blocks"] == [{"page": None, "heading": "Hosts", "text": "name | ip\nnas | 10.0.0.5"}]
    odp = parse(f.make_odp([{"body": "One", "notes": "N1"}, {"body": "Two", "name": "Agenda"}]))
    assert [(b["page"], b["heading"], b["text"]) for b in odp["blocks"]] == [(1, "Slide 1", "One"), (1, "Slide 1 › Speaker notes", "N1"), (2, "Slide 2: Agenda", "Two")]


# Hostile archives and XML


def test_archive_defences() -> None:
    ratio = f.zipped({"word/document.xml": "<w:document xmlns:w='x'><w:body>" + "A" * 20_000_000 + "</w:body></w:document>"})
    assert len(ratio) < 100_000 and "decompression bomb" in parse(ratio)["error"]
    huge = f.zipped({"word/document.xml": "<w:document xmlns:w='x'><w:body>" + "A" * 30_000_000 + "</w:body></w:document>"})
    assert "too large" in parse(huge)["error"]
    sums = f.zipped({f"word/part{i}.bin": os.urandom(5_000_000) for i in range(14)} | {"word/document.xml": "<x/>"})
    assert "expands to more than" in parse(sums)["error"]
    many = f.zipped({f"word/file{i}.xml": "x" for i in range(MAX_ENTRIES + 5)} | {"word/document.xml": "<x/>"})
    assert "too many entries" in parse(many)["error"]
    with pytest.raises(ParseError, match="not a valid archive"):
        open_archive(b"PK\x03\x04 truncated")
    plain = bytearray(f.zipped({"word/document.xml": "<w:document xmlns:w='x'/>"}))
    plain[plain.index(b"PK\x01\x02") + 8] |= 0x01  # the central directory says the entry is encrypted
    assert "encrypted" in parse(bytes(plain))["error"]
    # entries that climb out of the archive are never read
    sneaky = f.zipped({"../word/document.xml": "<x/>", "word/document.xml": "<w:document xmlns:w='x'/>"})
    zf = zipfile.ZipFile(io.BytesIO(sneaky))
    assert read_part(zf, "../word/document.xml", required=False) is None and read_part(zf, "/etc/passwd", required=False) is None


def test_xml_with_doctype_or_entities_is_refused() -> None:
    laugh = b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;">]><lolz>&lol2;</lolz>'
    with pytest.raises(ParseError, match="DOCTYPE or entities"):
        parse_xml(laugh)
    with pytest.raises(ParseError, match="DOCTYPE or entities"):
        parse_xml(b"<!doctype x><x/>")
    with pytest.raises(ParseError, match="entities"):
        parse_xml(b'<x><!ENTITY a "b"></x>')
    with pytest.raises(ParseError, match="encoding"):
        parse_xml("<x/>".encode("utf-16"))
    with pytest.raises(ParseError, match="not valid XML"):
        parse_xml(b"<x><unclosed>")
    docx = f.zipped({"word/document.xml": '<!DOCTYPE d [<!ENTITY e "boom">]><w:document xmlns:w="x"><w:body><w:p><w:r><w:t>&e;</w:t></w:r></w:p></w:body></w:document>', "[Content_Types].xml": "<Types/>"})
    assert "DOCTYPE" in parse(docx)["error"]


# PDF


def test_pdf_text_pages_and_scans() -> None:
    result = parse(f.make_pdf(["First page has enough text to count as text.", None, "Third page also has a good amount of text.", "tiny"]))
    assert [(b["page"], b["text"][:5]) for b in result["blocks"]] == [(1, "First"), (3, "Third"), (4, "tiny")]
    assert result["scan_pages"] == [2, 4] and result["pages"] == 4 and result["truncated"] is False
    assert parse(f.make_pdf(["a long enough page of text to be read"] * 5), max_pages=2)["truncated"] is True
    assert parse(f.make_pdf(["tiny"]), ocr_min_chars=0)["scan_pages"] == []


def test_encrypted_and_broken_pdfs() -> None:
    writer = pypdf.PdfWriter()
    writer.add_blank_page(100, 100)
    writer.encrypt("secret")
    buffer = io.BytesIO()
    writer.write(buffer)
    assert "encrypted" in parse(buffer.getvalue())["error"]
    assert "not a readable PDF" in parse(b"%PDF-1.4\nthis is not really a pdf")["error"]


def test_a_page_is_rendered_to_a_png() -> None:
    result = run({"cmd": "render", "page": 2}, f.make_pdf(["text", None]))
    png = base64.b64decode(result["image_b64"])
    width, height = struct.unpack(">II", png[16:24])
    assert png.startswith(b"\x89PNG") and result["mime"] == "image/png" and 100 < width <= 1600 and 100 < height <= 1600
    assert "no page 9" in run({"cmd": "render", "page": 9}, f.make_pdf(["x"]))["error"]
    assert "not a PDF" in run({"cmd": "render", "page": 1}, f.make_png())["error"]
    with pytest.raises(ParseError):
        encode_png(b"\x00", 1, 1, 1, 1)


# Images without private data


def test_jpeg_loses_exif_gps_xmp_and_comments() -> None:
    original = f.make_jpeg()
    assert f.GPS_MARK in original and f.SECRET_COMMENT in original and b"owner" in original
    clean = strip_jpeg(original)
    assert f.GPS_MARK not in clean and f.SECRET_COMMENT not in clean and b"owner" not in clean and b"Exif" not in clean and b"xmpmeta" not in clean
    assert clean.startswith(b"\xff\xd8\xff\xe0") and b"JFIF" in clean and clean.endswith(b"PIXELS\xff\xd9")  # JFIF, the scan and its end marker stay
    assert b"\xff\xc0" in clean and b"\xff\xdb" in clean  # frame header and quantisation table
    assert len(clean) < len(original)


def test_png_keeps_only_what_is_needed() -> None:
    original = f.make_png()
    assert f.GPS_MARK in original
    clean = strip_png(original)
    assert f.GPS_MARK not in clean and b"tEXt" not in clean and b"eXIf" not in clean and b"tIME" not in clean
    kinds = []
    i = 8
    while i < len(clean):
        length = struct.unpack(">I", clean[i:i + 4])[0]
        kinds.append(clean[i + 4:i + 8])
        body = clean[i + 8:i + 8 + length]
        assert struct.unpack(">I", clean[i + 8 + length:i + 12 + length])[0] == zlib.crc32(clean[i + 4:i + 8] + body) & 0xFFFFFFFF  # checksums are intact
        i += 12 + length
    assert kinds[0] == b"IHDR" and kinds[-1] == b"IEND" and b"IDAT" in kinds


def test_webp_loses_exif_xmp_and_their_flags() -> None:
    original = f.make_webp()
    assert f.GPS_MARK in original
    clean = strip_webp(original)
    assert f.GPS_MARK not in clean and b"EXIF" not in clean and b"XMP " not in clean and b"owner" not in clean
    assert clean[:4] == b"RIFF" and clean[8:12] == b"WEBP" and struct.unpack("<I", clean[4:8])[0] == len(clean) - 8
    vp8x = clean.index(b"VP8X") + 8
    assert clean[vp8x] & (0x20 | 0x08 | 0x04) == 0 and clean[vp8x] & 0x10 == 0x10  # EXIF, XMP, ICC cleared; the alpha flag stays
    assert b"PIXELDATA!" in clean


def test_damaged_images_are_refused() -> None:
    for fn, data in ((strip_jpeg, b"\xff\xd8\xff\xe1\x00\x02"), (strip_jpeg, b"\xff\xd8\xff\xd9"), (strip_png, f.make_png()[:40]),
                     (strip_png, b"\x89PNG\r\n\x1a\n" + b"\x00" * 20), (strip_webp, b"RIFF\x04\x00\x00\x00WEBP"), (strip_jpeg, b"not a jpeg")):
        with pytest.raises(ParseError):
            fn(data)
    assert run({"cmd": "parse"}, f.make_jpeg()[:30])["error"]


def test_images_are_returned_clean_through_run() -> None:
    for data, secret in ((f.make_jpeg(), f.GPS_MARK), (f.make_png(), f.GPS_MARK), (f.make_webp(), f.GPS_MARK)):
        result = run({"cmd": "parse"}, data)
        assert result["kind"] == "image" and result["mime"].startswith("image/") and secret not in base64.b64decode(result["image_b64"])


# The sandbox


def sandboxed(data: bytes = b"", command: dict[str, Any] | None = None, **kw: Any) -> dict[str, Any]:
    return run_parser(command or {"cmd": "parse", "limits": LIMITS}, data, timeout=kw.pop("timeout", 20), memory_mb=kw.pop("memory_mb", 768), max_output=kw.pop("max_output", 10**7))


def test_the_sandbox_runs_parsers_and_reports_their_errors() -> None:
    assert sandboxed(f.make_docx([("Heading1", "Guide"), ("", "Text")]))["blocks"][0]["heading"] == "Guide"
    assert "not a PDF, Office" in sandboxed(b"hello")["error"]
    png = sandboxed(f.make_pdf(["x", None]), {"cmd": "render", "page": 2})
    assert png["mime"] == "image/png" and base64.b64decode(png["image_b64"]).startswith(b"\x89PNG")


def test_the_child_has_none_of_the_credentials(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    monkeypatch.setenv("AIFRED_ADMIN_PASSWORD", "hunter2-hunter2")
    monkeypatch.setenv("OPENBAO_SECRET_ID", "approle-secret")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_secret")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "secret.txt").write_text("not in the child's working directory")
    spy = "import json, os, sys; sys.stdout.write(json.dumps({'env': dict(os.environ), 'cwd': os.getcwd(), 'files': os.listdir('.'), 'argv': sys.argv[1:]}))"
    monkeypatch.setattr(rag_sandbox, "BOOT", spy)
    seen = sandboxed(b"x")
    flat = json.dumps(seen)
    assert "hunter2" not in flat and "approle-secret" not in flat and "gsk_secret" not in flat
    assert set(seen["env"]) <= {"PATH", "LANG", "PYTHONDONTWRITEBYTECODE", "PYTHONHASHSEED", "LC_CTYPE"} and seen["files"] == [] and seen["cwd"] != str(tmp_path)


@pytest.mark.parametrize(
    ("script", "message"),
    [
        ("while True:\n    pass", "took longer|CPU time"),
        ("import time\ntime.sleep(60)", "took longer"),
        ("import ctypes\nctypes.string_at(0)", "crashed"),
        ("import os, signal\nos.kill(os.getpid(), signal.SIGKILL)", "crashed"),
        ("raise SystemExit(3)", "exit code 3"),
        ("import sys\nsys.stdout.write('not json')", "no usable answer"),
        ("import sys\nsys.stdout.write('[1, 2]')", "no usable answer"),
    ],
)
def test_the_sandbox_survives_hostile_children(monkeypatch: pytest.MonkeyPatch, script: str, message: str) -> None:
    monkeypatch.setattr(rag_sandbox, "BOOT", script)
    with pytest.raises(SandboxError, match=message):
        sandboxed(b"x", timeout=2)


def test_memory_and_output_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rag_sandbox, "BOOT", "import resource, sys\nresource.setrlimit(resource.RLIMIT_AS, (200*1024*1024,)*2)\nx = bytearray(900*1024*1024)\nsys.stdout.write('{}')")
    with pytest.raises(SandboxError):
        sandboxed(b"x", timeout=5)
    monkeypatch.setattr(rag_sandbox, "BOOT", "import sys\nsys.stdout.write('{\"a\": \"' + 'x' * 5000 + '\"}')")
    with pytest.raises(SandboxError, match="larger than allowed"):
        sandboxed(b"x", max_output=1000)


def test_the_real_memory_limit_stops_a_file_that_is_too_big_to_parse() -> None:
    body = os.urandom(9_000_000).hex()  # 18 MB of text that does not compress away, so the archive checks let it through
    big = f.zipped({"word/document.xml": f"<w:document {f.W}><w:body><w:p><w:r><w:t>{body}</w:t></w:r></w:p></w:body></w:document>"})
    assert sandboxed(big, memory_mb=1500, max_output=10**8)["blocks"][0]["text"].startswith(body[:20])  # with room it is read
    try:
        outcome = sandboxed(big, memory_mb=330, max_output=10**8)
    except SandboxError as exc:
        outcome = {"error": str(exc)}
    assert "error" in outcome and "blocks" not in outcome  # with the limit it is not (the parser runs out of memory and the file is refused)


def test_a_pdf_that_makes_the_parser_spin_is_stopped(monkeypatch: pytest.MonkeyPatch) -> None:
    import time

    monkeypatch.setattr(rag_sandbox, "BOOT", "import time\nwhile True:\n    time.sleep(0.01)")
    started = time.time()
    with pytest.raises(SandboxError, match="took longer than 1 s"):
        sandboxed(f.make_pdf(["x"]), timeout=1)
    assert time.time() - started < 5
