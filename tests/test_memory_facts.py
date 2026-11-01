"""Tests for long-term memory: the SQLite backend, subject isolation, retention and cursors."""

from __future__ import annotations

import sqlite3
import time

import numpy as np
import pytest

from aifred.config import SETTING_SPECS
from aifred.memory_backend import MemoryFactError, check_subject, visible_subjects
from aifred.memory_sqlite import MAX_TEXT, SqliteMemory
from aifred.store import Store


@pytest.fixture
def mem(store: Store) -> SqliteMemory:
    return SqliteMemory(store)


def _vec(*values: float) -> np.ndarray:
    return np.array(values, dtype=np.float32)


def test_subjects_are_validated() -> None:
    for ok in ("owner", "user:123", "chat:-100123", "chat:tg:42", "agent:researcher", "user:a@b.it"):
        assert check_subject(ok) == ok
    for bad in ("", "Owner", "user:", "user:a b", "group:1", "owner:1", "user:" + "x" * 129, "agent:../x"):
        with pytest.raises(MemoryFactError):
            check_subject(bad)


def test_visible_subjects_never_mix_owner_and_others() -> None:
    assert visible_subjects(restricted=False) == ("owner",)
    assert visible_subjects(restricted=False, user_id="9", chat_id="1") == ("owner",)
    assert visible_subjects(restricted=True, user_id="9", chat_id="1") == ("user:9", "chat:1")
    assert visible_subjects(restricted=True, chat_id="1") == ("chat:1",)
    assert visible_subjects(restricted=True) == ()
    assert visible_subjects(restricted=False, agent="researcher") == ("agent:researcher",)
    assert visible_subjects(restricted=True, user_id="9", agent="researcher") == ("agent:researcher",)


def test_a_fact_is_stored_and_read_back(mem: SqliteMemory) -> None:
    fact = mem.add("owner", "  Marco   prefers short answers ", kind="preference", importance=2, entities=["Marco", "marco", " Acme "])
    assert fact.text == "Marco prefers short answers" and fact.importance == 1.0
    assert fact.entities == ["acme", "marco"] and fact.status == "active" and fact.use_count == 0
    assert mem.get(fact.id, ["owner"]) == fact


def test_facts_are_refused_when_malformed(mem: SqliteMemory) -> None:
    with pytest.raises(MemoryFactError):
        mem.add("owner", "   ")
    with pytest.raises(MemoryFactError):
        mem.add("owner", "x" * (MAX_TEXT + 1))
    with pytest.raises(MemoryFactError):
        mem.add("owner", "ok", kind="rumour")
    with pytest.raises(MemoryFactError):
        mem.add("someone", "ok")


def test_nothing_is_visible_without_subjects(mem: SqliteMemory) -> None:
    fact = mem.add("owner", "the vault is in the cellar")
    assert mem.get(fact.id, []) is None
    assert mem.list_facts([]) == [] and mem.search([], "vault") == [] and mem.neighbors([], ["vault"]) == []
    assert mem.update(fact.id, [], text="changed") is False
    assert mem.delete(fact.id, []) is False
    assert mem.set_status(fact.id, [], "archived") is False
    assert mem.stats([]) == {"total": 0, "by_status": {}, "subjects": []}


def test_subjects_cannot_read_or_change_each_other(mem: SqliteMemory) -> None:
    owner = mem.add("owner", "the owner keeps the alarm code secret", entities=["alarm"], embedding=_vec(1, 0), emb_model="m")
    alice = mem.add("user:alice", "alice likes green tea", entities=["tea"], embedding=_vec(0, 1), emb_model="m")
    bob = mem.add("user:bob", "bob likes green tea too", entities=["tea"])
    room = mem.add("chat:g1", "this group meets on friday", entities=["friday"])
    agent = mem.add("agent:scout", "scout checks the feeds", entities=["feeds"])

    alice_sees = ("user:alice", "chat:g1")
    assert {f.id for f in mem.list_facts(alice_sees)} == {alice.id, room.id}
    assert [f.id for f in mem.search(alice_sees, "green tea")] == [alice.id]
    assert mem.search(alice_sees, "alarm code") == []
    assert mem.search(alice_sees, "nothing in common", vector=_vec(1, 0), emb_model="m", min_similarity=0.5) == []  # the owner's vector is out of reach
    assert {f.id for f in mem.neighbors(alice_sees, ["tea", "friday"])} == {alice.id, room.id}
    for hidden in (owner, bob, agent):
        assert mem.get(hidden.id, alice_sees) is None
        assert mem.update(hidden.id, alice_sees, text="tampered") is False
        assert mem.set_status(hidden.id, alice_sees, "archived") is False
        assert mem.delete(hidden.id, alice_sees) is False
        assert mem.get(hidden.id, [hidden.subject]) is not None
    assert mem.get(owner.id, ["owner"]).text == "the owner keeps the alarm code secret"  # type: ignore[union-attr]
    assert mem.stats(["user:bob"])["total"] == 1


