"""Tests for aifred.chunking."""

from __future__ import annotations

import pytest

from aifred.chunking import (
    FormatError,
    chunk_text,
    join_overlapping,
    load_document,
    normalise,
    supported,
)

MD = """# Guide

Intro paragraph about the guide.

## Install

Run the installer.

### Proxmox

Back up every VM first.

```bash
# not a heading
qm snapshot 100 pre-upgrade
```

## Usage

| host | role |
| --- | --- |
| pve1 | primary |
| pve2 | backup |
"""


def test_sections_carry_their_heading_path() -> None:
    chunks = chunk_text(normalise(MD))
    assert [c.ord for c in chunks] == list(range(len(chunks)))
    by_heading = {c.heading: c.text for c in chunks}
    assert set(by_heading) == {"Guide", "Guide › Install", "Guide › Install › Proxmox", "Guide › Usage"}
    assert "Intro paragraph" in by_heading["Guide"]
    assert "Run the installer." in by_heading["Guide › Install"]
    assert "# not a heading" in by_heading["Guide › Install › Proxmox"]  # a fenced comment is not a heading
    assert by_heading["Guide › Usage"].startswith("| host | role |")


def test_chunks_never_span_two_sections() -> None:
    chunks = chunk_text(normalise(MD), chunk_chars=2000)
    assert len(chunks) == 4


def test_fenced_code_stays_whole_when_it_fits() -> None:
    text = "# T\n\n```py\nline one\n\nline after a blank line\n```\n"
    [chunk] = chunk_text(normalise(text))
    assert chunk.text == "```py\nline one\n\nline after a blank line\n```"


def test_packing_respects_the_limit_with_overlap() -> None:
    paragraphs = [f"Paragraph number {i} " + "word " * 40 for i in range(30)]
    text = "# Long\n\n" + "\n\n".join(paragraphs)
    chunks = chunk_text(text, chunk_chars=600, overlap=100)
    assert len(chunks) > 5
    assert all(len(c.text) <= 600 for c in chunks), max(len(c.text) for c in chunks)
    assert all(c.heading == "Long" for c in chunks)
    # consecutive chunks of a section share text at the seam
    for earlier, later in zip(chunks, chunks[1:], strict=False):
        tail = earlier.text[-60:].split()[-3:]
        assert all(word in later.text[:300] for word in tail), (earlier.ord, tail)


def test_no_overlap_across_sections() -> None:
    text = "# A\n\n" + "alpha " * 30 + "\n\n# B\n\n" + "beta " * 30
    chunks = chunk_text(text, chunk_chars=400, overlap=100)
    b = [c for c in chunks if c.heading == "B"]
    assert b and "alpha" not in b[0].text


def test_a_very_long_line_is_cut_at_word_boundaries() -> None:
    text = "# T\n\n" + " ".join(f"token{i}" for i in range(2000))
    chunks = chunk_text(text, chunk_chars=500, overlap=0)
    assert all(len(c.text) <= 500 for c in chunks)
    rebuilt = " ".join(c.text for c in chunks).split()
    assert rebuilt == [f"token{i}" for i in range(2000)]  # nothing lost, no word broken


def test_a_run_without_spaces_is_cut_at_the_limit_not_inside_a_character() -> None:
    text = "# T\n\n" + "é" * 3000 + "日本語" * 500
    chunks = chunk_text(text, chunk_chars=400, overlap=0)
    assert all(len(c.text) <= 400 for c in chunks)
    assert "".join(c.text for c in chunks) == "é" * 3000 + "日本語" * 500
    for c in chunks:
        c.text.encode("utf-8")  # raises if a surrogate half slipped in


def test_long_code_block_splits_by_lines() -> None:
    code = "\n".join(f"line_{i:04d} = {i}" for i in range(400))
    chunks = chunk_text(f"# C\n\n```\n{code}\n```\n", chunk_chars=500, overlap=0)
    assert len(chunks) > 3 and all(len(c.text) <= 500 for c in chunks)
    joined = "\n".join(c.text for c in chunks)
    for i in (0, 123, 399):
        assert f"line_{i:04d} = {i}" in joined


