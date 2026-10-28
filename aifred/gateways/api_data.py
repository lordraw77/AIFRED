"""Dashboard API: memory, skills, tools, the journal and approval queue, logs, search and gateway restart (spec 10.5)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from aifred.gateways.api import AdminDep, Ok, ServicesDep
from aifred.instructions import INTERNAL_TOOLSETS
from aifred.logbuffer import BUFFER
from aifred.memory import MemoryError_
from aifred.skills import SkillError
from aifred.tools.registry import SOURCE_TO_GATEWAY, ToolContext
from aifred.writes import WriteError, apply_memory, apply_pending, apply_skill, reject_pending, undo_entry

router = APIRouter()


def _bad(exc: Exception) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))


# Memory


def _memory_view(services: Any, target: str) -> dict[str, Any]:
    try:
        snap = services.memory.snapshot(target)
    except MemoryError_ as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None
    return {"target": target, "entries": snap.entries, "used": snap.used, "limit": snap.limit, "counter": snap.counter}


class EntryIn(BaseModel):
    """A memory entry; ``match`` identifies the entry to replace."""

    content: str = Field(min_length=1, max_length=2000)
    match: str | None = None


@router.get("/api/memory/graph/view")
def memory_graph(_: AdminDep, services: ServicesDep) -> dict[str, Any]:
    """The memory entries as a graph: names in double brackets are nodes, shared entries are edges."""
    from aifred.memory_graph import build

    return build({t: services.memory.snapshot(t).entries for t in ("memory", "user")})


@router.get("/api/memory/{target}")
def get_memory(target: str, _: AdminDep, services: ServicesDep) -> dict[str, Any]:
    """Entries of ``memory`` or ``user`` with the space used."""
    return _memory_view(services, target)


@router.post("/api/memory/{target}", status_code=status.HTTP_201_CREATED)
def add_memory(target: str, body: EntryIn, _: AdminDep, services: ServicesDep) -> dict[str, Any]:
    """Add an entry."""
    _memory_view(services, target)
    try:
        apply_memory(services, {"action": "add", "target": target, "content": body.content}, None)
    except WriteError as exc:
        raise _bad(exc) from None
    return _memory_view(services, target)


@router.put("/api/memory/{target}")
def replace_memory(target: str, body: EntryIn, _: AdminDep, services: ServicesDep) -> dict[str, Any]:
    """Replace the entry that contains ``match``."""
    _memory_view(services, target)
    try:
        apply_memory(services, {"action": "replace", "target": target, "match": body.match, "content": body.content}, None)
    except WriteError as exc:
        raise _bad(exc) from None
    return _memory_view(services, target)


@router.delete("/api/memory/{target}")
def delete_memory(target: str, entry: Annotated[str, Query(min_length=1)], _: AdminDep, services: ServicesDep) -> dict[str, Any]:
    """Remove an entry given its exact text."""
    _memory_view(services, target)
    if not services.memory.remove_exact(target, entry):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such entry")
    services.store.journal_add("memory", "remove", f"removed: {entry}"[:300], None, "applied", {"target": target, "old": entry})
    return _memory_view(services, target)


# Skills


class SkillIn(BaseModel):
    """A skill's description and Markdown body."""

    description: str = Field(min_length=1, max_length=500)
    body: str = Field(min_length=1, max_length=100_000)


@router.get("/api/skills")
def list_skills(_: AdminDep, services: ServicesDep) -> list[dict[str, str]]:
    """Skills with their descriptions."""
    return [{"name": n, "description": d} for n, d in services.skills.index()]


@router.get("/api/skills/{name}")
def get_skill(name: str, _: AdminDep, services: ServicesDep) -> dict[str, str]:
    """One skill with its body."""
    try:
        skill = services.skills.get(name)
    except SkillError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None
    return {"name": skill.name, "description": skill.description, "body": skill.body}


