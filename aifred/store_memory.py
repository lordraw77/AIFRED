"""Database access for long-term memory: facts, entity links, session episodes and extraction cursors.

Mixed into :class:`aifred.store.Store`, which supplies ``_tx``, ``_query`` and ``_one``.
Every read takes the subjects it may see; an empty list sees nothing, which is how one owner, user, chat or
agent is kept from reading another's facts.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Sequence
from contextlib import AbstractContextManager
from typing import Any

_FACT_COLUMNS = (
    "id, subject, kind, text, importance, source, source_session, status, superseded_by, "
    "emb_model, created, updated, last_used, use_count, archived"
)
_UPDATABLE = ("kind", "text", "importance", "status", "superseded_by", "embedding", "emb_model", "archived")


def _marks(items: Sequence[object]) -> str:
    return ",".join("?" * len(items))


class MemoryStoreMixin:
    """Long-term memory rows."""

    def _tx(self) -> AbstractContextManager[sqlite3.Connection]:  # pragma: no cover - provided by Store
        raise NotImplementedError

    def _query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:  # pragma: no cover
        raise NotImplementedError

    def _one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:  # pragma: no cover
        raise NotImplementedError

    # Facts

    def mem_fact_add(
        self,
        subject: str,
        text: str,
        kind: str = "fact",
        importance: float = 0.5,
        source: str = "auto",
        source_session: str = "",
        entities: Sequence[str] = (),
        embedding: bytes | None = None,
        emb_model: str | None = None,
    ) -> int:
        """Insert a fact and its entity links; returns its id."""
        now = time.time()
        with self._tx() as c:
            cur = c.execute(
                "INSERT INTO mem_facts (subject, kind, text, importance, source, source_session, embedding, emb_model, created, updated) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (subject, kind, text, importance, source, source_session, embedding, emb_model, now, now),
            )
            fact_id = int(cur.lastrowid or 0)
            c.executemany("INSERT OR IGNORE INTO mem_links (fact_id, entity) VALUES (?, ?)", [(fact_id, e) for e in entities])
            return fact_id

    def mem_fact_get(self, fact_id: int, subjects: Sequence[str]) -> dict[str, Any] | None:
        """A fact, only if it belongs to one of ``subjects``."""
        if not subjects:
            return None
        row = self._one(
            f"SELECT {_FACT_COLUMNS} FROM mem_facts WHERE id = ? AND subject IN ({_marks(subjects)})", (fact_id, *subjects)
        )
        return self._with_entities([row])[0] if row else None

    def mem_fact_update(self, fact_id: int, subjects: Sequence[str], entities: Sequence[str] | None = None, **fields: Any) -> bool:
        """Change fields of a fact (and replace its entities when given); False if it is not visible to ``subjects``."""
        unknown = set(fields) - set(_UPDATABLE)
        if unknown:
            raise ValueError(f"cannot update {sorted(unknown)}")
        if not subjects:
            return False
        sets = [f"{k} = ?" for k in fields] + ["updated = ?"]
        with self._tx() as c:
            cur = c.execute(
                f"UPDATE mem_facts SET {', '.join(sets)} WHERE id = ? AND subject IN ({_marks(subjects)})",
                (*fields.values(), time.time(), fact_id, *subjects),
            )
            if not cur.rowcount:
                return False
            if entities is not None:
                c.execute("DELETE FROM mem_links WHERE fact_id = ?", (fact_id,))
                c.executemany("INSERT OR IGNORE INTO mem_links (fact_id, entity) VALUES (?, ?)", [(fact_id, e) for e in entities])
            return True

    def mem_fact_delete(self, fact_id: int, subjects: Sequence[str]) -> bool:
        """Delete one fact for good."""
        if not subjects:
            return False
        with self._tx() as c:
            return c.execute(f"DELETE FROM mem_facts WHERE id = ? AND subject IN ({_marks(subjects)})", (fact_id, *subjects)).rowcount > 0

    def mem_forget(self, subject: str) -> int:
        """Delete everything stored for one subject (facts, episodes); returns the number of facts removed."""
        with self._tx() as c:
            n = c.execute("DELETE FROM mem_facts WHERE subject = ?", (subject,)).rowcount
            c.execute("DELETE FROM mem_episodes WHERE subject = ?", (subject,))
            return n

    def mem_fact_list(
        self,
        subjects: Sequence[str],
        status: str | None = "active",
        kind: str | None = None,
        source: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Facts of ``subjects``, most recently changed first."""
        if not subjects:
            return []
        where, params = [f"subject IN ({_marks(subjects)})"], list(subjects)
        for column, value in (("status", status), ("kind", kind), ("source", source)):
            if value is not None:
                where.append(f"{column} = ?")
                params.append(value)
        rows = self._query(
            f"SELECT {_FACT_COLUMNS} FROM mem_facts WHERE {' AND '.join(where)} ORDER BY updated DESC, id DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        )
        return self._with_entities(rows)

    def mem_fact_search_text(self, subjects: Sequence[str], match: str, limit: int = 20) -> list[dict[str, Any]]:
        """Active facts matching an FTS5 ``match`` expression, best first (``rank`` is bm25: lower is better)."""
        if not subjects:
            return []
        rows = self._query(
            f"SELECT f.id, f.subject, f.kind, f.text, f.importance, f.created, f.updated, f.last_used, f.use_count, bm25(mem_facts_fts) AS rank "
            f"FROM mem_facts_fts JOIN mem_facts f ON f.id = mem_facts_fts.rowid "
            f"WHERE mem_facts_fts MATCH ? AND f.status = 'active' AND f.subject IN ({_marks(subjects)}) ORDER BY rank LIMIT ?",
            (match, *subjects, limit),
        )
        return rows

    def mem_fact_vectors(self, subjects: Sequence[str], emb_model: str) -> list[dict[str, Any]]:
        """``id`` and ``embedding`` of the active facts embedded with ``emb_model``."""
        if not subjects:
            return []
        return self._query(
            f"SELECT id, embedding FROM mem_facts WHERE status = 'active' AND embedding IS NOT NULL AND emb_model = ? "
            f"AND subject IN ({_marks(subjects)})",
            (emb_model, *subjects),
        )

    def mem_facts_by_ids(self, ids: Sequence[int], subjects: Sequence[str]) -> list[dict[str, Any]]:
        """Active facts by id, restricted to ``subjects``; the order of ``ids`` is not kept."""
        if not subjects or not ids:
            return []
        rows = self._query(
            f"SELECT {_FACT_COLUMNS} FROM mem_facts WHERE status = 'active' AND id IN ({_marks(ids)}) AND subject IN ({_marks(subjects)})",
            (*ids, *subjects),
        )
        return self._with_entities(rows)

    def mem_fact_touch(self, ids: Sequence[int]) -> None:
        """Record that facts were recalled (feeds recency and retention)."""
        if not ids:
            return
        with self._tx() as c:
            c.execute(f"UPDATE mem_facts SET last_used = ?, use_count = use_count + 1 WHERE id IN ({_marks(ids)})", (time.time(), *ids))

    def mem_entity_facts(self, subjects: Sequence[str], entities: Sequence[str], limit: int = 20) -> list[dict[str, Any]]:
        """Active facts that mention any of ``entities`` (lower-cased names), for the graph hop."""
        if not subjects or not entities:
            return []
        rows = self._query(
            f"SELECT DISTINCT f.id FROM mem_links l JOIN mem_facts f ON f.id = l.fact_id "
            f"WHERE l.entity IN ({_marks(entities)}) AND f.status = 'active' AND f.subject IN ({_marks(subjects)}) "
            f"ORDER BY f.importance DESC, f.updated DESC LIMIT ?",
            (*entities, *subjects, limit),
        )
        return self.mem_facts_by_ids([r["id"] for r in rows], subjects)

    def _with_entities(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not rows:
            return rows
        ids = [r["id"] for r in rows]
        found: dict[int, list[str]] = {i: [] for i in ids}
        for link in self._query(f"SELECT fact_id, entity FROM mem_links WHERE fact_id IN ({_marks(ids)}) ORDER BY entity", tuple(ids)):
            found[link["fact_id"]].append(link["entity"])
        for r in rows:
            r["entities"] = found[r["id"]]
        return rows

    def mem_facts_to_embed(self, emb_model: str, limit: int = 64) -> list[dict[str, Any]]:
        """Active facts with no vector, or one made by another model, oldest first."""
        return self._query(
            "SELECT id, subject, text FROM mem_facts WHERE status = 'active' AND (embedding IS NULL OR emb_model IS NOT ?) "
            "ORDER BY id LIMIT ?",
            (emb_model, limit),
        )

    # Retention

    def mem_archive_stale(self, cutoff: float) -> int:
        """Archive active facts not recalled (or, if never recalled, not changed) since ``cutoff``."""
        now = time.time()
        with self._tx() as c:
            return c.execute(
                "UPDATE mem_facts SET status = 'archived', archived = ?, updated = ? "
                "WHERE status = 'active' AND COALESCE(last_used, updated) < ?",
                (now, now, cutoff),
            ).rowcount

    def mem_purge_archived(self, cutoff: float) -> int:
        """Delete archived or superseded facts archived before ``cutoff``."""
        with self._tx() as c:
            return c.execute(
                "DELETE FROM mem_facts WHERE status IN ('archived', 'superseded') AND COALESCE(archived, updated) < ?", (cutoff,)
            ).rowcount

    def mem_stats(self, subjects: Sequence[str] | None = None) -> dict[str, Any]:
        """Counts per status (all subjects when ``subjects`` is None) and the subjects that have facts."""
        where, params = ("", ()) if subjects is None else (f" WHERE subject IN ({_marks(subjects)})", tuple(subjects))
        if subjects is not None and not subjects:
            return {"total": 0, "by_status": {}, "subjects": []}
        by_status = {r["status"]: r["n"] for r in self._query(f"SELECT status, COUNT(*) AS n FROM mem_facts{where} GROUP BY status", params)}
        names = [r["subject"] for r in self._query(f"SELECT DISTINCT subject FROM mem_facts{where} ORDER BY subject", params)]
        return {"total": sum(by_status.values()), "by_status": by_status, "subjects": names}

    # Episodes and extraction cursors

    def mem_episode_set(self, subject: str, session_id: str, summary: str, upto: int, embedding: bytes | None = None, emb_model: str | None = None) -> None:
        """Store or replace the summary of one session for one subject."""
        with self._tx() as c:
            c.execute(
                "INSERT INTO mem_episodes (subject, session_id, summary, upto, embedding, emb_model, created) VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(subject, session_id) DO UPDATE SET summary = excluded.summary, upto = excluded.upto, "
                "embedding = excluded.embedding, emb_model = excluded.emb_model, created = excluded.created",
                (subject, session_id, summary, upto, embedding, emb_model, time.time()),
            )

    def mem_episode_list(self, subjects: Sequence[str], limit: int = 50) -> list[dict[str, Any]]:
        """Episodes of ``subjects``, newest first."""
        if not subjects:
            return []
        return self._query(
            f"SELECT id, subject, session_id, summary, upto, created FROM mem_episodes WHERE subject IN ({_marks(subjects)}) "
            f"ORDER BY created DESC LIMIT ?",
            (*subjects, limit),
        )

    def mem_cursor_get(self, session_id: str) -> dict[str, Any]:
        """Last message already read for extraction and turns since, for one session."""
        row = self._one("SELECT last_message_id, turns, updated FROM mem_cursor WHERE session_id = ?", (session_id,))
        return row or {"last_message_id": 0, "turns": 0, "updated": 0.0}

    def mem_cursor_set(self, session_id: str, last_message_id: int, turns: int = 0) -> None:
        """Move the extraction cursor of a session."""
        with self._tx() as c:
            c.execute(
                "INSERT INTO mem_cursor (session_id, last_message_id, turns, updated) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET last_message_id = excluded.last_message_id, turns = excluded.turns, updated = excluded.updated",
                (session_id, last_message_id, turns, time.time()),
            )

    def mem_cursor_idle(self, cutoff: float, limit: int = 20) -> list[str]:
        """Sessions with turns not yet read for facts whose last turn is older than ``cutoff``, oldest first."""
        rows = self._query("SELECT session_id FROM mem_cursor WHERE turns > 0 AND updated < ? ORDER BY updated LIMIT ?", (cutoff, limit))
        return [r["session_id"] for r in rows]
