"""Dashboard API for long-term memory: facts, subjects, settings in effect, export and import (docs/adr.md ADR-005).

Distinct from ``/api/memory``, which edits the curated notes (MEMORY.md and USER.md). The admin sees the facts of
every subject; the conversations themselves only ever see their own (:mod:`aifred.memory_service`).
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field

from aifred.gateways.api import AdminDep, ServicesDep
from aifred.memory_backend import KINDS, Fact, MemoryFactError, check_subject
from aifred.memory_service import refusal
from aifred.writes import WriteError, apply_memory

router = APIRouter()
IMPORT_MAX_BYTES = 5_242_880


class FactIn(BaseModel):
    """A fact written by hand."""

    subject: str = Field(min_length=1, max_length=140)
    text: str = Field(min_length=1, max_length=2000)
    kind: Literal["preference", "decision", "fact", "outcome"] = "fact"
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    entities: list[str] = Field(default_factory=list, max_length=8)


class FactPatch(BaseModel):
    """Fields to change; omitted ones stay."""

    text: str | None = Field(default=None, min_length=1, max_length=2000)
    kind: Literal["preference", "decision", "fact", "outcome"] | None = None
    importance: float | None = Field(default=None, ge=0.0, le=1.0)
    entities: list[str] | None = Field(default=None, max_length=8)


class PauseIn(BaseModel):
    """Switch a subject's memory off or on."""

    paused: bool


def _unprocessable(exc: Exception) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))


def _view(services: Any, fact: Fact) -> dict[str, Any]:
    return {**asdict(fact), "promotable": services.ltm.promotable(fact)}


def _all(services: Any) -> list[str]:
    return list(services.ltm.backend.stats()["subjects"])


def _find(services: Any, fact_id: int) -> Fact:
    fact = services.ltm.backend.get(fact_id, _all(services))
    if fact is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no fact #{fact_id}")
    return fact


def _embed(services: Any, text: str) -> dict[str, Any]:
    vectors, tag = services.ltm.embed([text])
    return {"embedding": vectors[0], "emb_model": tag} if vectors else {}


@router.get("/api/ltm")
def status_(_: AdminDep, services: ServicesDep) -> dict[str, Any]:
    """Settings in effect, counts per subject and status, and the last extraction."""
    return services.ltm.status()