@router.put("/api/skills/{name}")
def put_skill(name: str, body: SkillIn, _: AdminDep, services: ServicesDep) -> dict[str, str]:
    """Create or overwrite a skill (journaled, so it can be undone)."""
    try:
        apply_skill(services, {"name": name, "description": body.description, "body": body.body}, None)
    except WriteError as exc:
        raise _bad(exc) from None
    return get_skill(name, _, services)


@router.delete("/api/skills/{name}")
def delete_skill(name: str, _: AdminDep, services: ServicesDep) -> Ok:
    """Delete a skill (journaled with its content)."""
    try:
        previous = services.skills.get(name)
    except SkillError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None
    services.skills.delete(name)
    services.store.journal_add("skill", "delete", f"{name}: deleted", None, "applied", {"name": name, "previous": {"description": previous.description, "body": previous.body}})
    return Ok()


# Tools


@router.get("/api/tools")
def list_tools(_: AdminDep, services: ServicesDep) -> dict[str, Any]:
    """Toolsets with their tools, which gateways enable them and how often each tool was called since the start."""
    registry = services.tools
    gateways = {name: SOURCE_TO_GATEWAY[name] for name in ("cli", "web", "api", "telegram", "cron")}
    enabled = {g: registry.enabled_toolsets(services, source) for source, g in gateways.items()}
    ctx = ToolContext(services, "dashboard", "web")
    toolsets: dict[str, list[dict[str, Any]]] = {}
    for tool in registry.all():
        if tool.toolset in INTERNAL_TOOLSETS:
            continue
        toolsets.setdefault(tool.toolset, []).append({
            "name": tool.name, "description": tool.description[:300], "available": bool(tool.available(ctx)),
            "restricted_ok": tool.restricted_ok, "calls": registry.calls.get(tool.name, 0),
        })
    return {
        "gateways": list(gateways.values()),
        "toolsets": [
            {"name": name, "tools": sorted(tools, key=lambda t: t["name"]), "enabled": {g: registry.toolset_enabled(name, enabled[g]) for g in gateways.values()},
             "calls": sum(t["calls"] for t in tools)}
            for name, tools in sorted(toolsets.items())
        ],
    }


TOGGLE_GATEWAYS = {name: SOURCE_TO_GATEWAY[name] for name in ("cli", "web", "api", "telegram", "cron")}


class ToolsetSwitch(BaseModel):
    """Turn a toolset on or off for some gateways; ``ALL`` changes the shared ``TOOLSETS`` list."""

    gateways: list[str] = Field(min_length=1, max_length=6)
    on: bool


def _toolset_name(name: str, services: Any) -> str:
    known = {t.toolset for t in services.tools.all()} - INTERNAL_TOOLSETS
    servers = {f"mcp:{x.name}" for x in services.mcp.list()}
    if name not in known | servers | {"mcp", "agents", "cron"}:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no such toolset: {name}")
    return name


@router.get("/api/toolsets/{name:path}")
def toolset_state(name: str, _: AdminDep, services: ServicesDep) -> dict[str, Any]:
    """For which gateways a toolset is on (each gateway's own list, else the shared ``TOOLSETS``)."""
    name = _toolset_name(name, services)
    registry = services.tools
    return {"name": name, "gateways": {g: registry.toolset_enabled(name, registry.enabled_toolsets(services, source)) for source, g in TOGGLE_GATEWAYS.items()}}


@router.put("/api/toolsets/{name:path}")
def toolset_switch(name: str, body: ToolsetSwitch, _: AdminDep, services: ServicesDep) -> dict[str, Any]:
    """Add a toolset to, or remove it from, the lists of the chosen gateways, so nobody edits TOOLSETS by hand.

    A gateway without its own list gets one (a copy of the shared list with the change), so the other gateways do not move.
    """
    name = _toolset_name(name, services)
    cfg = services.config
    values: dict[str, list[str]] = {}
    for gateway in dict.fromkeys(body.gateways):
        if gateway == "ALL":
            key = "TOOLSETS"
            current = list(cfg.get(key))
        elif gateway in TOGGLE_GATEWAYS.values():
            key = f"TOOLSETS_{gateway}"
            own = cfg.get(key)
            current = list(own if own is not None else cfg.get("TOOLSETS"))
        else:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"unknown gateway: {gateway}")
        if body.on and name not in current:
            current.append(name)
        elif not body.on:
            if name.startswith("mcp:") and "mcp" in current:
                raise HTTPException(status.HTTP_409_CONFLICT, f"{gateway} uses the whole mcp group; remove mcp on the Config page to choose servers one by one")
            current = [x for x in current if x != name]
        values[key] = current
    try:
        cfg.update(values)
    except Exception as exc:  # SettingError: the list is not valid
        raise _bad(exc) from None
    return toolset_state(name, _, services)


