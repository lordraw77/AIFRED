"""Tests for the knowledge-base queries in aifred.store_rag."""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest

from aifred.store import Store


def chunk(i: int, text: str, heading: str = "", embedding: bytes | None = None, sha: str | None = None) -> dict[str, Any]:
    return {"ord": i, "heading": heading, "text": text, "text_sha": sha or f"sha-{text}", "embedding": embedding}


@pytest.fixture
def col(store: Store) -> int:
    return store.rag_collection_create("homelab", "notes", None, None)


def test_schema_has_the_knowledge_base(store: Store) -> None:
    names = {r["name"] for r in store._query("SELECT name FROM sqlite_master WHERE type IN ('table', 'trigger')")}
    assert {"rag_collections", "rag_documents", "rag_chunks", "rag_chunks_fts", "rag_chunks_ai", "rag_chunks_ad", "rag_chunks_au"} <= names
    cols = {r["name"] for r in store._query("PRAGMA table_info(models)")}
    assert {"kind", "dim"} <= cols


def test_collection_queries(store: Store, col: int) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        store.rag_collection_create("homelab", "", None, None)
    assert store.rag_collection_get("homelab")["description"] == "notes"  # type: ignore[index]
    assert store.rag_collection_by_id(col)["name"] == "homelab"  # type: ignore[index]
    assert store.rag_collection_update("homelab", {"description": "new", "embed_provider": "p", "embed_model": "m", "name": "ignored"})
    row = store.rag_collection_get("homelab")
    assert row is not None and (row["description"], row["embed_provider"], row["embed_model"], row["name"]) == ("new", "p", "m", "homelab")
    assert not store.rag_collection_update("ghost", {"description": "x"}) and store.rag_collection_update("homelab", {"junk": 1})


def test_upsert_semantics(store: Store, col: int) -> None:
    doc, queued = store.rag_doc_upsert(col, "a.md", "upload", "text", "a", "sha1", 10)
    assert queued
    assert store.rag_doc_upsert(col, "a.md", "upload", "text", "a", "sha1", 10) == (doc, False)  # same content: nothing to do
    assert store.rag_doc_upsert(col, "a.md", "upload", "text", "a", "sha1", 10, force=True) == (doc, True)
    store.rag_doc_set(doc, status="ready", chunks=3)
    assert store.rag_doc_upsert(col, "a.md", "upload", "text", "a", "sha2", 12) == (doc, True)  # changed: queued again
    row = store.rag_doc_get(doc)
    assert row is not None and row["status"] == "queued" and row["sha256"] == "sha2" and row["chunks"] == 3 and row["collection"] == "homelab"
    store.rag_doc_set(doc, status="error", error="boom")
    assert store.rag_doc_upsert(col, "a.md", "upload", "text", "a", "sha2", 12) == (doc, False)
    assert store.rag_doc_upsert(col, "a.md", "upload", "text", "a", "sha2", 12, retry_errors=True) == (doc, True)
    assert store.rag_doc_get(doc)["error"] == ""  # type: ignore[index]
    assert store.rag_doc_find(col, "a.md")["id"] == doc and store.rag_doc_find(col, "z.md") is None  # type: ignore[index]


def test_job_queue(store: Store, col: int) -> None:
    a, _ = store.rag_doc_upsert(col, "a.md", "file", "text", "a", "1", 1)
    b, _ = store.rag_doc_upsert(col, "b.md", "file", "text", "b", "2", 1)
    store.rag_doc_set(b, status="waiting", retry_at=500.0)
    job = store.rag_next_job(100.0)
    assert job is not None and job["id"] == a and job["status"] == "indexing"
    assert store.rag_next_job(100.0) is None  # b is waiting until 500
    assert store.rag_next_job(600.0)["id"] == b  # type: ignore[index]
    assert store.rag_next_job(600.0) is None
    assert store.rag_recover_indexing() == 2
    assert {d["status"] for d in store.rag_doc_list()} == {"queued"}
    assert store.rag_requeue_collection(col) == 2
    store.rag_doc_set(a, status="indexing")
    assert store.rag_requeue_collection(col) == 1  # a document being indexed is left alone


def test_swap_replaces_chunks_atomically_and_keeps_the_index_in_step(store: Store, col: int) -> None:
    doc, _ = store.rag_doc_upsert(col, "a.md", "file", "text", "a", "1", 1)
    gen = store.rag_generation(col)
    store.rag_chunks_swap(doc, [chunk(0, "alpha zebra"), chunk(1, "beta giraffe", "Sec")], {"status": "ready", "chunks": 2, "title": "A", "flagged": 1})
    assert store.rag_generation(col) == gen + 1 and store.rag_chunk_total() == 2
    row = store.rag_doc_get(doc)
    assert row is not None and (row["status"], row["chunks"], row["title"], row["flagged"]) == ("ready", 2, "A", 1)
    assert [r["id"] for r in store.rag_fts('"zebra"', None, 5)] and store.rag_fts('"zebra" OR "Sec"', [col], 5)
    store.rag_chunks_swap(doc, [chunk(0, "gamma lion")], {"status": "ready", "chunks": 1})
    assert store.rag_fts('"zebra"', None, 5) == []  # the old text left the full-text index with its chunk
    assert store.rag_fts('"lion"', None, 5) and store.rag_chunk_total() == 1
    with pytest.raises(KeyError):
        store.rag_chunks_swap(9999, [chunk(0, "x")], {})
    assert store.rag_chunk_total() == 1  # a failed swap changes nothing


