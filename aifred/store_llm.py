"""Database access for LLM providers, models, routes, health and usage (spec 6.1).

Mixed into :class:`aifred.store.Store`, which supplies ``_lock``, ``_tx``,
``_query`` and ``_one``.
"""

from __future__ import annotations

import json
import time
from contextlib import AbstractContextManager
from typing import Any
import sqlite3
import threading

_JSON_COLUMNS = ("headers", "extra_body", "config", "timeouts")
_PROVIDER_FIELDS = (
    "slug", "type", "base_url", "headers", "extra_body", "config", "connect_timeout",
    "first_token_timeout", "stall_timeout", "total_timeout", "verify_tls", "enabled",
    "budget_tokens_daily", "budget_tokens_monthly",
)
_MODEL_FIELDS = (
    "context_window", "tools", "streaming", "vision", "thinking_control", "price_in", "price_out", "kind",
)
_EMBEDDING_NAME_HINTS = ("embed", "bge-", "e5-", "gte-", "minilm", "mpnet", "nomic-bert")


def guess_kind(model_id: str) -> str:
    """Guess ``embedding`` or ``chat`` from a model id (only a default; the admin can change it)."""
    lowered = model_id.lower()
    return "embedding" if any(hint in lowered for hint in _EMBEDDING_NAME_HINTS) else "chat"


def _decode(row: dict[str, Any]) -> dict[str, Any]:
    for column in _JSON_COLUMNS:
        if column in row and isinstance(row[column], str):
            row[column] = json.loads(row[column])
    return row


