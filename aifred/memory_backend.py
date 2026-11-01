"""The contract of long-term memory: facts the assistant keeps between sessions (docs/adr.md ADR-005).

``MEMORY.md`` and ``USER.md`` (:mod:`aifred.memory`) stay the curated notes sent with every request. This is the second
level: any number of small facts, found again by search when a turn needs them. A backend stores and finds facts; it
knows nothing about models. Embeddings are computed by the caller and passed in.

A fact belongs to exactly one *subject*: ``owner``, ``user:<id>``, ``chat:<id>`` or ``agent:<name>``. Every read names
the subjects it may see (:func:`visible_subjects`); a fact of any other subject is never returned.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol

import numpy as np

Status = Literal["active", "archived", "superseded"]
KINDS = ("preference", "decision", "fact", "outcome")
SOURCES = ("auto", "manual", "import")
STATUSES: tuple[Status, ...] = ("active", "archived", "superseded")

OWNER = "owner"
_SUBJECT = re.compile(r"^(owner|(user|chat|agent):[A-Za-z0-9_.:@+\-]{1,128})$")


class MemoryFactError(ValueError):
    """A fact or subject was refused; the message is meant for the person or the model."""


def check_subject(subject: str) -> str:
    """Return ``subject`` if it is ``owner`` or ``user:``, ``chat:``, ``agent:`` followed by an id.

    Raises:
        MemoryFactError: For anything else.
    """
    if not _SUBJECT.match(subject):
        raise MemoryFactError(f"invalid memory subject {subject!r}; use owner, user:<id>, chat:<id> or agent:<name>")
    return subject


def visible_subjects(*, restricted: bool, user_id: str | None = None, chat_id: str | None = None, agent: str | None = None) -> tuple[str, ...]:
    """Subjects one conversation may read.

    The owner's chats see ``owner``; a restricted chat (somebody other than the owner) sees only its own user and
    chat, never ``owner``; a sub-agent sees only its own. Nothing is shared between them.
    """
    if agent:
        return (check_subject(f"agent:{agent}"),)
    if not restricted:
        return (OWNER,)
    found = [check_subject(f"{kind}:{value}") for kind, value in (("user", user_id), ("chat", chat_id)) if value]
    return tuple(found)


@dataclass
class Fact:
    """One remembered fact.

    Attributes:
        id: Row id.
        subject: Whose fact it is.
        kind: One of :data:`KINDS`.
        text: The fact, one or two sentences.
        importance: 0 to 1; weighs recall and retention.
        source: ``auto`` (extracted), ``manual`` or ``import``.
        source_session: Session it was extracted from.
        status: ``active``, ``archived`` (retention) or ``superseded`` (replaced by ``superseded_by``).
        entities: Lower-cased names the fact is about.
        created: Creation time.
        updated: Last change.
        last_used: Last recall, if any.
        use_count: How many times it was recalled.
        score: Rank of this fact in the search that returned it (higher is better); 0 otherwise.
        similarity: Cosine similarity to the query vector when a vector search found it; 0 otherwise.
    """

    id: int
    subject: str
    kind: str
    text: str
    importance: float
    source: str
    source_session: str
    status: str
    entities: list[str] = field(default_factory=list)
    superseded_by: int | None = None
    created: float = 0.0
    updated: float = 0.0
    last_used: float | None = None
    use_count: int = 0
    score: float = 0.0
    similarity: float = 0.0


class MemoryBackend(Protocol):
    """What the extractor, the recall step, the tool and the web page use. Synchronous: call from worker threads."""

    name: str

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
        """Store a fact."""

    def get(self, fact_id: int, subjects: Sequence[str]) -> Fact | None:
        """A fact, if it belongs to one of ``subjects``."""

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
        """Change a fact; False if it is not visible."""

    def set_status(self, fact_id: int, subjects: Sequence[str], status: Status, superseded_by: int | None = None) -> bool:
        """Archive, restore or supersede a fact."""

    def delete(self, fact_id: int, subjects: Sequence[str]) -> bool:
        """Delete one fact for good."""

    def forget_subject(self, subject: str) -> int:
        """Delete everything stored for a subject; returns the number of facts."""

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
        """Active facts for a query: full-text and, when a vector is given, vector search, fused. Best first."""

    def neighbors(self, subjects: Sequence[str], entities: Sequence[str], limit: int = 20) -> list[Fact]:
        """Active facts that mention any of ``entities``: one hop over the entity graph."""

    def touch(self, ids: Sequence[int]) -> None:
        """Record that facts were recalled."""

    def archive_stale(self, older_than_days: float) -> int:
        """Archive active facts unused for that long; returns how many."""

    def purge_archived(self, older_than_days: float) -> int:
        """Delete facts archived or superseded for that long; returns how many."""

    def stats(self, subjects: Sequence[str] | None = None) -> dict[str, object]:
        """Counts per status and the subjects that have facts."""
