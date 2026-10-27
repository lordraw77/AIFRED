"""SQLite persistence (spec section 5).

One connection in WAL mode guarded by a lock; methods are synchronous and are
meant to be called from worker threads by async code. The schema is versioned
with ``PRAGMA user_version`` and migrations are forward-only (docs/analysis.md A6).
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from aifred.store_agents import AgentStoreMixin
from aifred.store_instructions import InstructionStoreMixin
from aifred.store_llm import LLMStoreMixin
from aifred.store_mcp import McpStoreMixin
from aifred.store_rag import RagStoreMixin
from aifred.store_sched import SchedulerStoreMixin

logger = logging.getLogger(__name__)

ROLES = ("system", "user", "assistant", "tool")

_MIGRATIONS: list[str] = [
    # 1: core tables, configuration and credentials
    """
    CREATE TABLE sessions (
        id TEXT PRIMARY KEY,
        source TEXT NOT NULL,
        title TEXT NOT NULL DEFAULT '',
        created REAL NOT NULL,
        updated REAL NOT NULL
    );
    CREATE INDEX sessions_updated ON sessions(updated);

    CREATE TABLE messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
        role TEXT NOT NULL CHECK (role IN ('system', 'user', 'assistant', 'tool')),
        content TEXT,
        tool_calls TEXT,
        tool_call_id TEXT,
        ts REAL NOT NULL
    );
    CREATE INDEX messages_session ON messages(session_id, id);

    CREATE VIRTUAL TABLE messages_fts USING fts5(
        content, content='messages', content_rowid='id',
        tokenize='unicode61 remove_diacritics 2'
    );
    CREATE TRIGGER messages_ai AFTER INSERT ON messages BEGIN
        INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
    END;
    CREATE TRIGGER messages_ad AFTER DELETE ON messages BEGIN
        INSERT INTO messages_fts(messages_fts, rowid, content) VALUES ('delete', old.id, old.content);
    END;
    CREATE TRIGGER messages_au AFTER UPDATE OF content ON messages BEGIN
        INSERT INTO messages_fts(messages_fts, rowid, content) VALUES ('delete', old.id, old.content);
        INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
    END;

    CREATE TABLE kv (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated REAL NOT NULL
    );

    CREATE TABLE journal (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL,
        kind TEXT NOT NULL,
        action TEXT NOT NULL,
        summary TEXT NOT NULL,
        session_id TEXT,
        status TEXT NOT NULL DEFAULT 'applied',
        data TEXT
    );
    CREATE INDEX journal_ts ON journal(ts);

    CREATE TABLE pending_writes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL,
        kind TEXT NOT NULL,
        payload TEXT NOT NULL,
        session_id TEXT,
        status TEXT NOT NULL DEFAULT 'pending'
            CHECK (status IN ('pending', 'approved', 'rejected')),
        decided REAL
    );
    CREATE INDEX pending_writes_status ON pending_writes(status, id);

    CREATE TABLE settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated REAL NOT NULL
    );

    CREATE TABLE admin (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        username TEXT NOT NULL,
        password_hash TEXT NOT NULL,
        created REAL NOT NULL,
        updated REAL NOT NULL
    );

    CREATE TABLE auth_sessions (
        token_hash TEXT PRIMARY KEY,
        csrf_token TEXT NOT NULL,
        created REAL NOT NULL,
        last_seen REAL NOT NULL,
        expires REAL NOT NULL,
        ip TEXT,
        user_agent TEXT
    );
    CREATE INDEX auth_sessions_expires ON auth_sessions(expires);

    CREATE TABLE api_keys (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        key_hash TEXT NOT NULL UNIQUE,
        scope TEXT NOT NULL CHECK (scope IN ('chat', 'admin')),
        created REAL NOT NULL,
        expires REAL,
        last_used REAL,
        revoked REAL
    );

    CREATE TABLE secrets (
        name TEXT PRIMARY KEY,
        value BLOB NOT NULL,
        updated REAL NOT NULL
    );
    """,
    # 2: LLM providers, models, routes, health and usage
    """
    CREATE TABLE providers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        slug TEXT NOT NULL UNIQUE,
        type TEXT NOT NULL,
        base_url TEXT NOT NULL,
        headers TEXT NOT NULL DEFAULT '{}',
        extra_body TEXT NOT NULL DEFAULT '{}',
        config TEXT NOT NULL DEFAULT '{}',
        connect_timeout REAL NOT NULL DEFAULT 10,
        first_token_timeout REAL NOT NULL DEFAULT 30,
        stall_timeout REAL NOT NULL DEFAULT 60,
        total_timeout REAL NOT NULL DEFAULT 300,
        verify_tls TEXT NOT NULL DEFAULT 'true',
        enabled INTEGER NOT NULL DEFAULT 1,
        budget_tokens_daily INTEGER,
        budget_tokens_monthly INTEGER,
        created REAL NOT NULL,
        updated REAL NOT NULL
    );

    CREATE TABLE provider_keys (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        provider_id INTEGER NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
        label TEXT NOT NULL DEFAULT '',
        last4 TEXT NOT NULL DEFAULT '',
        position INTEGER NOT NULL DEFAULT 0,
        created REAL NOT NULL
    );
    CREATE INDEX provider_keys_provider ON provider_keys(provider_id, position);

    CREATE TABLE models (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        provider_id INTEGER NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
        model_id TEXT NOT NULL,
        context_window INTEGER,
        tools INTEGER,
        streaming INTEGER,
        vision INTEGER,
        thinking_control TEXT NOT NULL DEFAULT 'none'
            CHECK (thinking_control IN ('none', 'qwen_template', 'reasoning_effort')),
        price_in REAL,
        price_out REAL,
        manual INTEGER NOT NULL DEFAULT 0,
        available INTEGER NOT NULL DEFAULT 1,
        test_ok INTEGER,
        test_at REAL,
        test_error TEXT,
        UNIQUE (provider_id, model_id)
    );

    CREATE TABLE routes (
        name TEXT PRIMARY KEY,
        created REAL NOT NULL
    );
    INSERT INTO routes (name, created) VALUES ('default', 0), ('review', 0);

    CREATE TABLE route_entries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        route_name TEXT NOT NULL REFERENCES routes(name) ON DELETE CASCADE,
        position INTEGER NOT NULL,
        provider_id INTEGER NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
        model_id TEXT NOT NULL,
        key_id INTEGER REFERENCES provider_keys(id) ON DELETE SET NULL,
        timeouts TEXT NOT NULL DEFAULT '{}'
    );
    CREATE INDEX route_entries_route ON route_entries(route_name, position);

    CREATE TABLE provider_health (
        kind TEXT NOT NULL CHECK (kind IN ('entry', 'key')),
        ref TEXT NOT NULL,
        state TEXT NOT NULL CHECK (state IN ('cooldown', 'disabled')),
        until REAL,
        failures INTEGER NOT NULL DEFAULT 0,
        reason TEXT NOT NULL DEFAULT '',
        last_error TEXT NOT NULL DEFAULT '',
        updated REAL NOT NULL,
        PRIMARY KEY (kind, ref)
    );

    CREATE TABLE llm_usage (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL,
        provider_id INTEGER,
        provider TEXT NOT NULL,
        model TEXT NOT NULL,
        key_id INTEGER,
        route TEXT,
        source TEXT,
        agent TEXT,
        tokens_in INTEGER NOT NULL DEFAULT 0,
        tokens_out INTEGER NOT NULL DEFAULT 0,
        latency_ms INTEGER NOT NULL DEFAULT 0,
        outcome TEXT NOT NULL,
        error TEXT NOT NULL DEFAULT '',
        fallback INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX llm_usage_ts ON llm_usage(ts);
    CREATE INDEX llm_usage_provider ON llm_usage(provider_id, ts);
    """,
    # 3: embedding models and the local knowledge base (RAG)
    """
    ALTER TABLE models ADD COLUMN kind TEXT NOT NULL DEFAULT 'chat' CHECK (kind IN ('chat', 'embedding'));
    ALTER TABLE models ADD COLUMN dim INTEGER;

    CREATE TABLE rag_collections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        description TEXT NOT NULL DEFAULT '',
        embed_provider TEXT,
        embed_model TEXT,
        generation INTEGER NOT NULL DEFAULT 0,
        created REAL NOT NULL
    );

    CREATE TABLE rag_documents (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        collection_id INTEGER NOT NULL REFERENCES rag_collections(id) ON DELETE CASCADE,
        source TEXT NOT NULL,
        origin TEXT NOT NULL DEFAULT 'file' CHECK (origin IN ('file', 'upload', 'url')),
        kind TEXT NOT NULL DEFAULT 'text',
        title TEXT NOT NULL DEFAULT '',
        sha256 TEXT NOT NULL DEFAULT '',
        size INTEGER NOT NULL DEFAULT 0,
        chunks INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'queued'
            CHECK (status IN ('queued', 'indexing', 'ready', 'waiting', 'partial', 'error')),
        error TEXT NOT NULL DEFAULT '',
        flagged INTEGER NOT NULL DEFAULT 0,
        embed_tag TEXT,
        dim INTEGER,
        attempts INTEGER NOT NULL DEFAULT 0,
        retry_at REAL,
        created REAL NOT NULL,
        updated REAL NOT NULL,
        UNIQUE (collection_id, source)
    );
    CREATE INDEX rag_documents_status ON rag_documents(status, retry_at);

    CREATE TABLE rag_chunks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        document_id INTEGER NOT NULL REFERENCES rag_documents(id) ON DELETE CASCADE,
        ord INTEGER NOT NULL,
        heading TEXT NOT NULL DEFAULT '',
        page INTEGER,
        vision INTEGER NOT NULL DEFAULT 0,
        text TEXT NOT NULL,
        text_sha TEXT NOT NULL,
        embedding BLOB
    );
    CREATE INDEX rag_chunks_doc ON rag_chunks(document_id, ord);

    CREATE VIRTUAL TABLE rag_chunks_fts USING fts5(
        text, heading, content='rag_chunks', content_rowid='id',
        tokenize='unicode61 remove_diacritics 2'
    );
    CREATE TRIGGER rag_chunks_ai AFTER INSERT ON rag_chunks BEGIN
        INSERT INTO rag_chunks_fts(rowid, text, heading) VALUES (new.id, new.text, new.heading);
    END;
    CREATE TRIGGER rag_chunks_ad AFTER DELETE ON rag_chunks BEGIN
        INSERT INTO rag_chunks_fts(rag_chunks_fts, rowid, text, heading) VALUES ('delete', old.id, old.text, old.heading);
    END;
    CREATE TRIGGER rag_chunks_au AFTER UPDATE OF text, heading ON rag_chunks BEGIN
        INSERT INTO rag_chunks_fts(rag_chunks_fts, rowid, text, heading) VALUES ('delete', old.id, old.text, old.heading);
        INSERT INTO rag_chunks_fts(rowid, text, heading) VALUES (new.id, new.text, new.heading);
    END;
    """,
    # 4: MCP servers (header values live in the vault)
    """
    CREATE TABLE mcp_servers (
        name TEXT PRIMARY KEY,
        url TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1,
        header_names TEXT NOT NULL DEFAULT '[]',
        verify_tls TEXT NOT NULL DEFAULT 'true',
        timeout REAL,
        connect_timeout REAL,
        protocol_version TEXT NOT NULL DEFAULT 'auto',
        include TEXT NOT NULL DEFAULT '[]',
        exclude TEXT NOT NULL DEFAULT '[]',
        max_tools INTEGER,
        created REAL NOT NULL,
        updated REAL NOT NULL
    );
    """,
    # 5: instructions (what an MCP server, toolset or tool is for)
    """
    CREATE TABLE instructions (
        scope TEXT PRIMARY KEY,
        text TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1,
        updated REAL NOT NULL
    );
    """,
    # 6: vision model tests and the cache of image descriptions (knowledge base, second step)
    """
    INSERT OR IGNORE INTO routes (name, created) VALUES ('vision', 0);
    ALTER TABLE models ADD COLUMN vision_test_at REAL;
    ALTER TABLE models ADD COLUMN vision_error TEXT NOT NULL DEFAULT '';

    CREATE TABLE rag_vision_cache (
        sha256 TEXT NOT NULL,
        prompt TEXT NOT NULL,
        text TEXT NOT NULL,
        model TEXT NOT NULL DEFAULT '',
        created REAL NOT NULL,
        PRIMARY KEY (sha256, prompt)
    );
    """,
    # 7: scheduled tasks (docs/specs.md section 14)
    """
    CREATE TABLE schedules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        expression TEXT NOT NULL,
        prompt TEXT NOT NULL,
        deliver_chat INTEGER,
        enabled INTEGER NOT NULL DEFAULT 1,
        created_by TEXT NOT NULL DEFAULT 'user',
        created REAL NOT NULL,
        next_run REAL,
        last_run REAL,
        last_status TEXT NOT NULL DEFAULT '',
        run_count INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX idx_schedules_due ON schedules(enabled, next_run);

    CREATE TABLE schedule_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        schedule_id INTEGER NOT NULL REFERENCES schedules(id) ON DELETE CASCADE,
        started REAL NOT NULL,
        finished REAL,
        status TEXT NOT NULL DEFAULT 'running',
        session_id TEXT NOT NULL DEFAULT '',
        output TEXT NOT NULL DEFAULT '',
        error TEXT NOT NULL DEFAULT '',
        delivered INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX idx_schedule_runs ON schedule_runs(schedule_id, started);
    """,
    # 8: sub-agents (docs/specs.md section 14.1)
    """
    CREATE TABLE agents (
        name TEXT PRIMARY KEY,
        description TEXT NOT NULL,
        role TEXT NOT NULL,
        route TEXT NOT NULL DEFAULT 'default',
        skills TEXT NOT NULL DEFAULT '[]',
        toolsets TEXT NOT NULL DEFAULT '[]',
        memory TEXT NOT NULL DEFAULT 'none',
        max_iterations INTEGER,
        timeout INTEGER,
        token_budget INTEGER,
        enabled INTEGER NOT NULL DEFAULT 0,
        created REAL NOT NULL,
        updated REAL NOT NULL
    );

    CREATE TABLE agent_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        agent TEXT NOT NULL,
        parent_session TEXT NOT NULL DEFAULT '',
        session_id TEXT NOT NULL,
        task TEXT NOT NULL,
        started REAL NOT NULL,
        finished REAL,
        status TEXT NOT NULL DEFAULT 'running',
        output TEXT NOT NULL DEFAULT '',
        error TEXT NOT NULL DEFAULT '',
        tokens INTEGER NOT NULL DEFAULT 0,
        model TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX idx_agent_runs ON agent_runs(agent, id);

    ALTER TABLE sessions ADD COLUMN parent_id TEXT;
    """,
]


def _like_prefix(prefix: str | None) -> str:
    """LIKE pattern matching ids that start with ``prefix`` (everything when ``None``)."""
    if not prefix:
        return "%"
    return prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


class Store(LLMStoreMixin, RagStoreMixin, McpStoreMixin, InstructionStoreMixin, SchedulerStoreMixin, AgentStoreMixin):
    """Thread-safe access to the AIFRED database.

    Args:
        path: Database file; ``:memory:`` is accepted for tests.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._migrate()

    def close(self) -> None:
        """Close the connection."""
        with self._lock:
            self._conn.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")

    def _query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def _one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        rows = self._query(sql, params)
        return rows[0] if rows else None

    def _migrate(self) -> None:
        with self._lock:
            version = self._conn.execute("PRAGMA user_version").fetchone()[0]
            if version > len(_MIGRATIONS):
                raise RuntimeError(
                    f"Database schema version {version} is newer than this AIFRED "
                    f"({len(_MIGRATIONS)}); upgrade AIFRED or restore a backup"
                )
            for number, script in enumerate(_MIGRATIONS[version:], start=version + 1):
                logger.info("Applying database migration %d", number)
                # executescript commits first, so wrap the script in its own transaction
                self._conn.executescript(f"BEGIN IMMEDIATE;\n{script}\nPRAGMA user_version={number};\nCOMMIT;")

    @property
    def schema_version(self) -> int:
        """Current schema version."""
        with self._lock:
            return int(self._conn.execute("PRAGMA user_version").fetchone()[0])

    # Sessions and messages

    def ensure_session(self, session_id: str, source: str, title: str = "") -> None:
        """Create a session if it does not exist."""
        now = time.time()
        with self._tx() as c:
            c.execute(
                "INSERT OR IGNORE INTO sessions (id, source, title, created, updated) VALUES (?, ?, ?, ?, ?)",
                (session_id, source, title, now, now),
            )

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        """Return a session row with its message count."""
        return self._one(
            "SELECT s.*, (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) AS message_count "
            "FROM sessions s WHERE s.id = ?",
            (session_id,),
        )

    def list_sessions(
        self, source: str | None = None, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]:
        """List sessions, most recently updated first."""
        where, params = ("WHERE s.source = ?", (source,)) if source else ("", ())
        return self._query(
            "SELECT s.*, (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) AS message_count "
            f"FROM sessions s {where} ORDER BY s.updated DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        )

    def rename_session(self, session_id: str, title: str) -> bool:
        """Set a session title; returns whether the session exists."""
        with self._tx() as c:
            return c.execute("UPDATE sessions SET title = ? WHERE id = ?", (title, session_id)).rowcount > 0

    def delete_session(self, session_id: str) -> bool:
        """Delete a session, its messages and the sub-agent sessions it started; returns whether it existed."""
        with self._tx() as c:
            c.execute("DELETE FROM sessions WHERE parent_id = ?", (session_id,))
            return c.execute("DELETE FROM sessions WHERE id = ?", (session_id,)).rowcount > 0

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str | None,
        tool_calls: list[dict[str, Any]] | None = None,
        tool_call_id: str | None = None,
    ) -> int:
        """Append a message to an existing session.

        Returns:
            The message id.

        Raises:
            ValueError: If the role is invalid.
            sqlite3.IntegrityError: If the session does not exist.
        """
        if role not in ROLES:
            raise ValueError(f"invalid role {role!r}")
        now = time.time()
        with self._tx() as c:
            cur = c.execute(
                "INSERT INTO messages (session_id, role, content, tool_calls, tool_call_id, ts) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, role, content, json.dumps(tool_calls) if tool_calls else None, tool_call_id, now),
            )
            c.execute("UPDATE sessions SET updated = ? WHERE id = ?", (now, session_id))
            return int(cur.lastrowid or 0)

    def messages(self, session_id: str, limit: int = 500, before_id: int | None = None) -> list[dict[str, Any]]:
        """Return stored messages in chronological order (for the dashboard)."""
        rows = self._query(
            "SELECT * FROM messages WHERE session_id = ? AND id < ? ORDER BY id DESC LIMIT ?",
            (session_id, before_id if before_id is not None else 2**63 - 1, limit),
        )
        rows.reverse()
        for row in rows:
            row["tool_calls"] = json.loads(row["tool_calls"]) if row["tool_calls"] else None
        return rows

    def history(self, session_id: str, limit: int) -> list[dict[str, Any]]:
        """Return the last ``limit`` messages in OpenAI chat format.

        Everything before the first ``user`` message of the window is dropped, so
        the history never starts with an orphan tool result.
        """
        rows = self.messages(session_id, limit=limit)
        start = next((i for i, r in enumerate(rows) if r["role"] == "user"), len(rows))
        out: list[dict[str, Any]] = []
        for row in rows[start:]:
            msg: dict[str, Any] = {"role": row["role"], "content": row["content"]}
            if row["tool_calls"]:
                msg["tool_calls"] = row["tool_calls"]
            if row["tool_call_id"]:
                msg["tool_call_id"] = row["tool_call_id"]
            out.append(msg)
        return out

    def search(
        self, query: str, exclude_session: str | None = None, limit: int = 5, only_prefix: str | None = None
    ) -> list[dict[str, Any]]:
        """Full-text search over user and assistant messages.

        Query terms (unicode ``\\w+``) are quoted and joined with OR; results are
        ranked by relevance and carry a snippet with matches wrapped in ``**``.
        ``only_prefix`` restricts the search to sessions whose id starts with it.
        """
        terms = re.findall(r"\w+", query)
        if not terms:
            return []
        match = " OR ".join(f'"{t}"' for t in dict.fromkeys(terms))
        return self._query(
            "SELECT m.id, m.session_id, m.role, m.ts, s.title, s.source, "
            "snippet(messages_fts, 0, '**', '**', '…', 16) AS snippet "
            "FROM messages_fts JOIN messages m ON m.id = messages_fts.rowid "
            "JOIN sessions s ON s.id = m.session_id "
            "WHERE messages_fts MATCH ? AND m.role IN ('user', 'assistant') AND m.session_id != ? "
            "AND m.session_id LIKE ? ESCAPE '\\' ORDER BY rank LIMIT ?",
            (match, exclude_session or "", _like_prefix(only_prefix), limit),
        )

    def stats(self) -> dict[str, Any]:
        """Session and message counts for the dashboard."""
        row = self._one(
            "SELECT (SELECT COUNT(*) FROM sessions) AS sessions, (SELECT COUNT(*) FROM messages) AS messages, "
            "(SELECT COUNT(DISTINCT source) FROM sessions) AS sources"
        )
        return row or {}

    # Key-value

    def kv_get(self, key: str) -> str | None:
        """Return a value or ``None``."""
        row = self._one("SELECT value FROM kv WHERE key = ?", (key,))
        return row["value"] if row else None

    def kv_set(self, key: str, value: str) -> None:
        """Insert or replace a value."""
        with self._tx() as c:
            c.execute(
                "INSERT INTO kv (key, value, updated) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated = excluded.updated",
                (key, value, time.time()),
            )

    def kv_delete(self, key: str) -> bool:
        """Delete a key; returns whether it existed."""
        with self._tx() as c:
            return c.execute("DELETE FROM kv WHERE key = ?", (key,)).rowcount > 0

    # Journal

    def journal_add(
        self,
        kind: str,
        action: str,
        summary: str,
        session_id: str | None = None,
        status: str = "applied",
        data: dict[str, Any] | None = None,
    ) -> int:
        """Record an entry; ``data`` holds what is needed to undo it."""
        with self._tx() as c:
            cur = c.execute(
                "INSERT INTO journal (ts, kind, action, summary, session_id, status, data) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (time.time(), kind, action, summary, session_id, status, json.dumps(data) if data else None),
            )
            return int(cur.lastrowid or 0)

    def journal_list(self, limit: int = 100, kind: str | None = None) -> list[dict[str, Any]]:
        """Return entries, newest first."""
        where, params = ("WHERE kind = ?", (kind,)) if kind else ("", ())
        rows = self._query(f"SELECT * FROM journal {where} ORDER BY id DESC LIMIT ?", (*params, limit))
        for row in rows:
            row["data"] = json.loads(row["data"]) if row["data"] else None
        return rows

    def journal_set_status(self, entry_id: int, status: str) -> bool:
        """Update an entry status; returns whether it exists."""
        with self._tx() as c:
            return c.execute("UPDATE journal SET status = ? WHERE id = ?", (status, entry_id)).rowcount > 0

    # Pending writes

    def pending_add(self, kind: str, payload: dict[str, Any], session_id: str | None = None) -> int:
        """Queue a write for approval."""
        with self._tx() as c:
            cur = c.execute(
                "INSERT INTO pending_writes (ts, kind, payload, session_id) VALUES (?, ?, ?, ?)",
                (time.time(), kind, json.dumps(payload), session_id),
            )
            return int(cur.lastrowid or 0)

    def pending_list(self, status: str = "pending", limit: int = 100) -> list[dict[str, Any]]:
        """Return queued writes, oldest first."""
        rows = self._query(
            "SELECT * FROM pending_writes WHERE status = ? ORDER BY id LIMIT ?", (status, limit)
        )
        for row in rows:
            row["payload"] = json.loads(row["payload"])
        return rows

    def pending_get(self, write_id: int) -> dict[str, Any] | None:
        """Return one queued write."""
        row = self._one("SELECT * FROM pending_writes WHERE id = ?", (write_id,))
        if row:
            row["payload"] = json.loads(row["payload"])
        return row

    def pending_decide(self, write_id: int, status: str) -> bool:
        """Approve or reject a pending write; returns false if it was not pending."""
        if status not in ("approved", "rejected"):
            raise ValueError(f"invalid status {status!r}")
        with self._tx() as c:
            return (
                c.execute(
                    "UPDATE pending_writes SET status = ?, decided = ? WHERE id = ? AND status = 'pending'",
                    (status, time.time(), write_id),
                ).rowcount
                > 0
            )

    # Settings

    def settings_get_all(self) -> dict[str, Any]:
        """Return stored settings with JSON-decoded values."""
        out: dict[str, Any] = {}
        for row in self._query("SELECT key, value FROM settings"):
            try:
                out[row["key"]] = json.loads(row["value"])
            except json.JSONDecodeError:
                logger.warning("Stored setting %s is not valid JSON; ignoring it", row["key"])
        return out

    def settings_set_many(self, values: dict[str, str]) -> None:
        """Store JSON-encoded values in one transaction."""
        now = time.time()
        with self._tx() as c:
            c.executemany(
                "INSERT INTO settings (key, value, updated) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated = excluded.updated",
                [(k, v, now) for k, v in values.items()],
            )

    # Admin and login sessions

    def admin_get(self) -> dict[str, Any] | None:
        """Return the admin row."""
        return self._one("SELECT * FROM admin WHERE id = 1")

    def admin_create(self, username: str, password_hash: str) -> bool:
        """Create the admin; returns false if one already exists."""
        now = time.time()
        with self._tx() as c:
            cur = c.execute(
                "INSERT OR IGNORE INTO admin (id, username, password_hash, created, updated) VALUES (1, ?, ?, ?, ?)",
                (username, password_hash, now, now),
            )
            return cur.rowcount > 0

    def admin_set_password(self, password_hash: str) -> bool:
        """Replace the admin password hash; returns false if there is no admin."""
        with self._tx() as c:
            return (
                c.execute(
                    "UPDATE admin SET password_hash = ?, updated = ? WHERE id = 1", (password_hash, time.time())
                ).rowcount
                > 0
            )

    def auth_session_create(
        self, token_hash: str, csrf_token: str, expires: float, ip: str | None, user_agent: str | None
    ) -> None:
        """Store a login session."""
        now = time.time()
        with self._tx() as c:
            c.execute(
                "INSERT INTO auth_sessions (token_hash, csrf_token, created, last_seen, expires, ip, user_agent) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (token_hash, csrf_token, now, now, expires, ip, user_agent),
            )

    def auth_session_get(self, token_hash: str) -> dict[str, Any] | None:
        """Return a login session row."""
        return self._one("SELECT * FROM auth_sessions WHERE token_hash = ?", (token_hash,))

    def auth_session_touch(self, token_hash: str, last_seen: float, expires: float) -> None:
        """Slide a session's expiry."""
        with self._tx() as c:
            c.execute(
                "UPDATE auth_sessions SET last_seen = ?, expires = ? WHERE token_hash = ?",
                (last_seen, expires, token_hash),
            )

    def auth_session_list(self, now: float) -> list[dict[str, Any]]:
        """Return unexpired sessions, most recent first."""
        return self._query(
            "SELECT token_hash, created, last_seen, expires, ip, user_agent FROM auth_sessions "
            "WHERE expires > ? ORDER BY last_seen DESC",
            (now,),
        )

    def auth_session_delete(self, token_hash: str) -> bool:
        """Invalidate one session."""
        with self._tx() as c:
            return c.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (token_hash,)).rowcount > 0

    def auth_session_delete_all(self, except_hash: str | None = None) -> int:
        """Invalidate every session, optionally keeping one; returns the count removed."""
        with self._tx() as c:
            return c.execute(
                "DELETE FROM auth_sessions WHERE token_hash != ?", (except_hash or "",)
            ).rowcount

    def auth_session_purge(self, now: float) -> int:
        """Remove expired sessions."""
        with self._tx() as c:
            return c.execute("DELETE FROM auth_sessions WHERE expires <= ?", (now,)).rowcount

    # API keys

    def api_key_create(self, name: str, key_hash: str, scope: str, expires: float | None) -> int:
        """Store a hashed API key; returns its id."""
        with self._tx() as c:
            cur = c.execute(
                "INSERT INTO api_keys (name, key_hash, scope, created, expires) VALUES (?, ?, ?, ?, ?)",
                (name, key_hash, scope, time.time(), expires),
            )
            return int(cur.lastrowid or 0)

    def api_key_by_hash(self, key_hash: str) -> dict[str, Any] | None:
        """Return an API key row by hash."""
        return self._one("SELECT * FROM api_keys WHERE key_hash = ?", (key_hash,))

    def api_key_list(self) -> list[dict[str, Any]]:
        """Return API keys without their hashes."""
        return self._query(
            "SELECT id, name, scope, created, expires, last_used, revoked FROM api_keys ORDER BY id"
        )

    def api_key_revoke(self, key_id: int) -> bool:
        """Revoke a key; returns false if unknown or already revoked."""
        with self._tx() as c:
            return (
                c.execute(
                    "UPDATE api_keys SET revoked = ? WHERE id = ? AND revoked IS NULL", (time.time(), key_id)
                ).rowcount
                > 0
            )

    def api_key_touch(self, key_id: int, ts: float) -> None:
        """Record the last use of a key."""
        with self._tx() as c:
            c.execute("UPDATE api_keys SET last_used = ? WHERE id = ?", (ts, key_id))

    # Secrets (encrypted by aifred.vault)

    def secret_set(self, name: str, blob: bytes) -> None:
        """Insert or replace an encrypted value."""
        with self._tx() as c:
            c.execute(
                "INSERT INTO secrets (name, value, updated) VALUES (?, ?, ?) "
                "ON CONFLICT(name) DO UPDATE SET value = excluded.value, updated = excluded.updated",
                (name, blob, time.time()),
            )

    def secret_get(self, name: str) -> bytes | None:
        """Return an encrypted value."""
        row = self._one("SELECT value FROM secrets WHERE name = ?", (name,))
        return bytes(row["value"]) if row else None

    def secret_names(self) -> list[str]:
        """Names of all stored secrets."""
        return [r["name"] for r in self._query("SELECT name FROM secrets ORDER BY name")]

    def secret_delete(self, name: str) -> bool:
        """Delete a value; returns whether it existed."""
        with self._tx() as c:
            return c.execute("DELETE FROM secrets WHERE name = ?", (name,)).rowcount > 0
