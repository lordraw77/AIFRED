"""Database access for MCP server definitions (spec 9.2).

Mixed into :class:`aifred.store.Store`, which supplies ``_tx``, ``_query`` and ``_one``.
Header values are not here: they are encrypted in the vault and only their names are stored.
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import AbstractContextManager
from typing import Any

_JSON_COLUMNS = ("header_names", "include", "exclude", "command")
_FIELDS = (
    "url", "enabled", "header_names", "verify_tls", "timeout", "connect_timeout", "protocol_version", "include", "exclude", "max_tools", "command",
)


def _decode(row: dict[str, Any]) -> dict[str, Any]:
    for column in _JSON_COLUMNS:
        row[column] = json.loads(row[column])
    row["enabled"] = bool(row["enabled"])
    return row


class McpStoreMixin:
    """MCP server rows."""

    def _tx(self) -> AbstractContextManager[sqlite3.Connection]:  # pragma: no cover - provided by Store
        raise NotImplementedError

    def _query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:  # pragma: no cover
        raise NotImplementedError

    def _one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:  # pragma: no cover
        raise NotImplementedError

    def mcp_server_list(self) -> list[dict[str, Any]]:
        """Every server ordered by name."""
        return [_decode(r) for r in self._query("SELECT * FROM mcp_servers ORDER BY name")]

    def mcp_server_get(self, name: str) -> dict[str, Any] | None:
        """One server by name."""
        row = self._one("SELECT * FROM mcp_servers WHERE name = ?", (name,))
        return _decode(row) if row else None

    def mcp_server_save(self, name: str, values: dict[str, Any]) -> bool:
        """Insert or update a server; returns whether it was newly created."""
        row = {k: values[k] for k in _FIELDS if k in values}
        for column in _JSON_COLUMNS:
            if column in row:
                row[column] = json.dumps(row[column])
        if "enabled" in row:
            row["enabled"] = 1 if row["enabled"] else 0
        now = time.time()
        with self._tx() as c:
            exists = c.execute("SELECT 1 FROM mcp_servers WHERE name = ?", (name,)).fetchone() is not None
            if exists:
                if row:
                    assignments = ", ".join(f"{k} = ?" for k in row)
                    c.execute(f"UPDATE mcp_servers SET {assignments}, updated = ? WHERE name = ?", (*row.values(), now, name))
            else:
                columns = ["name", *row, "created", "updated"]
                c.execute(
                    f"INSERT INTO mcp_servers ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})",
                    (name, *row.values(), now, now),
                )
            return not exists

    def mcp_server_delete(self, name: str) -> bool:
        """Remove a server; returns whether it existed."""
        with self._tx() as c:
            return c.execute("DELETE FROM mcp_servers WHERE name = ?", (name,)).rowcount > 0
