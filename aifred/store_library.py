"""Database access for the library: saved answers and results (docs/specs.md section 15).

Mixed into :class:`aifred.store.Store`, which supplies ``_tx``, ``_query`` and ``_one``.
"""

from __future__ import annotations

import re
import sqlite3
import time
from contextlib import AbstractContextManager
from typing import Any

_COLUMNS = "id, title, content, prompt, tags, source, source_ref, previous IS NOT NULL AS has_previous, created, updated"


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _row(row: dict[str, Any]) -> dict[str, Any]:
    row["tags"] = [t for t in row["tags"].split(",") if t]
    row["has_previous"] = bool(row["has_previous"])
    return row


class LibraryStoreMixin:
    """Library rows."""

    def _tx(self) -> AbstractContextManager[sqlite3.Connection]:  # pragma: no cover - provided by Store
        raise NotImplementedError

    def _query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:  # pragma: no cover
        raise NotImplementedError

    def _one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:  # pragma: no cover
        raise NotImplementedError

    def library_count(self) -> int:
        """Number of items."""
        row = self._one("SELECT COUNT(*) AS n FROM library")
        return int(row["n"]) if row else 0

    def library_list(self, tag: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        """Items, most recently changed first, optionally only those with a tag."""
        where, params = ("WHERE (',' || tags || ',') LIKE ? ESCAPE '\\'", [f"%,{_escape(tag)},%"]) if tag else ("", [])
        rows = self._query(f"SELECT {_COLUMNS} FROM library {where} ORDER BY updated DESC, id DESC LIMIT ?", (*params, limit))
        return [_row(r) for r in rows]

    def library_get(self, item_id: int) -> dict[str, Any] | None:
        """One item by id."""
        row = self._one(f"SELECT {_COLUMNS} FROM library WHERE id = ?", (item_id,))
        return _row(row) if row else None

    def library_by_title(self, title: str) -> dict[str, Any] | None:
        """One item by title (case-insensitive)."""
        row = self._one(f"SELECT {_COLUMNS} FROM library WHERE title = ?", (title,))
        return _row(row) if row else None

    def library_previous(self, item_id: int) -> str | None:
        """The content before the last change, if kept."""
        row = self._one("SELECT previous FROM library WHERE id = ?", (item_id,))
        return row["previous"] if row else None

    def library_add(self, title: str, content: str, prompt: str, tags: list[str], source: str, source_ref: str) -> int:
        """Insert an item; the title must be new (``sqlite3.IntegrityError`` otherwise)."""
        now = time.time()
        with self._tx() as c:
            cur = c.execute(
                "INSERT INTO library (title, content, prompt, tags, source, source_ref, created, updated) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (title, content, prompt, ",".join(tags), source, source_ref, now, now),
            )
            return int(cur.lastrowid or 0)

    def library_update(self, item_id: int, values: dict[str, Any], keep_previous: bool = False) -> bool:
        """Change title, content, prompt or tags; with ``keep_previous`` the old content is kept for one undo.

        Raises:
            sqlite3.IntegrityError: If the new title belongs to another item.
        """
        sets, params = [], []
        for key in ("title", "content", "prompt"):
            if key in values:
                sets.append(f"{key} = ?")
                params.append(values[key])
        if "tags" in values:
            sets.append("tags = ?")
            params.append(",".join(values["tags"]))
        if keep_previous and "content" in values:
            sets.append("previous = (SELECT content FROM library WHERE id = ?)")
            params.append(item_id)
        if not sets:
            return self.library_get(item_id) is not None
        sets.append("updated = ?")
        params += [time.time(), item_id]
        with self._tx() as c:
            return c.execute(f"UPDATE library SET {', '.join(sets)} WHERE id = ?", params).rowcount > 0

    def library_restore(self, item_id: int) -> bool:
        """Swap the content with the kept previous version (pressing it twice goes back)."""
        with self._tx() as c:
            return c.execute(
                "UPDATE library SET content = previous, previous = content, updated = ? WHERE id = ? AND previous IS NOT NULL",
                (time.time(), item_id),
            ).rowcount > 0

    def library_delete(self, item_id: int) -> bool:
        """Delete an item."""
        with self._tx() as c:
            return c.execute("DELETE FROM library WHERE id = ?", (item_id,)).rowcount > 0

    def library_titles(self, limit: int) -> list[dict[str, Any]]:
        """Title and tags of the latest items, for the system prompt."""
        return [_row({**r, "has_previous": 0}) for r in self._query("SELECT id, title, tags FROM library ORDER BY updated DESC, id DESC LIMIT ?", (limit,))]

    def library_search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Full-text search over title, content and tags; a word in the title ranks higher.

        Query terms (unicode ``\\w+``) are quoted and joined with OR.
        """
        terms = re.findall(r"\w+", query)
        if not terms:
            return []
        match = " OR ".join(f'"{t}"' for t in dict.fromkeys(terms))
        rows = self._query(
            "SELECT l.id, l.title, l.tags, l.updated, length(l.content) AS size, "
            "snippet(library_fts, 1, '**', '**', '…', 16) AS snippet "
            "FROM library_fts JOIN library l ON l.id = library_fts.rowid "
            "WHERE library_fts MATCH ? ORDER BY bm25(library_fts, 8.0, 1.0, 4.0) LIMIT ?",
            (match, limit),
        )
        return [_row({**r, "has_previous": 0}) for r in rows]
