"""Markdown to the HTML subset Telegram accepts (``parse_mode=HTML``).

The model writes Markdown; Telegram shows it raw unless it is converted. Only ``<b> <i> <code> <pre> <a> <blockquote>``
are produced, every other character is escaped, and links are limited to ``http(s)``, so a reply can never smuggle
markup or other URL schemes into a chat.
"""

from __future__ import annotations

import html
import re

_TOKEN = re.compile(r"(`[^`\n]+`|\[[^\]\n]+\]\(https?://[^\s)]+\)|\*\*[^*\n]+\*\*|\*[^*\s][^*\n]*\*|https?://[^\s)<]+)")
_LIST = re.compile(r"^(\s*)([-*•]|\d+[.)])\s+(.*)$")
_ROW = re.compile(r"^\s*\|.*\|\s*$")
_RULE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
_RULE_TEXT = "──────────"
_esc = html.escape


def _inline(text: str, bold: bool = True) -> str:
    out: list[str] = []
    last = 0
    for m in _TOKEN.finditer(text):
        out.append(_esc(text[last:m.start()], quote=False))
        tok = m.group(0)
        if tok.startswith("`"):
            out.append(f"<code>{_esc(tok[1:-1], quote=False)}</code>")
        elif tok.startswith("["):
            at = tok.rindex("](")
            out.append(f'<a href="{_esc(tok[at + 2:-1])}">{_esc(tok[1:at], quote=False)}</a>')
        elif tok.startswith("http"):
            url = tok.rstrip(".,;:!?")
            out.append(f'<a href="{_esc(url)}">{_esc(url, quote=False)}</a>{_esc(tok[len(url):], quote=False)}')
        elif tok.startswith("**"):
            inner = _esc(tok[2:-2], quote=False)
            out.append(f"<b>{inner}</b>" if bold else inner)
        else:
            out.append(f"<i>{_esc(tok[1:-1], quote=False)}</i>")
        last = m.end()
    out.append(_esc(text[last:], quote=False))
    return "".join(out)


def _cells(row: str) -> list[str]:
    return [c.strip() for c in row.strip().strip("|").split("|")]


def _table(rows: list[str]) -> str:
    grid = [[re.sub(r"\*\*|`", "", c) for c in _cells(r)] for r in rows if not re.fullmatch(r"\s*\|[\s|:-]+\|\s*", r)]
    widths = [max(len(r[i]) if i < len(r) else 0 for r in grid) for i in range(max(map(len, grid)))]
    lines = ["  ".join((r[i] if i < len(r) else "").ljust(widths[i]) for i in range(len(widths))).rstrip() for r in grid]
    return "<pre>" + _esc("\n".join(lines), quote=False) + "</pre>"


def to_html(markdown: str) -> str:
    """Convert Markdown text to Telegram HTML (headings become bold, lists use bullets, tables become aligned text)."""
    lines = markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.lstrip().startswith("```"):
            code: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].lstrip().startswith("```"):
                code.append(lines[i])
                i += 1
            i += 1
            out.append("<pre>" + _esc("\n".join(code), quote=False) + "</pre>")
            continue
        if _RULE.match(line):
            out.append(_RULE_TEXT)
        elif (heading := re.match(r"^#{1,6}\s+(.*)$", line)):
            out.append(f"<b>{_inline(heading.group(1).strip(), bold=False)}</b>")
        elif _ROW.match(line):
            rows = []
            while i < len(lines) and _ROW.match(lines[i]):
                rows.append(lines[i])
                i += 1
            out.append(_table(rows))
            continue
        elif line.lstrip().startswith(">"):
            quote = []
            while i < len(lines) and lines[i].lstrip().startswith(">"):
                quote.append(_inline(lines[i].lstrip()[1:].strip()))
                i += 1
            out.append("<blockquote>" + "\n".join(quote) + "</blockquote>")
            continue
        elif (item := _LIST.match(line)):
            mark = "•" if item.group(2) in "-*•" else item.group(2)
            out.append(f"{item.group(1)}{mark} {_inline(item.group(3))}")
        else:
            out.append(_inline(line))
        i += 1
    return "\n".join(out)