def test_forgetting_a_subject_leaves_the_others(mem: SqliteMemory, store: Store) -> None:
    mem.add("user:alice", "alice one", entities=["alice"])
    mem.add("user:alice", "alice two")
    keep = mem.add("user:bob", "bob one", entities=["bob"])
    store.mem_episode_set("user:alice", "s1", "talked about tea", 4)
    store.mem_episode_set("user:bob", "s2", "talked about cake", 6)
    assert mem.forget_subject("user:alice") == 2
    assert mem.list_facts(["user:alice"], status=None) == []
    assert store.mem_episode_list(["user:alice"]) == [] and len(store.mem_episode_list(["user:bob"])) == 1
    assert [f.id for f in mem.list_facts(["user:bob"])] == [keep.id]
    assert store._query("SELECT COUNT(*) AS n FROM mem_links")[0]["n"] == 1  # the cascade took alice's links


def test_search_finds_words_and_accents(mem: SqliteMemory) -> None:
    fact = mem.add("owner", "Preferisce il caffè senza zucchero")
    mem.add("owner", "Lavora a Bologna")
    assert [f.id for f in mem.search(["owner"], "caffe zucchero")] == [fact.id]
    assert mem.search(["owner"], "") == [] and mem.search(["owner"], "?!") == []


def test_vector_search_fuses_with_text_and_respects_the_model(mem: SqliteMemory) -> None:
    near = mem.add("owner", "completely unrelated words", embedding=_vec(1, 0.1), emb_model="m1")
    far = mem.add("owner", "other things", embedding=_vec(0, 1), emb_model="m1")
    other_model = mem.add("owner", "different embedding", embedding=_vec(1, 0), emb_model="m2")
    text = mem.add("owner", "needle in the text")

    hits = mem.search(["owner"], "needle", vector=_vec(1, 0), emb_model="m1", limit=5, min_similarity=0.5)
    ids = [f.id for f in hits]
    assert near.id in ids and text.id in ids and far.id not in ids and other_model.id not in ids
    by_id = {f.id: f for f in hits}
    assert by_id[near.id].similarity > 0.99 and by_id[text.id].similarity == 0.0 and by_id[near.id].score > 0
    assert [f.id for f in mem.search(["owner"], "", vector=_vec(1, 0), emb_model="m1", min_similarity=0.5)] == [near.id]
    assert mem.search(["owner"], "", vector=_vec(1, 0, 0), emb_model="m1") == []  # another dimension is skipped, not an error


def test_updating_text_drops_a_stale_vector(mem: SqliteMemory, store: Store) -> None:
    fact = mem.add("owner", "lives in Rome", embedding=_vec(1, 0), emb_model="m")
    assert mem.update(fact.id, ["owner"], importance=0.9, entities=["Rome"])
    assert store._one("SELECT emb_model FROM mem_facts WHERE id = ?", (fact.id,))["emb_model"] == "m"
    assert mem.update(fact.id, ["owner"], text="lives in Milan")
    assert store._one("SELECT emb_model, embedding FROM mem_facts WHERE id = ?", (fact.id,)) == {"emb_model": None, "embedding": None}
    assert [f.id for f in mem.search(["owner"], "Milan")] == [fact.id] and mem.search(["owner"], "Rome") == []
    changed = mem.get(fact.id, ["owner"])
    assert changed is not None and changed.importance == 0.9 and changed.entities == ["rome"]


def test_status_changes_hide_and_restore(mem: SqliteMemory) -> None:
    old = mem.add("owner", "works at Acme")
    new = mem.add("owner", "works at Globex")
    assert mem.set_status(old.id, ["owner"], "superseded", superseded_by=new.id)
    assert [f.id for f in mem.list_facts(["owner"])] == [new.id]
    assert [f.id for f in mem.list_facts(["owner"], status="superseded")] == [old.id]
    assert mem.search(["owner"], "Acme") == []
    assert mem.set_status(old.id, ["owner"], "active")
    restored = mem.get(old.id, ["owner"])
    assert restored is not None and restored.status == "active"
    with pytest.raises(MemoryFactError):
        mem.set_status(old.id, ["owner"], "gone")  # type: ignore[arg-type]


