"""Dashboard API: status and conversations (spec 10.5)."""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from aifred import __version__
from aifred.gateways.api import AdminDep, Ok, ServicesDep

router = APIRouter()

PREVIEW_CHARS = 140
STARTED = time.time()


@router.get("/api/status")
def system_status(request: Request, _: AdminDep, services: ServicesDep) -> dict[str, Any]:
    """Version, uptime, storage and the state of every gateway."""
    store = services.store
    data_dir = services.settings.data_dir
    db = data_dir / "aifred.db"
    sessions = store.list_sessions(limit=1000)
    return {
        "version": __version__, "uptime": time.time() - STARTED, "data_dir": str(data_dir),
        "db_bytes": db.stat().st_size if db.exists() else 0, "sessions": len(sessions),
        "sources": sorted({s["source"] for s in sessions}), "messages": sum(s["message_count"] for s in sessions),
        "telegram": request.app.state.telegram.status(), "mcp": services.mcp_manager.status(), "rag": services.rag.status() if services.rag.enabled else None,
        "vault": services.vault.status(),
    }


# Conversations


def _preview(text: str | None) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= PREVIEW_CHARS else text[: PREVIEW_CHARS - 1] + "…"


@router.get("/api/sessions")
def list_sessions(
    _: AdminDep, services: ServicesDep, source: str | None = None, limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)
) -> list[dict[str, Any]]:
    """Recent conversations with their message count and a preview of the last message."""
    rows = services.store.list_sessions(source, limit, offset)
    out = []
    for row in rows:
        last = services.store.messages(row["id"], limit=1)
        out.append({**row, "preview": _preview(last[0]["content"]) if last else ""})
    return out


@router.get("/api/sessions/{session_id}/messages")
def session_messages(
    session_id: str, _: AdminDep, services: ServicesDep, limit: int = Query(200, ge=1, le=1000), before: int | None = None
) -> list[dict[str, Any]]:
    """Stored messages of a conversation in chronological order."""
    return services.store.messages(session_id, limit=limit, before_id=before)


class Rename(BaseModel):
    """A new title."""

    title: str = Field(min_length=1, max_length=200)


@router.put("/api/sessions/{session_id}")
def rename_session(session_id: str, body: Rename, _: AdminDep, services: ServicesDep) -> Ok:
    """Rename a conversation."""
    if not services.store.rename_session(session_id, body.title.strip()):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such conversation")
    return Ok()


@router.delete("/api/sessions/{session_id}")
def delete_session(session_id: str, _: AdminDep, services: ServicesDep) -> Ok:
    """Delete a conversation and its messages."""
    if not services.store.delete_session(session_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such conversation")
    return Ok()
