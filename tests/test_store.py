"""Tests for aifred.store."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from aifred.store import _MIGRATIONS, Store


def _chat(store: Store, session: str, *texts: str) -> None:
    store.ensure_session(session, "cli")
    for i, text in enumerate(texts):
        store.add_message(session, "user" if i % 2 == 0 else "assistant", text)


def test_schema_is_migrated_once(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite"
    Store(path).close()
    reopened = Store(path)
    assert reopened.schema_version == len(_MIGRATIONS)
    reopened.close()


def test_newer_schema_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite"
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA user_version=99")
    conn.close()
    with pytest.raises(RuntimeError, match="newer"):
        Store(path)


def test_wal_mode(store: Store) -> None:
    assert store._one("PRAGMA journal_mode") == {"journal_mode": "wal"}


def test_message_requires_existing_session(store: Store) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        store.add_message("missing", "user", "hello")


def test_invalid_role(store: Store) -> None:
    store.ensure_session("s", "cli")
    with pytest.raises(ValueError):
        store.add_message("s", "admin", "x")


def test_history_drops_leading_orphans(store: Store) -> None:
    store.ensure_session("s", "cli")
    store.add_message("s", "user", "first question")
    calls = [{"id": "c1", "type": "function", "function": {"name": "memory", "arguments": "{}"}}]
    store.add_message("s", "assistant", None, tool_calls=calls)
    store.add_message("s", "tool", "result", tool_call_id="c1")
    store.add_message("s", "assistant", "answer")
    store.add_message("s", "user", "second question")

    window = store.history("s", limit=3)  # starts at the tool result: orphan
    assert [m["role"] for m in window] == ["user"]
    assert window[0]["content"] == "second question"

    full = store.history("s", limit=10)
    assert [m["role"] for m in full] == ["user", "assistant", "tool", "assistant", "user"]
    assert full[1]["tool_calls"] == calls
    assert full[2]["tool_call_id"] == "c1"
    assert "tool_calls" not in full[0]


def test_history_without_user_message_is_empty(store: Store) -> None:
    store.ensure_session("s", "cli")
    store.add_message("s", "tool", "orphan", tool_call_id="x")
    assert store.history("s", limit=10) == []


def test_search_accented_text_and_exclusion(store: Store) -> None:
    _chat(store, "old", "Configurazione della città di Bologna", "Ecco la risposta su Bologna")
    _chat(store, "current", "parliamo di città")
    hits = store.search("citta", exclude_session="current")
    assert [h["session_id"] for h in hits] == ["old"]
    assert "**città**" in hits[0]["snippet"]
    assert {h["session_id"] for h in store.search("città")} == {"old", "current"}


def test_search_ignores_tool_messages_and_punctuation(store: Store) -> None:
    store.ensure_session("s", "cli")
    store.add_message("s", "user", "hello")
    store.add_message("s", "tool", "secret zebra", tool_call_id="t")
    assert store.search("zebra") == []
    assert store.search('"; DROP TABLE messages; --') == []
    assert store.search("") == []


def test_search_or_semantics_and_limit(store: Store) -> None:
    for i in range(5):
        _chat(store, f"s{i}", f"alpha topic {i}")
    _chat(store, "b", "beta only")
    assert len(store.search("alpha beta", limit=10)) == 6
    assert len(store.search("alpha", limit=2)) == 2


def test_delete_session_cascades_to_fts(store: Store) -> None:
    _chat(store, "s", "unique walrus")
    assert store.search("walrus")
    assert store.delete_session("s")
    assert store.search("walrus") == []
    assert store.messages("s") == []
    assert not store.delete_session("s")


def test_session_listing_and_rename(store: Store) -> None:
    _chat(store, "a", "one", "two")
    store.ensure_session("b", "telegram", "Title B")
    assert store.rename_session("a", "Renamed")
    rows = store.list_sessions()
    assert {r["id"] for r in rows} == {"a", "b"}
    row = store.get_session("a")
    assert row is not None and row["title"] == "Renamed" and row["message_count"] == 2
    assert [r["id"] for r in store.list_sessions(source="telegram")] == ["b"]
    assert store.stats() == {"sessions": 2, "messages": 2, "sources": 2}


def test_kv(store: Store) -> None:
    assert store.kv_get("k") is None
    store.kv_set("k", "1")
    store.kv_set("k", "2")
    assert store.kv_get("k") == "2"
    assert store.kv_delete("k")
    assert store.kv_get("k") is None


def test_journal(store: Store) -> None:
    first = store.journal_add("memory", "add", "likes tea", session_id="s", data={"entry": "tea"})
    store.journal_add("settings", "update", "X: 1 → 2")
    assert [e["kind"] for e in store.journal_list()] == ["settings", "memory"]
    assert store.journal_list(kind="memory")[0]["data"] == {"entry": "tea"}
    assert store.journal_set_status(first, "undone")
    assert store.journal_list(kind="memory")[0]["status"] == "undone"


def test_pending_writes(store: Store) -> None:
    wid = store.pending_add("memory", {"content": "x"}, "s")
    assert store.pending_list()[0]["payload"] == {"content": "x"}
    assert store.pending_decide(wid, "approved")
    assert not store.pending_decide(wid, "rejected")
    assert store.pending_get(wid)["status"] == "approved"  # type: ignore[index]
    assert store.pending_list() == []
    with pytest.raises(ValueError):
        store.pending_decide(wid, "maybe")


def test_data_survives_reopen(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite"
    s = Store(path)
    _chat(s, "s", "persistent penguin")
    s.kv_set("k", "v")
    s.close()
    s = Store(path)
    assert s.search("penguin")
    assert s.kv_get("k") == "v"
    s.close()
