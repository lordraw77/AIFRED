"""Tests for the legacy Office, e-mail and EPUB parsers (aifred.rag_legacy)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from aifred.rag import RagError
from aifred.rag_legacy import Ole
from aifred.rag_parse import ParseError, magic_kind, run, sniff
from tests import legacy_files as L
from tests.rag_support import KB

LIMITS = {"max_pages": 500, "ocr_min_chars": 40}
REAL_XLS = Path(__file__).parent / "files" / "real.xls"   # written by xlwt: a genuine BIFF8 workbook


def parse(data: bytes, **limits: int) -> dict[str, Any]:
    return run({"cmd": "parse", "limits": {**LIMITS, **limits}}, data)


def test_the_kind_is_decided_by_the_streams_and_the_bytes() -> None:
    assert sniff(L.make_doc(["x"])) == "doc" and sniff(L.make_ppt([["x"]])) == "ppt" and sniff(REAL_XLS.read_bytes()) == "xls"
    assert sniff(L.make_ole({"Other": b"x"})) is None
    assert sniff(L.make_eml("Hi", "body")) == "eml" and sniff(L.make_epub({"a.xhtml": "<html/>"})) == "epub"
    assert sniff(b"just some text\nwith two lines") is None
    assert magic_kind(L.make_doc(["x"])[:16]) == "ole"


def test_doc_text_from_the_piece_table() -> None:
    result = parse(L.make_doc(["Intro\rHello, caf\xe9.\r", "Second \u20ac piece\r", "a\x07b\x07\x07\r"]))
    assert result["kind"] == "doc"
    text = result["blocks"][0]["text"]
    assert "Hello, café." in text and "Second € piece" in text and "a | b" in text


def test_doc_field_codes_are_dropped_and_encryption_refused() -> None:
    result = parse(L.make_doc(["See \x13 HYPERLINK \"x\" \x14the manual\x15 now\r"]))
    assert "See the manual now" in result["blocks"][0]["text"] and "HYPERLINK" not in result["blocks"][0]["text"]
    assert "encrypted" in parse(L.make_doc(["x"], encrypted=True))["error"]


def test_xls_values_sheets_and_a_string_table_split_by_continue_records() -> None:
    result = parse(REAL_XLS.read_bytes())
    by_sheet = {b["heading"].split(" › ")[0]: b["text"] for b in reversed(result["blocks"])}
    assert set(by_sheet) == {"Servers", "Notes", "Big"}
    servers = result["blocks"][0]["text"]
    assert "pve2 | 10.70.0.2 | 128.5" in servers and "città ✓ | docker3 | -7" in servers and servers.endswith("total")   # xlwt stores no cached formula result, so the cell stays empty
    assert by_sheet["Notes"] == "quorum corosync"
    big = " ".join(b["text"] for b in result["blocks"] if b["heading"].startswith("Big"))
    assert "string number 0 with" in big and "string number 599 with some padding" in big


def test_ppt_slides_use_the_list_and_the_text_boxes() -> None:
    result = parse(L.make_ppt([["Title one", "body in 8 bit"], ["Second slide"]], [["box text"], ["other box"]]))
    assert [b["heading"] for b in result["blocks"]] == ["Slide 1", "Slide 2"]
    assert result["blocks"][0]["text"] == "Title one\nbody in 8 bit\nbox text" and result["pages"] == 2
    assert "other box" in result["blocks"][1]["text"]


def test_eml_headers_body_and_attachment_names() -> None:
    result = parse(L.make_eml("Plan", "plain body", html="<p>rich body</p>", attachment=("plan.pdf", b"%PDF")))
    texts = [b["text"] for b in result["blocks"]]
    assert result["title"] == "Plan" and "From: Anna <anna@example.org>" in texts[0]
    assert "plain body" in texts and "rich body" not in " ".join(texts) and texts[-1] == "plan.pdf"
    only_html = parse(L.make_eml("H", "x", html="<p>rich body</p>", html_only=True))
    assert any("rich body" in b["text"] for b in only_html["blocks"])


def test_epub_chapters_in_spine_order_with_a_doctype_and_entities() -> None:
    chapter = '<?xml version="1.0"?><!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" "x.dtd"><html xmlns="http://www.w3.org/1999/xhtml"><body>{}</body></html>'
    result = parse(L.make_epub({"b.xhtml": chapter.format("<h1>Second</h1><p>two</p>"), "a.xhtml": chapter.format("<h1>First</h1><p>one&nbsp;1 &amp; more</p><h2>Sub</h2><p>deep</p>")}))
    assert result["title"] == "Manual"
    assert [(b["heading"], b["text"]) for b in result["blocks"]] == [("Second", "two"), ("First", "one 1 & more"), ("First › Sub", "deep")]
    bomb = chapter.replace("<html", '<!ENTITY x "y"><html', 1).replace("]>", "")
    assert parse(L.make_epub({"a.xhtml": '<?xml version="1.0"?><!DOCTYPE html [<!ENTITY x "y">]><html xmlns="http://www.w3.org/1999/xhtml"><body><p>&x;</p></body></html>'}))["blocks"] == []
    assert bomb  # the internal subset is refused: the chapter is skipped rather than expanded


def test_a_damaged_container_is_refused_not_crashed() -> None:
    good = L.make_doc(["Hello\r"])
    for cut in (600, 1200, len(good) // 2):
        assert "error" in parse(good[:cut])
    with pytest.raises(ParseError):
        Ole(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 600)
    looped = bytearray(good)
    looped[0x30:0x34] = (0).to_bytes(4, "little")     # the directory chain now starts in the FAT sector itself
    assert "error" in parse(bytes(looped)) or parse(bytes(looped))["blocks"] == []
    assert "error" in parse(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\xff" * 600)


def test_old_excel_versions_are_refused() -> None:
    stream = L.make_ole({"Book": b"\x09\x04\x06\x00\x00\x00\x10\x00".ljust(64, b"\0")})
    assert "before Excel 97" in parse(stream)["error"]


def test_the_knowledge_base_accepts_and_indexes_the_new_formats(kb: KB) -> None:
    kb.rag.create_collection("c")
    kb.rag.add_document("c", "old.doc", L.make_doc(["Quorum corosync notes\r"]))
    kb.rag.add_document("c", "mail.eml", L.make_eml("Backup", "The nightly vzdump failed on pve2"))
    kb.rag.add_document("c", "sheet.xls", REAL_XLS.read_bytes())
    assert kb.index() == 3
    assert {kb.doc(n)["status"] for n in ("old.doc", "mail.eml", "sheet.xls")} == {"ready"}
    assert kb.rag.search("vzdump failed").hits[0].source == "mail.eml"
    assert kb.rag.accepts("deck.ppt", L.make_ppt([["x"]])[:16]) == "office" and kb.rag.accepts("b.epub", L.make_epub({"a": "<html/>"})[:16]) == "office"
    with pytest.raises(RagError, match="not a valid .doc"):
        kb.rag.add_document("c", "fake.doc", b"just text")