def test_fts_filters_and_headings(store: Store) -> None:
    c1, c2 = store.rag_collection_create("one", "", None, None), store.rag_collection_create("two", "", None, None)
    d1, _ = store.rag_doc_upsert(c1, "a.md", "file", "text", "a", "1", 1)
    d2, _ = store.rag_doc_upsert(c2, "b.md", "file", "text", "b", "2", 1)
    store.rag_chunks_swap(d1, [chunk(0, "shared word", "Proxmox › Backup")], {"status": "ready"})
    store.rag_chunks_swap(d2, [chunk(0, "shared other")], {"status": "ready"})
    assert len(store.rag_fts('"shared"', None, 10)) == 2 and len(store.rag_fts('"shared"', [c2], 10)) == 1
    assert store.rag_fts('"shared"', [], 10) == []
    assert len(store.rag_fts('"backup"', None, 10)) == 1  # headings are indexed too
    assert len(store.rag_fts('"caffè" OR "shared"', None, 1)) == 1  # LIMIT
    info = store.rag_chunks_info([r["id"] for r in store.rag_fts('"shared"', None, 10)])
    assert {i["collection"] for i in info.values()} == {"one", "two"} and store.rag_chunks_info([]) == {}


def test_vector_queries(store: Store, col: int) -> None:
    doc, _ = store.rag_doc_upsert(col, "a.md", "file", "text", "a", "1", 1)
    store.rag_chunks_swap(
        doc, [chunk(i, f"text {i}", embedding=bytes([i]) * 8 if i % 5 else None) for i in range(12)],
        {"status": "ready", "embed_tag": "p|m", "dim": 2},
    )
    assert store.rag_vector_count(col, "p|m") == 9 and store.rag_vector_count(col, "other") == 0 and store.rag_vector_count(col, None) == 0
    blocks = list(store.rag_vector_blocks(col, "p|m", block=4))
    assert [len(ids) for ids, _ in blocks] == [4, 4, 1]
    flat = [i for ids, _ in blocks for i in ids]
    assert flat == sorted(flat) and len(set(flat)) == 9
    assert list(store.rag_vector_blocks(col, "other")) == []
    assert store.rag_stale_count(col, "p|m") == 0 and store.rag_stale_count(col, "p|other") == 1 and store.rag_stale_count(col, None) == 1
    store.rag_doc_set(doc, status="error")
    assert store.rag_stale_count(col, "p|other") == 0  # only indexed documents can be stale
    assert set(store.rag_chunk_embeddings(doc)) == {f"sha-text {i}" for i in range(12) if i % 5}


def test_deletes_cascade_and_bump_the_generation(store: Store, col: int) -> None:
    d1, _ = store.rag_doc_upsert(col, "a.md", "file", "text", "a", "1", 1)
    d2, _ = store.rag_doc_upsert(col, "b.md", "upload", "text", "b", "2", 1)
    for d in (d1, d2):
        store.rag_chunks_swap(d, [chunk(0, f"text of {d}")], {"status": "ready"})
    gen = store.rag_generation(col)
    assert store.rag_doc_remove_missing(col, set()) == 1  # only the folder document goes; the upload stays
    assert store.rag_generation(col) > gen and store.rag_doc_get(d2) is not None and store.rag_doc_get(d1) is None
    assert store.rag_stored_hashes() == {"2"}
    assert not store.rag_doc_delete(d1) and store.rag_doc_delete(d2)
    assert store.rag_chunk_total() == 0 and store.rag_generation(999) == -1
    d3, _ = store.rag_doc_upsert(col, "c.md", "file", "text", "c", "3", 1)
    store.rag_chunks_swap(d3, [chunk(0, "kept until the collection goes")], {"status": "ready"})
    assert store.rag_collection_delete("homelab") and store.rag_chunk_total() == 0 and store.rag_doc_list() == []
    assert store._query("SELECT COUNT(*) AS n FROM rag_chunks_fts WHERE rag_chunks_fts MATCH 'collection'")[0]["n"] == 0


def test_status_and_collection_list_counts(store: Store, col: int) -> None:
    d1, _ = store.rag_doc_upsert(col, "a.md", "file", "text", "a", "1", 1)
    d2, _ = store.rag_doc_upsert(col, "b.md", "file", "text", "b", "2", 1)
    store.rag_chunks_swap(d1, [chunk(0, "one"), chunk(1, "two")], {"status": "ready", "chunks": 2})
    store.rag_doc_set(d2, status="error", error="x")
    [row] = store.rag_collection_list()
    assert (row["documents"], row["chunk_count"], row["pending"], row["errors"]) == (2, 2, 0, 1)
    assert store.rag_status() == {"collections": 1, "chunks": 2, "documents": {"ready": 1, "error": 1}}
    assert len(store.rag_doc_list(col, limit=1)) == 1 and store.rag_doc_list(col + 1) == []