# Journal and approvals


@router.get("/api/journal")
def get_journal(_: AdminDep, services: ServicesDep, kind: str | None = None, limit: int = Query(100, ge=1, le=500)) -> list[dict[str, Any]]:
    """What the agent learned and what the admin changed, newest first."""
    return services.store.journal_list(limit=limit, kind=kind)


@router.post("/api/journal/{entry_id}/undo")
def undo_journal(entry_id: int, _: AdminDep, services: ServicesDep) -> dict[str, str]:
    """Revert an applied memory or skill change."""
    try:
        return {"message": undo_entry(services, entry_id)}
    except WriteError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None


@router.get("/api/pending")
def get_pending(_: AdminDep, services: ServicesDep) -> list[dict[str, Any]]:
    """Writes waiting for approval."""
    return services.store.pending_list()


@router.post("/api/pending/{write_id}/approve")
def approve_pending(write_id: int, _: AdminDep, services: ServicesDep) -> dict[str, str]:
    """Apply a queued write (the safety scan runs again)."""
    try:
        return {"message": apply_pending(services, write_id)}
    except WriteError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None


@router.post("/api/pending/{write_id}/reject")
def reject_pending_write(write_id: int, _: AdminDep, services: ServicesDep) -> Ok:
    """Discard a queued write."""
    try:
        reject_pending(services, write_id)
    except WriteError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    return Ok()


# Search


@router.get("/api/search")
def search_history(
    _: AdminDep, services: ServicesDep, q: Annotated[str, Query(min_length=1, max_length=200)], source: str | None = None, limit: int = Query(30, ge=1, le=100)
) -> list[dict[str, Any]]:
    """Full-text search over every conversation (user and assistant messages)."""
    rows = services.store.search(q, limit=limit * 4 if source else limit)
    return [r for r in rows if not source or r["source"] == source][:limit]


# Logs


@router.get("/api/logs")
def get_logs(_: AdminDep, level: str = "INFO", limit: int = Query(500, ge=1, le=500)) -> list[dict[str, Any]]:
    """The last log lines at or above ``level``."""
    return BUFFER.lines(level=level, limit=limit)


@router.get("/api/logs/stream", response_model=None)
async def stream_logs(request: Request, _: AdminDep, level: str = "INFO") -> StreamingResponse:
    """Server-Sent Events: new log lines as they appear (a keep-alive comment every 15 s)."""
    async def events() -> AsyncIterator[bytes]:
        last = BUFFER.lines(level="DEBUG", limit=1)
        seq = last[-1]["seq"] if last else 0
        idle = 0
        while not await request.is_disconnected():
            rows = BUFFER.lines(since=seq, level=level)
            if rows:
                seq = max(r["seq"] for r in rows)
                idle = 0
                for row in rows:
                    yield b"data: " + json.dumps(row, ensure_ascii=False).encode() + b"\n\n"
            else:
                idle += 1
                if idle >= 15:
                    idle = 0
                    yield b": keep-alive\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# Restart


@router.post("/api/restart")
async def restart_gateways(request: Request, _: AdminDep, services: ServicesDep) -> Ok:
    """Restart the Telegram poller and reconnect the MCP servers (the web server itself keeps running)."""
    await request.app.state.telegram.restart()
    await asyncio.to_thread(services.mcp_manager.reload)
    return Ok()
