"""Database access for sub-agent definitions and their runs (docs/specs.md section 14.1).

Mixed into :class:`aifred.store.Store`, which supplies ``_tx``, ``_query`` and ``_one``.
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import AbstractContextManager
from typing import Any

RUN_OUTPUT_LIMIT = 20_000
_JSON = ("skills", "toolsets")
_FIELDS = ("description", "role", "route", "skills", "toolsets", "memory", "max_iterations", "timeout", "token_budget", "enabled")


def _decode(row: dict[str, Any]) -> dict[str, Any]:
    for column in _JSON:
        row[column] = json.loads(row[column])
    row["enabled"] = bool(row["enabled"])
    return row


class AgentStoreMixin:
    """Agent and run rows."""

    def _tx(self) -> AbstractContextManager[sqlite3.Connection]:  # pragma: no cover - provided by Store
        raise NotImplementedError

    def _query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:  # pragma: no cover
        raise NotImplementedError

    def _one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:  # pragma: no cover
        raise NotImplementedError

    def agent_list(self) -> list[dict[str, Any]]:
        """Every agent ordered by name."""
        return [_decode(r) for r in self._query("SELECT * FROM agents ORDER BY name")]

    def agent_get(self, name: str) -> dict[str, Any] | None:
        """One agent by name."""
        row = self._one("SELECT * FROM agents WHERE name = ?", (name,))
        return _decode(row) if row else None

    def agent_save(self, name: str, values: dict[str, Any]) -> bool:
        """Insert or update an agent (only the given fields change); returns whether it was created."""
        row = {k: values[k] for k in _FIELDS if k in values}
        for column in _JSON:
            if column in row:
                row[column] = json.dumps(row[column])
        if "enabled" in row:
            row["enabled"] = 1 if row["enabled"] else 0
        now = time.time()
        with self._tx() as c:
            exists = c.execute("SELECT 1 FROM agents WHERE name = ?", (name,)).fetchone() is not None
            if exists:
                if row:
                    sets = ", ".join(f"{k} = ?" for k in row)
                    c.execute(f"UPDATE agents SET {sets}, updated = ? WHERE name = ?", (*row.values(), now, name))  # noqa: S608
            else:
                row.setdefault("skills", "[]")
                row.setdefault("toolsets", "[]")
                columns = ", ".join(["name", *row, "created", "updated"])
                marks = ", ".join("?" * (len(row) + 3))
                c.execute(f"INSERT INTO agents ({columns}) VALUES ({marks})", (name, *row.values(), now, now))  # noqa: S608
            return not exists

    def agent_delete(self, name: str) -> bool:
        """Delete an agent (its run history stays)."""
        with self._tx() as c:
            return c.execute("DELETE FROM agents WHERE name = ?", (name,)).rowcount > 0

    def agent_run_start(self, agent: str, parent_session: str, session_id: str, task: str) -> int:
        """Record that a run began; returns the run id."""
        with self._tx() as c:
            cur = c.execute("INSERT INTO agent_runs (agent, parent_session, session_id, task, started) VALUES (?, ?, ?, ?, ?)",
                            (agent, parent_session, session_id, task[:4000], time.time()))
            return int(cur.lastrowid or 0)

    def agent_run_finish(self, run_id: int, status: str, output: str = "", error: str = "", tokens: int = 0, model: str = "", keep: int = 100) -> None:
        """Close a run and trim the agent's old history."""
        with self._tx() as c:
            c.execute("UPDATE agent_runs SET finished = ?, status = ?, output = ?, error = ?, tokens = ?, model = ? WHERE id = ?",
                      (time.time(), status, output[:RUN_OUTPUT_LIMIT], error[:2000], tokens, model, run_id))
            row = c.execute("SELECT agent FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
            if row is not None:
                c.execute("DELETE FROM agent_runs WHERE agent = ? AND id NOT IN (SELECT id FROM agent_runs WHERE agent = ? ORDER BY id DESC LIMIT ?)",
                          (row["agent"], row["agent"], keep))

    def agent_runs(self, name: str, limit: int = 20) -> list[dict[str, Any]]:
        """Recent runs of an agent, newest first."""
        return self._query("SELECT * FROM agent_runs WHERE agent = ? ORDER BY id DESC LIMIT ?", (name, limit))

    def agent_runs_interrupted(self) -> int:
        """Mark runs left 'running' by a crash as failed; returns how many."""
        with self._tx() as c:
            return c.execute("UPDATE agent_runs SET status = 'error', error = 'interrupted by a restart', finished = ? WHERE status = 'running'",
                             (time.time(),)).rowcount

    def session_set_parent(self, session_id: str, parent_id: str) -> None:
        """Link a sub-agent's session to the conversation that delegated to it."""
        with self._tx() as c:
            c.execute("UPDATE sessions SET parent_id = ? WHERE id = ?", (parent_id or None, session_id))
