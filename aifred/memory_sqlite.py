"""Long-term memory on the SQLite database (docs/adr.md ADR-005).

Full-text search is FTS5, vector search an exact cosine product over the float32 vectors of the subjects asked for,
the same approach as the knowledge base (ADR-003), and the two rankings are fused with reciprocal rank fusion.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Any

import numpy as np

from aifred.embeddings import from_blob, to_blob
from aifred.memory_backend import KINDS, SOURCES, STATUSES, Fact, MemoryFactError, Status, check_subject
from aifred.memory_graph import _key
from aifred.rag import build_match, reciprocal_rank_fusion
from aifred.store import Store

DAY = 86400.0
MAX_TEXT = 600
MAX_ENTITIES = 8


def _unit(vector: np.ndarray) -> np.ndarray:
    v = np.asarray(vector, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(v))
    return v / norm if norm > 0 else v


def _names(entities: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(k for e in entities if (k := _key(e))))[:MAX_ENTITIES]


def _fact(row: dict[str, Any], score: float = 0.0, similarity: float = 0.0) -> Fact:
    return Fact(
        id=row["id"], subject=row["subject"], kind=row["kind"], text=row["text"], importance=row["importance"],
        source=row.get("source", ""), source_session=row.get("source_session", ""), status=row.get("status", "active"),
        entities=row.get("entities", []), superseded_by=row.get("superseded_by"), created=row.get("created", 0.0),
        updated=row.get("updated", 0.0), last_used=row.get("last_used"), use_count=row.get("use_count", 0),
        score=score, similarity=similarity,
    )


def _clean(text: str, kind: str, importance: float, source: str = "auto") -> tuple[str, str, float]:
    text = " ".join(text.split())
    if not text:
        raise MemoryFactError("a fact cannot be empty")
    if len(text) > MAX_TEXT:
        raise MemoryFactError(f"a fact is limited to {MAX_TEXT} characters; split it into smaller facts")
    if kind not in KINDS:
        raise MemoryFactError(f"kind must be one of {', '.join(KINDS)}")
    if source not in SOURCES:
        raise MemoryFactError(f"source must be one of {', '.join(SOURCES)}")
    return text, kind, min(1.0, max(0.0, float(importance)))


class SqliteMemory:
    """:class:`aifred.memory_backend.MemoryBackend` over a :class:`~aifred.store.Store`."""

    name = "sqlite"

    def __init__(self, store: Store) -> None:
        self._s = store

    def add(
        self,
        subject: str,
        text: str,
        *,
        kind: str = "fact",
        importance: float = 0.5,
        source: str = "auto",
        source_session: str = "",
        entities: Sequence[str] = (),
        embedding: np.ndarray | None = None,
        emb_model: str | None = None,
    ) -> Fact:
        """Store a fact; the embedding, when given, is normalised and kept with the model that made it."""
        check_subject(subject)
        text, kind, importance = _clean(text, kind, importance, source)
        blob = to_blob(_unit(embedding)) if embedding is not None else None
        fact_id = self._s.mem_fact_add(
            subject, text, kind, importance, source, source_session, _names(entities), blob, emb_model if blob else None
        )
        fact = self.get(fact_id, [subject])
        assert fact is not None
        return fact

    def get(self, fact_id: int, subjects: Sequence[str]) -> Fact | None:
        """A fact, if it belongs to one of ``subjects``."""
        row = self._s.mem_fact_get(fact_id, subjects)
        return _fact(row) if row else None

    def update(
        self,
        fact_id: int,
        subjects: Sequence[str],
        *,
        text: str | None = None,
        kind: str | None = None,
        importance: float | None = None,
        entities: Sequence[str] | None = None,
        embedding: np.ndarray | None = None,
        emb_model: str | None = None,
    ) -> bool:
        """Change a fact. A new text without a new embedding drops the old vector, which no longer matches it."""
        current = self._s.mem_fact_get(fact_id, subjects)
        if current is None:
            return False
        new_text, new_kind, new_importance = _clean(
            current["text"] if text is None else text, current["kind"] if kind is None else kind,
            current["importance"] if importance is None else importance,
        )
        fields: dict[str, Any] = {"text": new_text, "kind": new_kind, "importance": new_importance}
        if embedding is not None:
            fields.update(embedding=to_blob(_unit(embedding)), emb_model=emb_model)
        elif new_text != current["text"]:
            fields.update(embedding=None, emb_model=None)
        return self._s.mem_fact_update(fact_id, subjects, None if entities is None else _names(entities), **fields)

    def set_status(self, fact_id: int, subjects: Sequence[str], status: Status, superseded_by: int | None = None) -> bool:
        """Archive, restore or supersede a fact; restoring clears the archive time."""
        if status not in STATUSES:
            raise MemoryFactError(f"status must be one of {', '.join(STATUSES)}")
        archived = None if status == "active" else time.time()
        return self._s.mem_fact_update(fact_id, subjects, status=status, superseded_by=superseded_by, archived=archived)

    def delete(self, fact_id: int, subjects: Sequence[str]) -> bool:
        """Delete one fact for good."""
        return self._s.mem_fact_delete(fact_id, subjects)

    def forget_subject(self, subject: str) -> int:
        """Delete everything stored for a subject."""
        return self._s.mem_forget(check_subject(subject))

    def list_facts(
        self,
        subjects: Sequence[str],
        *,
        status: str | None = "active",
        kind: str | None = None,
        source: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[Fact]:
        """Facts of ``subjects``, most recently changed first."""
        return [_fact(r) for r in self._s.mem_fact_list(subjects, status, kind, source, limit, offset)]

    def search(
        self,
        subjects: Sequence[str],
        query: str,
        *,
        vector: np.ndarray | None = None,
        emb_model: str | None = None,
        limit: int = 10,
        min_similarity: float = 0.0,
    ) -> list[Fact]:
        """Full-text and vector rankings fused; a vector only counts against facts embedded with ``emb_model``."""
        if not subjects:
            return []
        pool = max(limit * 3, 20)
        rankings: list[list[int]] = []
        match = build_match(query)
        if match:
            rankings.append([r["id"] for r in self._s.mem_fact_search_text(subjects, match, pool)])
        similarity: dict[int, float] = {}
        if vector is not None and emb_model:
            similarity = self._vector_scores(subjects, _unit(vector), emb_model, pool, min_similarity)
            rankings.append(sorted(similarity, key=similarity.__getitem__, reverse=True))
        fused = reciprocal_rank_fusion([r for r in rankings if r])
        top = sorted(fused, key=fused.__getitem__, reverse=True)[:limit]
        rows = {r["id"]: r for r in self._s.mem_facts_by_ids(top, subjects)}
        return [_fact(rows[i], fused[i], similarity.get(i, 0.0)) for i in top if i in rows]

    def _vector_scores(self, subjects: Sequence[str], query: np.ndarray, emb_model: str, limit: int, floor: float) -> dict[int, float]:
        rows = self._s.mem_fact_vectors(subjects, emb_model)
        vectors = [(r["id"], from_blob(r["embedding"])) for r in rows]
        vectors = [(i, v) for i, v in vectors if v.shape == query.shape]
        if not vectors:
            return {}
        ids = np.fromiter((i for i, _ in vectors), dtype=np.int64, count=len(vectors))
        scores = np.stack([v for _, v in vectors]) @ query
        order = np.argsort(-scores)[:limit]
        return {int(ids[i]): float(scores[i]) for i in order if scores[i] >= floor}

    def neighbors(self, subjects: Sequence[str], entities: Sequence[str], limit: int = 20) -> list[Fact]:
        """Active facts that mention any of ``entities``."""
        return [_fact(r) for r in self._s.mem_entity_facts(subjects, _names(entities), limit)]

    def touch(self, ids: Sequence[int]) -> None:
        """Record that facts were recalled."""
        self._s.mem_fact_touch(list(ids))

    def archive_stale(self, older_than_days: float) -> int:
        """Archive active facts unused for ``older_than_days``."""
        return self._s.mem_archive_stale(time.time() - older_than_days * DAY)

    def purge_archived(self, older_than_days: float) -> int:
        """Delete facts archived or superseded for ``older_than_days``."""
        return self._s.mem_purge_archived(time.time() - older_than_days * DAY)

    def stats(self, subjects: Sequence[str] | None = None) -> dict[str, object]:
        """Counts per status and the subjects that have facts."""
        return self._s.mem_stats(subjects)
