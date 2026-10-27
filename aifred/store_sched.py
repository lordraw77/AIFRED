"""Database access for scheduled tasks (docs/specs.md section 14).

Mixed into :class:`aifred.store.Store`, which supplies ``_tx``, ``_query`` and ``_one``.
"""

from __future__ import annotations

import sqlite3
import time
from contextlib import AbstractContextManager
from typing import Any

RUN_OUTPUT_LIMIT = 20_000
_FIELDS = ("expression", "prompt", "deliver_chat", "enabled", "next_run")


def _decode(row: dict[str, Any]) -> dict[str, Any]:
    row["enabled"] = bool(row["enabled"])
    return row


class SchedulerStoreMixin:
    """Schedule and run-history rows."""

    def _tx(self) -> AbstractContextManager[sqlite3.Connection]:  # pragma: no cover - provided by Store
        raise NotImplementedError

    def _query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:  # pragma: no cover
        raise NotImplementedError

    def _one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:  # pragma: no cover
        raise NotImplementedError

    def schedule_list(self) -> list[dict[str, Any]]:
        """Every schedule ordered by name."""
        return [_decode(r) for r in self._query("SELECT * FROM schedules ORDER BY name")]

    def schedule_count(self) -> int:
        """How many schedules exist."""
        return int(self._query("SELECT COUNT(*) AS n FROM schedules")[0]["n"])

    def schedule_get(self, name: str) -> dict[str, Any] | None:
        """One schedule by name."""
        row = self._one("SELECT * FROM schedules WHERE name = ?", (name,))
        return _decode(row) if row else None

    def schedule_create(self, name: str, expression: str, prompt: str, deliver_chat: int | None, next_run: float | None,
                        created_by: str = "user", enabled: bool = True) -> dict[str, Any]:
        """Insert a schedule.

        Raises:
            ValueError: If the name is taken.
        """
        try:
            with self._tx() as c:
                c.execute(
                    "INSERT INTO schedules (name, expression, prompt, deliver_chat, enabled, created_by, created, next_run)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (name, expression, prompt, deliver_chat, 1 if enabled else 0, created_by, time.time(), next_run),
                )
        except sqlite3.IntegrityError:
            raise ValueError(f"a schedule named {name!r} already exists") from None
        return self.schedule_get(name)  # type: ignore[return-value]

    def schedule_update(self, name: str, **values: Any) -> bool:
        """Change fields of a schedule; returns whether it exists."""
        row = {k: values[k] for k in _FIELDS if k in values}
        if "enabled" in row:
            row["enabled"] = 1 if row["enabled"] else 0
        if not row:
            return self.schedule_get(name) is not None
        sets = ", ".join(f"{k} = ?" for k in row)
        with self._tx() as c:
            return c.execute(f"UPDATE schedules SET {sets} WHERE name = ?", (*row.values(), name)).rowcount > 0  # noqa: S608

    def schedule_delete(self, name: str) -> bool:
        """Delete a schedule and its history."""
        with self._tx() as c:
            return c.execute("DELETE FROM schedules WHERE name = ?", (name,)).rowcount > 0

    def schedule_due(self, now: float, limit: int = 20) -> list[dict[str, Any]]:
        """Enabled schedules whose next run is at or before ``now``, oldest first."""
        return [_decode(r) for r in self._query(
            "SELECT * FROM schedules WHERE enabled = 1 AND next_run IS NOT NULL AND next_run <= ? ORDER BY next_run LIMIT ?", (now, limit))]

    def schedule_claim(self, schedule_id: int, expected_next: float, new_next: float | None) -> bool:
        """Move a due schedule to its next slot; only one caller wins, so a slot never runs twice."""
        with self._tx() as c:
            return c.execute("UPDATE schedules SET next_run = ? WHERE id = ? AND next_run = ? AND enabled = 1",
                             (new_next, schedule_id, expected_next)).rowcount > 0

    def schedule_run_start(self, schedule_id: int, session_id: str) -> int:
        """Record that a run began; returns the run id."""
        with self._tx() as c:
            cur = c.execute("INSERT INTO schedule_runs (schedule_id, started, session_id) VALUES (?, ?, ?)",
                            (schedule_id, time.time(), session_id))
            c.execute("UPDATE schedules SET last_run = ?, last_status = 'running' WHERE id = ?", (time.time(), schedule_id))
            return int(cur.lastrowid or 0)

    def schedule_run_finish(self, run_id: int, status: str, output: str = "", error: str = "", delivered: bool = False,
                            keep: int = 50) -> None:
        """Close a run, update the schedule's summary and trim old history."""
        with self._tx() as c:
            c.execute("UPDATE schedule_runs SET finished = ?, status = ?, output = ?, error = ?, delivered = ? WHERE id = ?",
                      (time.time(), status, output[:RUN_OUTPUT_LIMIT], error[:2000], 1 if delivered else 0, run_id))
            row = c.execute("SELECT schedule_id FROM schedule_runs WHERE id = ?", (run_id,)).fetchone()
            if row is None:
                return
            c.execute("UPDATE schedules SET last_status = ?, run_count = run_count + 1 WHERE id = ?", (status, row["schedule_id"]))
            c.execute("DELETE FROM schedule_runs WHERE schedule_id = ? AND id NOT IN"
                      " (SELECT id FROM schedule_runs WHERE schedule_id = ? ORDER BY id DESC LIMIT ?)", (row["schedule_id"], row["schedule_id"], keep))

    def schedule_runs(self, name: str, limit: int = 20) -> list[dict[str, Any]]:
        """Recent runs of a schedule, newest first."""
        return self._query(
            "SELECT r.* FROM schedule_runs r JOIN schedules s ON s.id = r.schedule_id WHERE s.name = ? ORDER BY r.id DESC LIMIT ?", (name, limit))

    def schedule_interrupted(self) -> int:
        """Mark runs left 'running' by a crash or restart as failed; returns how many."""
        with self._tx() as c:
            n = c.execute("UPDATE schedule_runs SET status = 'error', error = 'interrupted by a restart', finished = ? WHERE status = 'running'",
                          (time.time(),)).rowcount
            c.execute("UPDATE schedules SET last_status = 'error' WHERE last_status = 'running'")
            return n