def test_long_table_repeats_its_header() -> None:
    rows = "\n".join(f"| host{i} | role{i} |" for i in range(200))
    chunks = chunk_text(f"# H\n\n| host | role |\n| --- | --- |\n{rows}\n", chunk_chars=500, overlap=0)
    assert len(chunks) > 3
    assert all(c.text.startswith("| host | role |\n| --- | --- |") for c in chunks)
    assert all(len(c.text) <= 500 for c in chunks)


def test_csv_header_repeats_in_every_chunk() -> None:
    body = "\n".join(f"{i},name{i},value{i}" for i in range(300))
    chunks = chunk_text(f"id,name,value\n{body}", chunk_chars=400, csv=True)
    assert len(chunks) > 3 and all(c.text.startswith("id,name,value\n") for c in chunks)
    assert all(len(c.text) <= 400 for c in chunks)


def test_degenerate_inputs() -> None:
    assert chunk_text("") == []
    assert chunk_text("\n\n  \n") == []
    assert chunk_text("---\n***\n") == []  # nothing searchable
    assert [c.text for c in chunk_text("okay")] == ["okay"]
    assert chunk_text("ok") == []  # under three characters carries nothing to search
    with pytest.raises(ValueError):
        chunk_text("x", chunk_chars=10)


def test_overlap_is_capped() -> None:
    text = "# T\n\n" + "\n\n".join("sentence number %d is here." % i for i in range(100))
    chunks = chunk_text(text, chunk_chars=300, overlap=10_000)
    assert all(len(c.text) <= 300 for c in chunks)


def test_normalise() -> None:
    assert normalise("a\r\nb\rc\x00  \n\n\n\n d") == "a\nb\nc\n\n d"
    assert normalise("é") == "é"  # NFC


def test_join_overlapping() -> None:
    first = "the quick brown fox jumps over"
    second = "jumps over the lazy dog"
    assert join_overlapping(first, second) == "the quick brown fox jumps over the lazy dog"
    assert join_overlapping("abc", "xyz") == "abc\n\nxyz"


# Loading ----------------------------------------------------------------------------------------------


def test_load_markdown_title_and_text() -> None:
    doc = load_document("notes/proxmox.md", b"\xef\xbb\xbf# Proxmox upgrade\r\n\r\nBack up first.\r\n")
    assert doc.title == "Proxmox upgrade" and doc.text == "# Proxmox upgrade\n\nBack up first." and not doc.csv


def test_load_html_is_converted() -> None:
    doc = load_document("page.html", b"<html><head><title> My Page </title></head><body><script>x()</script><p>Hello</p></body></html>")
    assert doc.title == "My Page" and doc.text == "Hello"


def test_load_plain_formats() -> None:
    assert load_document("a.txt", "Città perché".encode()).title == "a"
    assert load_document("data.csv", b"a,b\n1,2").csv is True
    assert load_document("Dockerfile", b"FROM python:3.13").title == "Dockerfile"
    assert load_document("u16.txt", "ciao".encode("utf-16")).text == "ciao"


@pytest.mark.parametrize("name", ["scan.pdf", "report.docx", "sheet.xlsx", "photo.jpg", "slides.pptx", "pic.PNG"])
def test_second_step_formats_get_a_clear_message(name: str) -> None:
    with pytest.raises(FormatError, match="second step"):
        load_document(name, b"%PDF-1.7")


@pytest.mark.parametrize(
    ("name", "data", "message"),
    [
        ("blob.bin", b"\x00\x01", "unsupported"),
        ("x.txt", b"abc\x00def", "binary"),
        ("x.txt", b"\xff\xfe\xfa", "not valid"),
        ("empty.md", b"  \n ", "no text"),
        ("noext", b"hello", "unsupported"),
    ],
)
def test_load_rejections(name: str, data: bytes, message: str) -> None:
    with pytest.raises(FormatError, match=message):
        load_document(name, data)


def test_supported() -> None:
    assert supported("a.MD") and supported("Makefile") and supported("x/y/z.py")
    assert not supported("a.pdf") and not supported("a.exe") and not supported("noext")