def test_retention_archives_unused_facts_then_purges(mem: SqliteMemory, store: Store) -> None:
    now = time.time()
    day = 86400

    def age(fact_id: int, updated_days: float, used_days: float | None) -> None:
        store._conn.execute(
            "UPDATE mem_facts SET updated = ?, last_used = ? WHERE id = ?",
            (now - updated_days * day, None if used_days is None else now - used_days * day, fact_id),
        )

    stale = mem.add("owner", "never recalled, old")
    used = mem.add("owner", "old but recalled lately")
    fresh = mem.add("owner", "new")
    age(stale.id, 200, None)
    age(used.id, 200, 10)
    assert mem.archive_stale(180) == 1
    assert [f.id for f in mem.list_facts(["owner"], status="archived")] == [stale.id]
    assert {f.id for f in mem.list_facts(["owner"])} == {used.id, fresh.id}

    assert mem.purge_archived(30) == 0  # archived just now, still inside the grace period
    store._conn.execute("UPDATE mem_facts SET archived = ? WHERE id = ?", (now - 31 * day, stale.id))
    assert mem.purge_archived(30) == 1
    assert mem.get(stale.id, ["owner"]) is None
    assert mem.search(["owner"], "recalled") != []  # the live facts survive the purge and the index stays consistent


def test_touch_counts_recalls(mem: SqliteMemory) -> None:
    fact = mem.add("owner", "remember me")
    mem.touch([fact.id, fact.id])
    mem.touch([])
    seen = mem.get(fact.id, ["owner"])
    assert seen is not None and seen.use_count == 1 and seen.last_used is not None


def test_stats_and_listing_filters(mem: SqliteMemory) -> None:
    mem.add("owner", "a", kind="preference")
    gone = mem.add("owner", "b", kind="decision", source="manual")
    mem.add("user:alice", "c")
    mem.set_status(gone.id, ["owner"], "archived")
    assert mem.stats() == {"total": 3, "by_status": {"active": 2, "archived": 1}, "subjects": ["owner", "user:alice"]}
    assert mem.stats(["owner"])["total"] == 2
    assert [f.text for f in mem.list_facts(["owner"], status=None, kind="decision")] == ["b"]
    assert [f.text for f in mem.list_facts(["owner"], status=None, source="manual")] == ["b"]
    assert len(mem.list_facts(["owner", "user:alice"], status=None, limit=2)) == 2
    assert len(mem.list_facts(["owner", "user:alice"], status=None, limit=2, offset=2)) == 1


def test_episodes_replace_per_subject_and_session(store: Store) -> None:
    store.mem_episode_set("owner", "s1", "first summary", 5)
    store.mem_episode_set("owner", "s1", "better summary", 9)
    store.mem_episode_set("user:alice", "s1", "alice's own", 3)
    assert [(e["summary"], e["upto"]) for e in store.mem_episode_list(["owner"])] == [("better summary", 9)]
    assert len(store.mem_episode_list(["owner", "user:alice"])) == 2


def test_the_extraction_cursor_survives_and_is_per_session(store: Store) -> None:
    assert store.mem_cursor_get("s1") == {"last_message_id": 0, "turns": 0, "updated": 0.0}
    store.mem_cursor_set("s1", 42, 3)
    store.mem_cursor_set("s2", 7)
    store.mem_cursor_set("s1", 50, 0)
    assert store.mem_cursor_get("s1")["last_message_id"] == 50 and store.mem_cursor_get("s1")["turns"] == 0
    assert store.mem_cursor_get("s2")["last_message_id"] == 7


def test_unknown_fields_are_refused(store: Store) -> None:
    fact_id = store.mem_fact_add("owner", "x")
    with pytest.raises(ValueError, match="cannot update"):
        store.mem_fact_update(fact_id, ["owner"], subject="user:alice")


def test_migration_is_idempotent_on_reopen(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "db.sqlite"
    first = Store(path)
    first.mem_fact_add("owner", "kept")
    first.close()
    again = Store(path)
    assert again.mem_fact_list(["owner"])[0]["text"] == "kept"
    again.close()
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'mem_facts_fts'").fetchone()
    conn.close()


def test_memory_settings_have_the_agreed_defaults() -> None:
    d = {k: s.default for k, s in SETTING_SPECS.items() if k.startswith("MEMORY_") or k == "AUTO_MEMORY"}
    assert d["AUTO_MEMORY"] == "off"
    assert d["MEMORY_EXTRACT_ROUTE"] == "" and d["MEMORY_EXTRACT_FALLBACK"] is True
    assert d["MEMORY_RETENTION_DAYS"] == 180 and d["MEMORY_GRACE_DAYS"] == 30
    assert d["MEMORY_EVERY_TURNS"] == 12 and d["MEMORY_RECALL_TOKENS"] == 600