@router.get("/api/ltm/facts")
def list_facts(
    _: AdminDep, services: ServicesDep, subject: Annotated[str, Query(max_length=140)] = "",
    state: Annotated[str, Query(alias="status", max_length=12)] = "active", kind: Annotated[str, Query(max_length=12)] = "",
    source: Annotated[str, Query(max_length=8)] = "", q: Annotated[str, Query(max_length=300)] = "",
    limit: Annotated[int, Query(ge=1, le=500)] = 100, offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    """Facts of one subject (all when empty); ``status`` is ``active``, ``archived``, ``superseded`` or ``all``; ``q`` searches the text."""
    ltm = services.ltm
    subjects = [subject] if subject else _all(services)
    wanted = None if state == "all" else state
    if q.strip():
        found = [f for f in ltm.backend.search(subjects, q, limit=limit) if kind in ("", f.kind)]
        facts = [f for f in found if wanted in (None, f.status)]
    else:
        facts = ltm.backend.list_facts(subjects, status=wanted, kind=kind or None, source=source or None, limit=limit, offset=offset)
    return {"facts": [_view(services, f) for f in facts], "kinds": list(KINDS)}


@router.post("/api/ltm/facts", status_code=status.HTTP_201_CREATED)
def add_fact(body: FactIn, _: AdminDep, services: ServicesDep) -> dict[str, Any]:
    """Write a fact by hand into any subject."""
    try:
        check_subject(body.subject)
        if why := refusal(body.text):
            raise MemoryFactError(f"refused: {why}")
        fact = services.ltm.backend.add(
            body.subject, body.text, kind=body.kind, importance=body.importance, source="manual", entities=body.entities,
            **_embed(services, body.text),
        )
    except MemoryFactError as exc:
        raise _unprocessable(exc) from None
    services.store.journal_add("facts", "add", fact.text[:300], None, "applied", {"id": fact.id, "subject": fact.subject})
    return _view(services, fact)


@router.put("/api/ltm/facts/{fact_id}")
def change_fact(fact_id: int, body: FactPatch, _: AdminDep, services: ServicesDep) -> dict[str, Any]:
    """Edit a fact; a new text gets a new vector."""
    current = _find(services, fact_id)
    try:
        if body.text and (why := refusal(body.text)):
            raise MemoryFactError(f"refused: {why}")
        extra = _embed(services, body.text) if body.text and body.text != current.text else {}
        services.ltm.backend.update(
            fact_id, [current.subject], text=body.text, kind=body.kind, importance=body.importance, entities=body.entities, **extra
        )
    except MemoryFactError as exc:
        raise _unprocessable(exc) from None
    return _view(services, _find(services, fact_id))


@router.post("/api/ltm/facts/{fact_id}/archive")
def archive_fact(fact_id: int, _: AdminDep, services: ServicesDep) -> dict[str, Any]:
    """Hide a fact from recall; it can be restored until the grace period ends."""
    fact = _find(services, fact_id)
    services.ltm.backend.set_status(fact_id, [fact.subject], "archived")
    return _view(services, _find(services, fact_id))


@router.post("/api/ltm/facts/{fact_id}/restore")
def restore_fact(fact_id: int, _: AdminDep, services: ServicesDep) -> dict[str, Any]:
    """Bring an archived or replaced fact back."""
    fact = _find(services, fact_id)
    services.ltm.backend.set_status(fact_id, [fact.subject], "active")
    return _view(services, _find(services, fact_id))


@router.delete("/api/ltm/facts/{fact_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_fact(fact_id: int, _: AdminDep, services: ServicesDep) -> None:
    """Delete a fact for good."""
    fact = _find(services, fact_id)
    services.ltm.backend.delete(fact_id, [fact.subject])
    services.store.journal_add("facts", "delete", fact.text[:300], None, "applied", {"id": fact_id, "subject": fact.subject})


@router.post("/api/ltm/facts/{fact_id}/promote")
def promote_fact(fact_id: int, _: AdminDep, services: ServicesDep) -> dict[str, str]:
    """Copy a fact into MEMORY.md (the curated notes sent with every request); the fact stays where it is."""
    fact = _find(services, fact_id)
    if fact.subject != "owner":
        raise HTTPException(status.HTTP_409_CONFLICT, "only the owner's facts can go into MEMORY.md")
    try:
        message = apply_memory(services, {"action": "add", "target": "memory", "content": fact.text}, None)
    except WriteError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    return {"detail": message}


@router.delete("/api/ltm/subjects/{subject}")
def forget_subject(subject: str, _: AdminDep, services: ServicesDep) -> dict[str, int]:
    """Delete everything remembered for one subject."""
    try:
        removed = services.ltm.backend.forget_subject(subject)
    except MemoryFactError as exc:
        raise _unprocessable(exc) from None
    services.store.journal_add("facts", "forget", f"{removed} facts of {subject}", None, "applied", {"subject": subject})
    return {"removed": removed}


@router.put("/api/ltm/subjects/{subject}/pause")
def pause_subject(subject: str, body: PauseIn, _: AdminDep, services: ServicesDep) -> dict[str, Any]:
    """Stop (or resume) storing and recalling for one subject; its facts are kept."""
    try:
        services.ltm.set_paused(subject, body.paused)
    except MemoryFactError as exc:
        raise _unprocessable(exc) from None
    return {"subject": subject, "paused": body.paused}


@router.post("/api/ltm/maintain")
def maintain(_: AdminDep, services: ServicesDep) -> dict[str, int]:
    """Run the upkeep now: archive unused facts, delete expired ones, merge duplicates."""
    return services.ltm.maintain()


@router.get("/api/ltm/export")
def export(
    _: AdminDep, services: ServicesDep, subject: Annotated[str, Query(max_length=140)] = "",
    fmt: Annotated[Literal["json", "md"], Query(alias="format")] = "json",
) -> Response:
    """Download facts as JSON (the import format) or Markdown; vectors are not included."""
    subjects = [subject] if subject else None
    name = (subject or "all").replace(":", "-")
    if fmt == "md":
        return Response(services.ltm.export_markdown(subjects), media_type="text/markdown; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="aifred-memory-{name}.md"'})
    return Response(json.dumps(services.ltm.export(subjects), ensure_ascii=False, indent=1), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="aifred-memory-{name}.json"'})


@router.post("/api/ltm/import")
async def import_facts(
    request: Request, _: AdminDep, services: ServicesDep, mode: Annotated[Literal["merge", "replace"], Query()] = "merge",
    dry_run: Annotated[bool, Query()] = True,
) -> dict[str, Any]:
    """Import an export. ``dry_run`` (the default) only reports what would change; send ``dry_run=false`` to apply it."""
    if int(request.headers.get("content-length") or 0) > IMPORT_MAX_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "the file is larger than 5 MB")
    try:
        data = await request.json()
    except ValueError:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "the file is not valid JSON") from None
    try:
        report = await asyncio.to_thread(services.ltm.import_facts, data, mode=mode, dry_run=dry_run)
    except MemoryFactError as exc:
        raise _unprocessable(exc) from None
    if not dry_run:
        services.store.journal_add("facts", "import", f"{report['new']} facts ({mode})", None, "applied", {})
        await asyncio.to_thread(services.ltm.reembed)
    return report
