"""Database access for the local knowledge base (spec 9.4).

Mixed into :class:`aifred.store.Store`, which supplies ``_tx``, ``_query`` and ``_one``.
The database doubles as the ingestion queue: a document is picked up by whichever process
runs the worker, and survives a restart.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from contextlib import AbstractContextManager
from typing import Any
import sqlite3

_DOC_FIELDS = (
    "kind", "title", "sha256", "size", "chunks", "status", "error", "flagged", "embed_tag", "dim", "attempts", "retry_at",
)


class RagStoreMixin:
    """Collections, documents and chunks."""

    def _tx(self) -> AbstractContextManager[sqlite3.Connection]:  # pragma: no cover - provided by Store
        raise NotImplementedError

    def _query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:  # pragma: no cover
        raise NotImplementedError

    def _one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:  # pragma: no cover
        raise NotImplementedError

    # Collections

    def rag_collection_create(self, name: str, description: str, embed_provider: str | None, embed_model: str | None) -> int:
        """Create a collection.

        Raises:
            sqlite3.IntegrityError: If the name is taken.
        """
        with self._tx() as c:
            cur = c.execute(
                "INSERT INTO rag_collections (name, description, embed_provider, embed_model, created) VALUES (?, ?, ?, ?, ?)",
                (name, description, embed_provider, embed_model, time.time()),
            )
            return int(cur.lastrowid or 0)

    def rag_collection_get(self, name: str) -> dict[str, Any] | None:
        """Return a collection by name."""
        return self._one("SELECT * FROM rag_collections WHERE name = ?", (name,))

    def rag_collection_by_id(self, collection_id: int) -> dict[str, Any] | None:
        """Return a collection by id."""
        return self._one("SELECT * FROM rag_collections WHERE id = ?", (collection_id,))

    def rag_collection_list(self) -> list[dict[str, Any]]:
        """Return collections with document and chunk counts."""
        return self._query(
            "SELECT c.*, "
            "(SELECT COUNT(*) FROM rag_documents d WHERE d.collection_id = c.id) AS documents, "
            "(SELECT COALESCE(SUM(d.chunks), 0) FROM rag_documents d WHERE d.collection_id = c.id) AS chunk_count, "
            "(SELECT COUNT(*) FROM rag_documents d WHERE d.collection_id = c.id AND d.status IN ('queued', 'indexing', 'waiting')) AS pending, "
            "(SELECT COUNT(*) FROM rag_documents d WHERE d.collection_id = c.id AND d.status = 'error') AS errors "
            "FROM rag_collections c ORDER BY c.name"
        )

    def rag_collection_update(self, name: str, fields: dict[str, Any]) -> bool:
        """Update ``description``, ``embed_provider`` or ``embed_model``; returns whether it exists."""
        allowed = {k: v for k, v in fields.items() if k in ("description", "embed_provider", "embed_model")}
        if not allowed:
            return self.rag_collection_get(name) is not None
        assignments = ", ".join(f"{k} = ?" for k in allowed)
        with self._tx() as c:
            return c.execute(f"UPDATE rag_collections SET {assignments} WHERE name = ?", (*allowed.values(), name)).rowcount > 0

    def rag_collection_delete(self, name: str) -> bool:
        """Delete a collection with its documents and chunks."""
        with self._tx() as c:
            return c.execute("DELETE FROM rag_collections WHERE name = ?", (name,)).rowcount > 0

    # Documents

    def rag_doc_upsert(
        self, collection_id: int, source: str, origin: str, kind: str, title: str, sha256: str, size: int,
        *, force: bool = False, retry_errors: bool = False,
    ) -> tuple[int, bool]:
        """Register a document for (re)indexing.

        Identical content already indexed (or on its way) is left alone, unless ``force`` is set or
        the previous attempt failed and ``retry_errors`` is set. Old chunks stay searchable until the
        new version replaces them.

        Returns:
            The document id and whether it was queued.
        """
        now = time.time()
        with self._tx() as c:
            row = c.execute(
                "SELECT id, sha256, status FROM rag_documents WHERE collection_id = ? AND source = ?", (collection_id, source)
            ).fetchone()
            if row is None:
                cur = c.execute(
                    "INSERT INTO rag_documents (collection_id, source, origin, kind, title, sha256, size, status, created, updated) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?)",
                    (collection_id, source, origin, kind, title, sha256, size, now, now),
                )
                return int(cur.lastrowid or 0), True
            doc_id, old_sha, status = row[0], row[1], row[2]
            unchanged = old_sha == sha256
            if unchanged and not force and not (status == "error" and retry_errors):
                return int(doc_id), False
            c.execute(
                "UPDATE rag_documents SET origin = ?, kind = ?, title = ?, sha256 = ?, size = ?, status = 'queued', "
                "error = '', attempts = 0, retry_at = NULL, updated = ? WHERE id = ?",
                (origin, kind, title, sha256, size, now, doc_id),
            )
            return int(doc_id), True

    def rag_doc_get(self, doc_id: int) -> dict[str, Any] | None:
        """Return a document with its collection name."""
        return self._one(
            "SELECT d.*, c.name AS collection FROM rag_documents d JOIN rag_collections c ON c.id = d.collection_id WHERE d.id = ?",
            (doc_id,),
        )

    def rag_doc_find(self, collection_id: int, source: str) -> dict[str, Any] | None:
        """Return a document by collection and source."""
        return self._one("SELECT * FROM rag_documents WHERE collection_id = ? AND source = ?", (collection_id, source))

    def rag_doc_list(self, collection_id: int | None = None, limit: int = 500, offset: int = 0) -> list[dict[str, Any]]:
        """Return documents, newest first."""
        where, params = ("WHERE d.collection_id = ?", (collection_id,)) if collection_id is not None else ("", ())
        return self._query(
            "SELECT d.*, c.name AS collection FROM rag_documents d JOIN rag_collections c ON c.id = d.collection_id "
            f"{where} ORDER BY d.updated DESC, d.id DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        )

    def rag_doc_set(self, doc_id: int, **fields: Any) -> bool:
        """Update document columns (status, error, attempts, retry_at, flagged, ...)."""
        allowed = {k: v for k, v in fields.items() if k in _DOC_FIELDS}
        if not allowed:
            return True
        assignments = ", ".join(f"{k} = ?" for k in allowed)
        with self._tx() as c:
            return c.execute(
                f"UPDATE rag_documents SET {assignments}, updated = ? WHERE id = ?", (*allowed.values(), time.time(), doc_id)
            ).rowcount > 0

    def rag_doc_delete(self, doc_id: int) -> bool:
        """Delete a document and its chunks; the collection's generation moves on."""
        with self._tx() as c:
            row = c.execute("SELECT collection_id FROM rag_documents WHERE id = ?", (doc_id,)).fetchone()
            if row is None:
                return False
            c.execute("DELETE FROM rag_documents WHERE id = ?", (doc_id,))
            c.execute("UPDATE rag_collections SET generation = generation + 1 WHERE id = ?", (row[0],))
            return True

    def rag_doc_remove_missing(self, collection_id: int, keep_sources: set[str]) -> int:
        """Delete folder documents whose files are gone; returns how many."""
        removed = 0
        for doc in self._query("SELECT id, source FROM rag_documents WHERE collection_id = ? AND origin = 'file'", (collection_id,)):
            if doc["source"] not in keep_sources and self.rag_doc_delete(doc["id"]):
                removed += 1
        return removed

    def rag_next_job(self, now: float) -> dict[str, Any] | None:
        """Claim the next document to index (``queued``, or ``waiting`` whose retry time has come)."""
        with self._tx() as c:
            row = c.execute(
                "SELECT id FROM rag_documents WHERE status = 'queued' OR (status = 'waiting' AND COALESCE(retry_at, 0) <= ?) "
                "OR (status = 'partial' AND retry_at IS NOT NULL AND retry_at <= ?) "
                "ORDER BY CASE status WHEN 'queued' THEN 0 ELSE 1 END, updated, id LIMIT 1",
                (now, now),
            ).fetchone()
            if row is None:
                return None
            c.execute("UPDATE rag_documents SET status = 'indexing', updated = ? WHERE id = ?", (now, row[0]))
        return self.rag_doc_get(int(row[0]))

    def rag_vision_cached(self, sha256: str, prompt: str) -> str | None:
        """The description of an image already read with this prompt version."""
        row = self._one("SELECT text FROM rag_vision_cache WHERE sha256 = ? AND prompt = ?", (sha256, prompt))
        return str(row["text"]) if row else None

    def rag_vision_put(self, sha256: str, prompt: str, text: str, model: str) -> None:
        """Remember a description so re-indexing does not pay for it again."""
        with self._tx() as c:
            c.execute("INSERT OR REPLACE INTO rag_vision_cache (sha256, prompt, text, model, created) VALUES (?, ?, ?, ?, ?)", (sha256, prompt, text, model, time.time()))

    def rag_recover_indexing(self) -> int:
        """After a crash, documents left ``indexing`` go back to the queue."""
        with self._tx() as c:
            return c.execute("UPDATE rag_documents SET status = 'queued' WHERE status = 'indexing'").rowcount

    def rag_requeue_collection(self, collection_id: int) -> int:
        """Queue every document of a collection again (after a change of embedding model)."""
        with self._tx() as c:
            return c.execute(
                "UPDATE rag_documents SET status = 'queued', error = '', attempts = 0, retry_at = NULL, updated = ? "
                "WHERE collection_id = ? AND status != 'indexing'",
                (time.time(), collection_id),
            ).rowcount

    # Chunks

    def rag_chunk_total(self) -> int:
        """Chunks in the whole knowledge base."""
        row = self._one("SELECT COUNT(*) AS n FROM rag_chunks")
        return int(row["n"]) if row else 0

    def rag_chunk_embeddings(self, doc_id: int) -> dict[str, bytes]:
        """Embeddings of a document's current chunks by text hash, so unchanged text is not embedded again."""
        return {
            r["text_sha"]: bytes(r["embedding"])
            for r in self._query("SELECT text_sha, embedding FROM rag_chunks WHERE document_id = ? AND embedding IS NOT NULL", (doc_id,))
        }

    def rag_chunks_swap(self, doc_id: int, chunks: list[dict[str, Any]], doc_fields: dict[str, Any]) -> None:
        """Replace a document's chunks and update the document in one transaction.

        Args:
            doc_id: Document id.
            chunks: Dicts with ``ord``, ``heading``, ``page``, ``vision``, ``text``, ``text_sha``, ``embedding``.
            doc_fields: Document columns to set (``title``, ``chunks``, ``status``, ``flagged``, ``embed_tag``, ``dim``, ...).
        """
        allowed = {k: v for k, v in doc_fields.items() if k in _DOC_FIELDS}
        now = time.time()
        with self._tx() as c:
            row = c.execute("SELECT collection_id FROM rag_documents WHERE id = ?", (doc_id,)).fetchone()
            if row is None:
                raise KeyError(f"document {doc_id} no longer exists")
            c.execute("DELETE FROM rag_chunks WHERE document_id = ?", (doc_id,))
            c.executemany(
                "INSERT INTO rag_chunks (document_id, ord, heading, page, vision, text, text_sha, embedding) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (doc_id, ch["ord"], ch.get("heading", ""), ch.get("page"), int(ch.get("vision", 0)), ch["text"], ch["text_sha"], ch.get("embedding"))
                    for ch in chunks
                ],
            )
            assignments = ", ".join(f"{k} = ?" for k in allowed)
            c.execute(
                f"UPDATE rag_documents SET {assignments + ', ' if assignments else ''}updated = ? WHERE id = ?",
                (*allowed.values(), now, doc_id),
            )
            c.execute("UPDATE rag_collections SET generation = generation + 1 WHERE id = ?", (row[0],))

    def rag_stored_hashes(self) -> set[str]:
        """Content hashes of uploaded and downloaded documents (their stored copies must be kept)."""
        return {r["sha256"] for r in self._query("SELECT DISTINCT sha256 FROM rag_documents WHERE origin != 'file'")}

    def rag_stale_count(self, collection_id: int, tag: str | None) -> int:
        """Indexed documents whose vectors do not come from ``tag`` (the model now configured)."""
        row = self._one(
            "SELECT COUNT(*) AS n FROM rag_documents WHERE collection_id = ? AND status IN ('ready', 'partial') AND embed_tag IS NOT ?",
            (collection_id, tag),
        )
        return int(row["n"]) if row else 0

    def rag_generation(self, collection_id: int) -> int:
        """The collection's change counter; caches are valid only for one value."""
        row = self._one("SELECT generation FROM rag_collections WHERE id = ?", (collection_id,))
        return int(row["generation"]) if row else -1

    def rag_vector_count(self, collection_id: int, tag: str | None) -> int:
        """Embedded chunks of documents whose vectors came from ``tag``."""
        row = self._one(
            "SELECT COUNT(*) AS n FROM rag_chunks c JOIN rag_documents d ON d.id = c.document_id "
            "WHERE d.collection_id = ? AND d.embed_tag = ? AND c.embedding IS NOT NULL",
            (collection_id, tag),
        )
        return int(row["n"]) if row else 0

    def rag_vector_blocks(self, collection_id: int, tag: str, block: int = 2048) -> Iterator[tuple[list[int], list[bytes]]]:
        """Yield ``(chunk ids, embedding blobs)`` in blocks, for documents embedded with ``tag``."""
        last = 0
        while True:
            rows = self._query(
                "SELECT c.id, c.embedding FROM rag_chunks c JOIN rag_documents d ON d.id = c.document_id "
                "WHERE d.collection_id = ? AND d.embed_tag = ? AND c.embedding IS NOT NULL AND c.id > ? ORDER BY c.id LIMIT ?",
                (collection_id, tag, last, block),
            )
            if not rows:
                return
            last = rows[-1]["id"]
            yield [r["id"] for r in rows], [bytes(r["embedding"]) for r in rows]

    def rag_fts(self, match: str, collection_ids: list[int] | None, limit: int) -> list[dict[str, Any]]:
        """BM25 search over chunk text and headings; best first."""
        where, params = "", ()
        if collection_ids is not None:
            if not collection_ids:
                return []
            where = f"AND d.collection_id IN ({', '.join('?' * len(collection_ids))})"
            params = tuple(collection_ids)
        return self._query(
            "SELECT c.id AS id, bm25(rag_chunks_fts, 1.0, 2.0) AS score FROM rag_chunks_fts "
            "JOIN rag_chunks c ON c.id = rag_chunks_fts.rowid JOIN rag_documents d ON d.id = c.document_id "
            f"WHERE rag_chunks_fts MATCH ? {where} ORDER BY score LIMIT ?",
            (match, *params, limit),
        )

    def rag_chunks_info(self, chunk_ids: list[int]) -> dict[int, dict[str, Any]]:
        """Chunks with their document and collection, by id."""
        if not chunk_ids:
            return {}
        rows = self._query(
            "SELECT c.id, c.document_id, c.ord, c.heading, c.page, c.vision, c.text, d.title, d.source, d.flagged, "
            "col.name AS collection FROM rag_chunks c JOIN rag_documents d ON d.id = c.document_id "
            f"JOIN rag_collections col ON col.id = d.collection_id WHERE c.id IN ({', '.join('?' * len(chunk_ids))})",
            tuple(chunk_ids),
        )
        return {r["id"]: r for r in rows}

    def rag_status(self) -> dict[str, Any]:
        """Totals for the dashboard."""
        by_status = {r["status"]: r["n"] for r in self._query("SELECT status, COUNT(*) AS n FROM rag_documents GROUP BY status")}
        row = self._one("SELECT (SELECT COUNT(*) FROM rag_collections) AS collections, (SELECT COUNT(*) FROM rag_chunks) AS chunks") or {}
        return {"collections": row.get("collections", 0), "chunks": row.get("chunks", 0), "documents": by_status}

    # Golden questions and evaluation runs

    def rag_golden_add(self, question: str, collection: str, source: str, phrase: str) -> int:
        """Add a question the knowledge base should answer, with where the answer lives (a source and/or a phrase)."""
        with self._tx() as c:
            return int(c.execute("INSERT INTO rag_golden (question, collection, source, phrase, created) VALUES (?, ?, ?, ?, ?)",
                                 (question, collection, source, phrase, time.time())).lastrowid or 0)

    def rag_golden_list(self) -> list[dict[str, Any]]:
        """Every golden question, oldest first."""
        return self._query("SELECT * FROM rag_golden ORDER BY id")

    def rag_golden_delete(self, golden_id: int) -> bool:
        """Remove a golden question; returns whether it existed."""
        with self._tx() as c:
            return c.execute("DELETE FROM rag_golden WHERE id = ?", (golden_id,)).rowcount > 0

    def rag_eval_add(self, label: str, variant: str, k: int, total: int, recall: float, mrr: float, misses: list[str]) -> int:
        """Record the result of one variant of an evaluation run."""
        with self._tx() as c:
            return int(c.execute("INSERT INTO rag_evals (ts, label, variant, k, total, recall, mrr, misses) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                                 (time.time(), label, variant, k, total, recall, mrr, json.dumps(misses))).lastrowid or 0)

    def rag_eval_list(self, limit: int = 60) -> list[dict[str, Any]]:
        """Recent evaluation results, newest first."""
        rows = self._query("SELECT * FROM rag_evals ORDER BY id DESC LIMIT ?", (limit,))
        for row in rows:
            row["misses"] = json.loads(row["misses"])
        return rows

    def rag_sample_chunks(self, count: int, collection: str | None = None) -> list[dict[str, Any]]:
        """Random indexed chunks with their document, to write questions from."""
        where, params = ("WHERE col.name = ?", (collection,)) if collection else ("", ())
        return self._query(
            "SELECT c.text, d.source, d.title, col.name AS collection FROM rag_chunks c JOIN rag_documents d ON d.id = c.document_id "
            f"JOIN rag_collections col ON col.id = d.collection_id {where} ORDER BY RANDOM() LIMIT ?", (*params, count),
        )
