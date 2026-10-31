"""Turning documents into chunks for the knowledge base (spec 9.4, "Chunking").

Text is split on headings and blank lines and packed into chunks of about ``chunk_chars``
characters. A chunk never spans two sections, so its heading path is exact. Fenced code blocks
and tables stay whole when they fit, over-long lines are cut at a sentence or word boundary,
and Python strings are sequences of code points, so nothing is ever cut inside a character.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

from aifred.tools.web import html_to_text

HEADING_SEPARATOR = " › "
MIN_CHUNK_CHARS = 3

TEXT_EXTENSIONS = frozenset(
    ".md .markdown .txt .rst .log .json .jsonl .csv .tsv .yaml .yml .toml .ini .cfg .conf .xml .html .htm "
    ".py .js .mjs .ts .tsx .jsx .go .rs .java .kt .c .h .cpp .hpp .cs .rb .php .sh .bash .zsh .sql .lua .pl .swift .scala .tf".split()
)
TEXT_NAMES = frozenset({"dockerfile", "makefile", "readme", "license", "changelog", "notice"})
SECOND_STEP_EXTENSIONS = frozenset(
    ".pdf .docx .xlsx .pptx .odt .ods .odp .doc .xls .ppt .epub .eml .jpg .jpeg .png .webp .gif .bmp .tif .tiff".split()
)

_FENCE_RE = re.compile(r"^\s{0,3}(```+|~~~+)")
_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})[ \t]+(.+?)[ \t#]*$")
_SENTENCE_RE = re.compile(r"(?<=[.!?…])\s+")


class FormatError(ValueError):
    """The file cannot be indexed; the message is shown to the admin."""


@dataclass(frozen=True)
class Chunk:
    """A piece of a document.

    Attributes:
        ord: Position in the document, from 0.
        heading: Heading path of the section, for example ``Guide › Install``.
        text: The chunk body.
        page: Page or slide number for PDF and presentations.
        vision: Whether the text was read from an image by a vision model.
    """

    ord: int
    heading: str
    text: str
    page: int | None = None
    vision: bool = False


@dataclass(frozen=True)
class Loaded:
    """A document converted to plain text.

    Attributes:
        title: Display title.
        text: Normalised text.
        csv: Whether the text is tabular (the header row is repeated in every chunk).
    """

    title: str
    text: str
    csv: bool = False


@dataclass
class _Block:
    kind: str  # para | code | table
    heading: str
    text: str


@dataclass
class _Section:
    heading: str
    blocks: list[_Block] = field(default_factory=list)


def normalise(text: str) -> str:
    """NFC, Unix newlines, no NUL, no trailing spaces, at most one blank line in a row."""
    text = unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", ""))
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return re.sub(r"\n{3,}", "\n\n", text).strip("\n")


def supported(name: str) -> bool:
    """Whether the first step of the knowledge base can read a file of this name."""
    path = PurePosixPath(name)
    return path.suffix.lower() in TEXT_EXTENSIONS or (not path.suffix and path.name.lower() in TEXT_NAMES)


def load_document(name: str, data: bytes, fallback_title: str | None = None) -> Loaded:
    """Decode a file and convert it to plain text.

    Args:
        name: File name (the extension decides how it is read).
        data: File content.
        fallback_title: Title to use when the document carries none (defaults to the file name).

    Raises:
        FormatError: For an unsupported type, binary content or text that is not valid Unicode.
    """
    path = PurePosixPath(name)
    ext = path.suffix.lower()
    if ext in SECOND_STEP_EXTENSIONS:
        raise FormatError(f"{ext} files need the second step of the knowledge base (PDF, Office and images); convert to text for now")
    if not supported(name):
        raise FormatError(f"unsupported file type {ext or path.name!r}")
    if b"\x00" in data[:4096] and not data.startswith((b"\xff\xfe", b"\xfe\xff")):
        raise FormatError("binary content")
    try:
        if data.startswith((b"\xff\xfe", b"\xfe\xff")):
            text = data.decode("utf-16")
        else:
            text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise FormatError("the file is not valid UTF-8 text") from None

    title = ""
    if ext in (".html", ".htm"):
        title, text = html_to_text(text)
    elif ext in (".md", ".markdown"):
        for line in text.split("\n")[:50]:
            if match := re.match(r"^#[ \t]+(.+?)[ \t#]*$", line):
                title = match.group(1)
                break
    text = normalise(text)
    if not text:
        raise FormatError("the document has no text")
    return Loaded(title=" ".join(title.split()) or fallback_title or path.stem or path.name, text=text, csv=ext in (".csv", ".tsv"))


# Parsing into sections and blocks -------------------------------------------------------------------


def _sections(text: str) -> list[_Section]:
    """Split text into sections (by Markdown headings), each a list of blocks."""
    sections: list[_Section] = [_Section("")]
    stack: list[tuple[int, str]] = []
    lines = text.split("\n")
    buffer: list[str] = []
    fence: str | None = None

    def flush(kind: str = "para") -> None:
        if buffer and any(line.strip() for line in buffer):
            sections[-1].blocks.append(_Block(kind, sections[-1].heading, "\n".join(buffer).strip("\n")))
        buffer.clear()

    for line in lines:
        fence_match = _FENCE_RE.match(line)
        if fence is not None:
            buffer.append(line)
            if fence_match and fence_match.group(1)[0] == fence[0] and len(fence_match.group(1)) >= len(fence) and line.strip() == fence_match.group(1):
                flush("code")
                fence = None
            continue
        if fence_match:
            flush()
            fence = fence_match.group(1)
            buffer.append(line)
            continue
        heading = _HEADING_RE.match(line)
        if heading:
            flush()
            level, title = len(heading.group(1)), heading.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            sections.append(_Section(HEADING_SEPARATOR.join(t for _, t in stack)))
            continue
        if not line.strip():
            flush("table" if buffer and all(b.lstrip().startswith("|") for b in buffer if b.strip()) else "para")
            continue
        buffer.append(line)
    flush("code" if fence is not None else "para")
    return [s for s in sections if s.blocks]


# Splitting what does not fit ------------------------------------------------------------------------


def _hard_split(text: str, limit: int) -> list[str]:
    """Cut a long run at whitespace when possible, otherwise at the limit (never inside a code point)."""
    out: list[str] = []
    while len(text) > limit:
        cut = text.rfind(" ", int(limit * 0.6), limit + 1)
        if cut <= 0:
            cut = limit
        out.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        out.append(text)
    return out


def _pack(units: list[str], limit: int, joiner: str) -> list[str]:
    pieces: list[str] = []
    current = ""
    for unit in units:
        for part in ([unit] if len(unit) <= limit else _hard_split(unit, limit)):
            candidate = f"{current}{joiner}{part}" if current else part
            if len(candidate) <= limit:
                current = candidate
            else:
                if current:
                    pieces.append(current)
                current = part
    if current:
        pieces.append(current)
    return pieces


def _split_block(block: _Block, limit: int) -> list[str]:
    """Pieces of a block that is longer than ``limit``."""
    if block.kind == "table":
        rows = block.text.split("\n")
        header = rows[:2] if len(rows) > 2 and re.match(r"^\s*\|?[\s:|-]+\|?\s*$", rows[1]) else []
        head = "\n".join(header)
        body = rows[len(header):]
        room = max(limit - len(head) - 1, limit // 2)
        return [f"{head}\n{piece}" if head else piece for piece in _pack(body, room, "\n")]
    if block.kind == "code":
        return _pack(block.text.split("\n"), limit, "\n")
    sentences = [s for chunk in block.text.split("\n") for s in _SENTENCE_RE.split(chunk) if s.strip()]
    return _pack(sentences, limit, " ")


def _overlap_tail(text: str, size: int) -> str:
    """The last ``size`` characters of ``text``, starting at a word boundary."""
    if size <= 0 or len(text) <= size:
        return ""
    tail = text[-size:]
    space = tail.find(" ")
    nl = tail.find("\n")
    starts = [i for i in (space, nl) if i >= 0]
    return tail[min(starts) + 1:].lstrip() if starts else ""


# Public API -----------------------------------------------------------------------------------------


def chunk_text(text: str, *, chunk_chars: int = 1200, overlap: int = 150, csv: bool = False) -> list[Chunk]:
    """Split normalised text into chunks.

    Args:
        text: Document text (call :func:`normalise` first, or use :func:`load_document`).
        chunk_chars: Target maximum characters per chunk, overlap included.
        overlap: Characters repeated at the start of a chunk from the end of the previous one
            in the same section (capped at a third of ``chunk_chars``).
        csv: Treat the text as a table: rows are grouped and the header row repeats in every chunk.

    Returns:
        Chunks in document order with consecutive ``ord`` values.
    """
    if chunk_chars < 50:
        raise ValueError("chunk_chars must be at least 50")
    overlap = max(0, min(overlap, chunk_chars // 3))
    if csv:
        return _chunk_csv(text, chunk_chars)

    out: list[Chunk] = []
    limit = chunk_chars - overlap - 1  # a piece plus an overlap prefix and one newline always fit in a chunk

    def emit(heading: str, text: str) -> None:
        if len(text.strip()) >= MIN_CHUNK_CHARS and any(ch.isalnum() for ch in text):
            out.append(Chunk(len(out), heading, text.strip()))

    for section in _sections(text):
        prefix = ""  # tail of the previous chunk of this section
        body = ""  # what has been packed into the chunk being built
        for block in section.blocks:
            pieces = [block.text] if len(block.text) <= limit else _split_block(block, limit)
            for piece in pieces:
                candidate = f"{body}\n\n{piece}" if body else piece
                if len(prefix) + (1 if prefix else 0) + len(candidate) <= chunk_chars:
                    body = candidate
                    continue
                finished = f"{prefix}\n{body}" if prefix else body
                emit(section.heading, finished)
                prefix = _overlap_tail(finished, overlap)
                body = piece
        if body:
            emit(section.heading, f"{prefix}\n{body}" if prefix else body)
    return out


def chunk_blocks(blocks: list[dict[str, Any]], *, chunk_chars: int = 1200, overlap: int = 150) -> list[Chunk]:
    """Chunk the blocks a parser produced, keeping each block's page (or slide) and whether a vision model read it.

    Args:
        blocks: Dicts with ``text`` and optionally ``page``, ``heading`` and ``vision``.
        chunk_chars: Target maximum characters per chunk.
        overlap: Characters repeated between consecutive chunks of one block.

    Returns:
        Chunks in order with consecutive ``ord`` values; a chunk never spans two blocks, so its page is exact.
    """
    out: list[Chunk] = []
    for block in blocks:
        text = normalise(str(block.get("text", "")))
        if not text:
            continue
        for chunk in chunk_text(text, chunk_chars=chunk_chars, overlap=overlap):
            heading = HEADING_SEPARATOR.join(part for part in (block.get("heading") or "", chunk.heading) if part)
            out.append(Chunk(len(out), heading, chunk.text, block.get("page"), bool(block.get("vision"))))
    return out


def _chunk_csv(text: str, chunk_chars: int) -> list[Chunk]:
    lines = text.split("\n")
    header, rows = lines[0], lines[1:]
    if not rows:
        return [Chunk(0, "", text)] if text.strip() else []
    room = max(chunk_chars - len(header) - 1, chunk_chars // 2)
    out = []
    for piece in _pack(rows, room, "\n"):
        out.append(Chunk(len(out), "", f"{header}\n{piece}"))
    return out


def join_overlapping(first: str, second: str, max_overlap: int = 400) -> str:
    """Join two consecutive chunks of a document, dropping the text they share."""
    for size in range(min(len(first), len(second), max_overlap), 0, -1):
        if first.endswith(second[:size]):
            return first + second[size:]
    return f"{first}\n\n{second}"
