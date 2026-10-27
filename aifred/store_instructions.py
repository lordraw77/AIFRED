"""Database access for instructions: what an MCP server, a toolset or a tool is for (spec 9.3).

Mixed into :class:`aifred.store.Store`, which supplies ``_tx``, ``_query`` and ``_one``.
"""

from __future__ import annotations

import sqlite3
import time
from contextlib import AbstractContextManager
from typing import Any


class InstructionStoreMixin:
    """Instruction rows, keyed by scope (``mcp:<name>``, ``toolset:<name>`` or ``tool:<name>``)."""

    def _tx(self) -> AbstractContextManager[sqlite3.Connection]:  # pragma: no cover - provided by Store
        raise NotImplementedError

    def _query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:  # pragma: no cover
        raise NotImplementedError

    def _one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:  # pragma: no cover
        raise NotImplementedError

    def instruction_list(self) -> list[dict[str, Any]]:
        """Every instruction ordered by scope."""
        rows = self._query("SELECT * FROM instructions ORDER BY scope")
        for row in rows:
            row["enabled"] = bool(row["enabled"])
        return rows

    def instruction_get(self, scope: str) -> dict[str, Any] | None:
        """One instruction."""
        row = self._one("SELECT * FROM instructions WHERE scope = ?", (scope,))
        if row:
            row["enabled"] = bool(row["enabled"])
        return row

    def instruction_set(self, scope: str, text: str, enabled: bool = True) -> None:
        """Insert or replace an instruction."""
        with self._tx() as c:
            c.execute(
                "INSERT INTO instructions (scope, text, enabled, updated) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(scope) DO UPDATE SET text = excluded.text, enabled = excluded.enabled, updated = excluded.updated",
                (scope, text, 1 if enabled else 0, time.time()),
            )

    def instruction_delete(self, scope: str) -> bool:
        """Remove an instruction; returns whether it existed."""
        with self._tx() as c:
            return c.execute("DELETE FROM instructions WHERE scope = ?", (scope,)).rowcount > 0

    def instruction_delete_prefix(self, prefix: str) -> int:
        """Remove every instruction whose scope starts with ``prefix`` (a deleted MCP server takes its tools' notes with it)."""
        escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        with self._tx() as c:
            return c.execute("DELETE FROM instructions WHERE scope LIKE ? ESCAPE '\\'", (escaped + "%",)).rowcount