class LLMStoreMixin:
    """Provider, model, route, health and usage queries."""

    _lock: threading.RLock

    def _tx(self) -> AbstractContextManager[sqlite3.Connection]:  # pragma: no cover - provided by Store
        raise NotImplementedError

    def _query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:  # pragma: no cover
        raise NotImplementedError

    def _one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:  # pragma: no cover
        raise NotImplementedError

    # Providers

    def provider_create(self, values: dict[str, Any]) -> int:
        """Insert a provider; ``values`` uses the column names (JSON fields as objects).

        Raises:
            sqlite3.IntegrityError: If the slug is taken.
        """
        now = time.time()
        row = {k: values[k] for k in _PROVIDER_FIELDS if k in values}
        for column in _JSON_COLUMNS:
            if column in row:
                row[column] = json.dumps(row[column])
        columns = [*row, "created", "updated"]
        with self._tx() as c:
            cur = c.execute(
                f"INSERT INTO providers ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})",
                (*row.values(), now, now),
            )
            return int(cur.lastrowid or 0)

    def provider_update(self, provider_id: int, values: dict[str, Any]) -> bool:
        """Update provider columns; returns whether the provider exists."""
        row = {k: values[k] for k in _PROVIDER_FIELDS if k in values}
        for column in _JSON_COLUMNS:
            if column in row:
                row[column] = json.dumps(row[column])
        if not row:
            return self.provider_get(provider_id) is not None
        assignments = ", ".join(f"{k} = ?" for k in row)
        with self._tx() as c:
            return (
                c.execute(
                    f"UPDATE providers SET {assignments}, updated = ? WHERE id = ?",
                    (*row.values(), time.time(), provider_id),
                ).rowcount
                > 0
            )

    def provider_get(self, provider_id: int) -> dict[str, Any] | None:
        """Return one provider."""
        row = self._one("SELECT * FROM providers WHERE id = ?", (provider_id,))
        return _decode(row) if row else None

    def provider_by_slug(self, slug: str) -> dict[str, Any] | None:
        """Return one provider by slug."""
        row = self._one("SELECT * FROM providers WHERE slug = ?", (slug,))
        return _decode(row) if row else None

    def provider_list(self) -> list[dict[str, Any]]:
        """Return every provider ordered by slug."""
        return [_decode(r) for r in self._query("SELECT * FROM providers ORDER BY slug")]

    def provider_delete(self, provider_id: int) -> list[int]:
        """Delete a provider with its keys, models and route entries.

        Returns:
            The ids of the deleted keys (so the caller can remove their secrets).
        """
        with self._tx() as c:
            key_ids = [r[0] for r in c.execute("SELECT id FROM provider_keys WHERE provider_id = ?", (provider_id,))]
            c.execute("DELETE FROM provider_health WHERE kind = 'entry' AND ref LIKE ?", (f"{provider_id}|%",))
            for key_id in key_ids:
                c.execute("DELETE FROM provider_health WHERE kind = 'key' AND ref = ?", (str(key_id),))
            c.execute("DELETE FROM providers WHERE id = ?", (provider_id,))
            self._renumber_routes(c)
            return key_ids

    # Provider keys (values live in the vault)

    def key_create(self, provider_id: int, label: str, last4: str) -> int:
        """Register a key row; the secret itself is stored by the caller."""
        with self._tx() as c:
            position = c.execute(
                "SELECT COALESCE(MAX(position) + 1, 0) FROM provider_keys WHERE provider_id = ?", (provider_id,)
            ).fetchone()[0]
            cur = c.execute(
                "INSERT INTO provider_keys (provider_id, label, last4, position, created) VALUES (?, ?, ?, ?, ?)",
                (provider_id, label, last4, position, time.time()),
            )
            return int(cur.lastrowid or 0)

    def key_list(self, provider_id: int) -> list[dict[str, Any]]:
        """Return a provider's keys in priority order."""
        return self._query("SELECT * FROM provider_keys WHERE provider_id = ? ORDER BY position, id", (provider_id,))

    def key_get(self, key_id: int) -> dict[str, Any] | None:
        """Return one key row."""
        return self._one("SELECT * FROM provider_keys WHERE id = ?", (key_id,))

    def key_update(self, key_id: int, *, label: str | None = None, last4: str | None = None) -> bool:
        """Update a key's label or last four characters."""
        with self._tx() as c:
            row = c.execute("SELECT label, last4 FROM provider_keys WHERE id = ?", (key_id,)).fetchone()
            if row is None:
                return False
            c.execute(
                "UPDATE provider_keys SET label = ?, last4 = ? WHERE id = ?",
                (row[0] if label is None else label, row[1] if last4 is None else last4, key_id),
            )
            return True

    def key_delete(self, key_id: int) -> bool:
        """Delete a key row and its health state."""
        with self._tx() as c:
            c.execute("DELETE FROM provider_health WHERE kind = 'key' AND ref = ?", (str(key_id),))
            return c.execute("DELETE FROM provider_keys WHERE id = ?", (key_id,)).rowcount > 0

    # Models

    def model_upsert_listed(self, provider_id: int, listed: list[dict[str, Any]]) -> int:
        """Merge a fetched model list; listed models become available, unlisted non-manual ones do not.

        Args:
            provider_id: Provider id.
            listed: Dicts with ``model_id`` and optionally ``context_window``.

        Returns:
            The number of models in the listing.
        """
        ids = {m["model_id"] for m in listed}
        with self._tx() as c:
            for m in listed:
                c.execute(
                    "INSERT INTO models (provider_id, model_id, context_window, kind) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(provider_id, model_id) DO UPDATE SET available = 1, "
                    "context_window = COALESCE(models.context_window, excluded.context_window)",
                    (provider_id, m["model_id"], m.get("context_window"), guess_kind(m["model_id"])),
                )
            for row in c.execute("SELECT model_id FROM models WHERE provider_id = ? AND manual = 0", (provider_id,)).fetchall():
                if row[0] not in ids:
                    c.execute(
                        "UPDATE models SET available = 0 WHERE provider_id = ? AND model_id = ?",
                        (provider_id, row[0]),
                    )
        return len(ids)

    def model_save(self, provider_id: int, model_id: str, values: dict[str, Any], manual: bool = False) -> None:
        """Create or update a model's overridable fields."""
        row = {k: values[k] for k in _MODEL_FIELDS if k in values}
        with self._tx() as c:
            c.execute(
                "INSERT OR IGNORE INTO models (provider_id, model_id, manual) VALUES (?, ?, ?)",
                (provider_id, model_id, int(manual)),
            )
            if row:
                assignments = ", ".join(f"{k} = ?" for k in row)
                c.execute(
                    f"UPDATE models SET {assignments} WHERE provider_id = ? AND model_id = ?",
                    (*row.values(), provider_id, model_id),
                )

    def model_get(self, provider_id: int, model_id: str) -> dict[str, Any] | None:
        """Return one model row."""
        return self._one("SELECT * FROM models WHERE provider_id = ? AND model_id = ?", (provider_id, model_id))

    def model_list(self, provider_id: int) -> list[dict[str, Any]]:
        """Return a provider's models ordered by id."""
        return self._query("SELECT * FROM models WHERE provider_id = ? ORDER BY model_id", (provider_id,))

    def model_delete(self, provider_id: int, model_id: str) -> bool:
        """Delete a model (and route entries that use it)."""
        with self._tx() as c:
            c.execute("DELETE FROM route_entries WHERE provider_id = ? AND model_id = ?", (provider_id, model_id))
            self._renumber_routes(c)
            return (
                c.execute("DELETE FROM models WHERE provider_id = ? AND model_id = ?", (provider_id, model_id)).rowcount
                > 0
            )

    def model_set_available(self, provider_id: int, model_id: str, available: bool) -> None:
        """Mark a model available or missing."""
        with self._tx() as c:
            c.execute(
                "UPDATE models SET available = ? WHERE provider_id = ? AND model_id = ?",
                (int(available), provider_id, model_id),
            )

    def model_set_test(self, provider_id: int, model_id: str, ok: bool, error: str = "") -> None:
        """Record the result of a tool-calling test."""
        with self._tx() as c:
            c.execute(
                "UPDATE models SET test_ok = ?, test_at = ?, test_error = ?, tools = ? "
                "WHERE provider_id = ? AND model_id = ?",
                (int(ok), time.time(), error[:500], int(ok), provider_id, model_id),
            )

    def model_set_vision_test(self, provider_id: int, model_id: str, ok: bool, error: str = "") -> None:
        """Record the result of a vision test (a model can read an image) without touching the tool-calling test."""
        with self._tx() as c:
            c.execute(
                "UPDATE models SET vision = ?, vision_test_at = ?, vision_error = ? WHERE provider_id = ? AND model_id = ?",
                (int(ok), time.time(), error[:500], provider_id, model_id),
            )

    def usage_calls_since(self, agent: str, since: float) -> int:
        """Successful calls of an agent since a time (the vision route's daily count)."""
        row = self._one("SELECT COUNT(*) AS n FROM llm_usage WHERE agent = ? AND ts >= ? AND outcome = 'success'", (agent, since))
        return int(row["n"]) if row else 0

    def model_set_embedding_test(self, provider_id: int, model_id: str, ok: bool, dim: int | None, error: str = "") -> None:
        """Record the result of an embedding test (the model becomes kind ``embedding``)."""
        with self._tx() as c:
            c.execute(
                "UPDATE models SET kind = 'embedding', test_ok = ?, test_at = ?, test_error = ?, dim = ? "
                "WHERE provider_id = ? AND model_id = ?",
                (int(ok), time.time(), error[:500], dim if ok else None, provider_id, model_id),
            )

    # Routes

    def _renumber_routes(self, c: sqlite3.Connection) -> None:
        for (name,) in c.execute("SELECT name FROM routes").fetchall():
            ids = [r[0] for r in c.execute("SELECT id FROM route_entries WHERE route_name = ? ORDER BY position, id", (name,))]
            for position, entry_id in enumerate(ids):
                c.execute("UPDATE route_entries SET position = ? WHERE id = ?", (position, entry_id))

    def route_names(self) -> list[str]:
        """Return route names, with ``default`` and ``review`` first."""
        names = [r["name"] for r in self._query("SELECT name FROM routes ORDER BY name")]
        return sorted(names, key=lambda n: ({"default": 0, "review": 1, "vision": 2}.get(n, 3), n))

    def route_exists(self, name: str) -> bool:
        """Whether a route exists."""
        return self._one("SELECT 1 AS x FROM routes WHERE name = ?", (name,)) is not None

    def route_create(self, name: str) -> bool:
        """Create an empty route; returns false if it exists."""
        with self._tx() as c:
            return c.execute("INSERT OR IGNORE INTO routes (name, created) VALUES (?, ?)", (name, time.time())).rowcount > 0

    def route_delete(self, name: str) -> bool:
        """Delete a route and its entries."""
        with self._tx() as c:
            return c.execute("DELETE FROM routes WHERE name = ?", (name,)).rowcount > 0

    def route_entries(self, name: str) -> list[dict[str, Any]]:
        """Return a route's entries in order, with provider slug and model flags."""
        rows = self._query(
            "SELECT e.id, e.route_name, e.position, e.provider_id, e.model_id, e.key_id, e.timeouts, "
            "p.slug AS provider, m.available AS model_available, m.test_ok AS test_ok, "
            "m.context_window AS context_window "
            "FROM route_entries e JOIN providers p ON p.id = e.provider_id "
            "LEFT JOIN models m ON m.provider_id = e.provider_id AND m.model_id = e.model_id "
            "WHERE e.route_name = ? ORDER BY e.position, e.id",
            (name,),
        )
        return [_decode(r) for r in rows]

    def route_entry_get(self, entry_id: int) -> dict[str, Any] | None:
        """Return one route entry."""
        row = self._one("SELECT * FROM route_entries WHERE id = ?", (entry_id,))
        return _decode(row) if row else None

    def route_set_entries(self, name: str, entries: list[dict[str, Any]]) -> None:
        """Replace a route's entries (``provider_id``, ``model_id``, ``key_id``, ``timeouts``)."""
        with self._tx() as c:
            c.execute("DELETE FROM route_entries WHERE route_name = ?", (name,))
            for position, e in enumerate(entries):
                c.execute(
                    "INSERT INTO route_entries (route_name, position, provider_id, model_id, key_id, timeouts) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (name, position, e["provider_id"], e["model_id"], e.get("key_id"), json.dumps(e.get("timeouts") or {})),
                )

    # Health

    def health_get(self, kind: str, ref: str) -> dict[str, Any] | None:
        """Return the health row of an entry or key."""
        return self._one("SELECT * FROM provider_health WHERE kind = ? AND ref = ?", (kind, ref))

    def health_list(self) -> list[dict[str, Any]]:
        """Return every health row."""
        return self._query("SELECT * FROM provider_health")

    def health_set(
        self, kind: str, ref: str, state: str, until: float | None, failures: int, reason: str, last_error: str
    ) -> None:
        """Insert or replace a health row."""
        with self._tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO provider_health (kind, ref, state, until, failures, reason, last_error, updated) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (kind, ref, state, until, failures, reason, last_error[:500], time.time()),
            )

    def health_clear(self, kind: str, ref: str) -> None:
        """Remove a health row."""
        with self._tx() as c:
            c.execute("DELETE FROM provider_health WHERE kind = ? AND ref = ?", (kind, ref))

    def health_clear_provider(self, provider_id: int, key_ids: list[int]) -> None:
        """Clear the health of every entry and key of a provider."""
        with self._tx() as c:
            c.execute("DELETE FROM provider_health WHERE kind = 'entry' AND ref LIKE ?", (f"{provider_id}|%",))
            for key_id in key_ids:
                c.execute("DELETE FROM provider_health WHERE kind = 'key' AND ref = ?", (str(key_id),))

    # Usage

    def usage_add(self, row: dict[str, Any]) -> None:
        """Record one model call."""
        with self._tx() as c:
            c.execute(
                "INSERT INTO llm_usage (ts, provider_id, provider, model, key_id, route, source, agent, "
                "tokens_in, tokens_out, latency_ms, outcome, error, fallback) "
                "VALUES (:ts, :provider_id, :provider, :model, :key_id, :route, :source, :agent, "
                ":tokens_in, :tokens_out, :latency_ms, :outcome, :error, :fallback)",
                {
                    "ts": time.time(), "provider_id": None, "key_id": None, "route": None, "source": None,
                    "agent": None, "tokens_in": 0, "tokens_out": 0, "latency_ms": 0, "error": "", "fallback": 0,
                    **row,
                },
            )

    def usage_tokens_since(self, provider_id: int, since: float) -> int:
        """Total tokens (in + out) of successful calls to a provider since a time."""
        row = self._one(
            "SELECT COALESCE(SUM(tokens_in + tokens_out), 0) AS n FROM llm_usage "
            "WHERE provider_id = ? AND ts >= ? AND outcome = 'success'",
            (provider_id, since),
        )
        return int(row["n"]) if row else 0

    def usage_provider_summary(self, since: float) -> dict[int, dict[str, Any]]:
        """Per-provider tokens, calls, fallbacks and last error since a time."""
        rows = self._query(
            "SELECT provider_id, SUM(CASE WHEN outcome = 'success' THEN tokens_in + tokens_out ELSE 0 END) AS tokens, "
            "SUM(outcome = 'success') AS calls, SUM(outcome = 'success' AND fallback = 1) AS fallbacks, "
            "SUM(outcome != 'success') AS errors FROM llm_usage WHERE ts >= ? AND provider_id IS NOT NULL "
            "GROUP BY provider_id",
            (since,),
        )
        return {r["provider_id"]: r for r in rows}

    def usage_last_error(self, provider_id: int) -> dict[str, Any] | None:
        """Most recent failed call of a provider."""
        return self._one(
            "SELECT ts, model, outcome, error FROM llm_usage WHERE provider_id = ? AND outcome != 'success' "
            "ORDER BY id DESC LIMIT 1",
            (provider_id,),
        )

    def usage_overview(self, since: float) -> list[dict[str, Any]]:
        """Tokens and calls per provider and model since a time, with the model's price per million tokens."""
        return self._query(
            "SELECT u.provider AS provider, u.model AS model, SUM(u.outcome = 'success') AS calls, "
            "SUM(u.outcome != 'success') AS errors, "
            "COALESCE(SUM(CASE WHEN u.outcome = 'success' THEN u.tokens_in END), 0) AS tokens_in, "
            "COALESCE(SUM(CASE WHEN u.outcome = 'success' THEN u.tokens_out END), 0) AS tokens_out, "
            "m.price_in AS price_in, m.price_out AS price_out "
            "FROM llm_usage u LEFT JOIN models m ON m.provider_id = u.provider_id AND m.model_id = u.model "
            "WHERE u.ts >= ? GROUP BY u.provider, u.model ORDER BY u.provider, u.model",
            (since,),
        )

    def usage_daily(self, since: float) -> list[dict[str, Any]]:
        """Tokens per day and provider since a time (successful calls)."""
        return self._query(
            "SELECT date(ts, 'unixepoch') AS day, provider, SUM(tokens_in + tokens_out) AS tokens "
            "FROM llm_usage WHERE ts >= ? AND outcome = 'success' GROUP BY day, provider ORDER BY day, provider",
            (since,),
        )

    def usage_daily_models(self, since: float) -> list[dict[str, Any]]:
        """Calls, tokens and model price per day, provider and model since a time."""
        return self._query(
            "SELECT date(u.ts, 'unixepoch') AS day, u.provider AS provider, u.model AS model, SUM(u.outcome = 'success') AS calls, "
            "SUM(u.outcome != 'success') AS errors, "
            "COALESCE(SUM(CASE WHEN u.outcome = 'success' THEN u.tokens_in END), 0) AS tokens_in, "
            "COALESCE(SUM(CASE WHEN u.outcome = 'success' THEN u.tokens_out END), 0) AS tokens_out, "
            "m.price_in AS price_in, m.price_out AS price_out "
            "FROM llm_usage u LEFT JOIN models m ON m.provider_id = u.provider_id AND m.model_id = u.model "
            "WHERE u.ts >= ? GROUP BY day, u.provider, u.model ORDER BY day DESC, u.provider",
            (since,),
        )

    def usage_report(self, since: float) -> list[dict[str, Any]]:
        """Daily usage grouped by provider, model and agent since a time."""
        return self._query(
            "SELECT date(ts, 'unixepoch') AS day, provider, model, COALESCE(agent, '') AS agent, "
            "SUM(outcome = 'success') AS calls, SUM(outcome != 'success') AS errors, "
            "SUM(tokens_in) AS tokens_in, SUM(tokens_out) AS tokens_out, "
            "CAST(AVG(CASE WHEN outcome = 'success' THEN latency_ms END) AS INTEGER) AS avg_latency_ms "
            "FROM llm_usage WHERE ts >= ? GROUP BY day, provider, model, agent ORDER BY day DESC, provider, model",
            (since,),
        )
