"""Dashboard API for sub-agents (docs/specs.md section 14.1)."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from aifred.agents import MEMORY_MODES, AgentError
from aifred.gateways.api import AdminDep, Ok, ServicesDep
from aifred.tools.registry import ToolContext

router = APIRouter()


class AgentIn(BaseModel):
    """Agent fields; on update an omitted field keeps its value (``null`` clears a limit)."""

    name: str | None = None
    description: str | None = None
    role: str | None = None
    route: str | None = None
    skills: list[str] | None = None
    toolsets: list[str] | None = None
    memory: str | None = None
    max_iterations: int | None = None
    timeout: int | None = None
    token_budget: int | None = None
    enabled: bool | None = None


class RunIn(BaseModel):
    """A task to try the agent on."""

    task: str
    context: str = ""


def _error(exc: AgentError) -> HTTPException:
    code = status.HTTP_404_NOT_FOUND if str(exc).startswith("no agent") else status.HTTP_422_UNPROCESSABLE_CONTENT
    return HTTPException(code, str(exc))


@router.get("/api/agents")
async def list_agents(_: AdminDep, services: ServicesDep) -> dict[str, Any]:
    """Every agent plus what the editor offers to choose from."""
    rows = await asyncio.to_thread(services.store.agent_list)
    routes = await asyncio.to_thread(services.store.route_names)
    skills = await asyncio.to_thread(services.skills.index)
    return {
        "agents": rows, "routes": [r for r in routes if r != services.config.get("RAG_VISION_ROUTE")],
        "toolsets": sorted(services.agents.known_toolsets() | {"mcp"}), "skills": [n for n, _ in skills], "memory_modes": list(MEMORY_MODES),
        "defaults": {"max_iterations": services.config.get("AGENT_MAX_ITERATIONS"), "timeout": services.config.get("AGENT_TIMEOUT")},
    }


@router.post("/api/agents", status_code=status.HTTP_201_CREATED)
async def create_agent(body: AgentIn, _: AdminDep, services: ServicesDep) -> dict[str, Any]:
    """Define a new agent (``name``, ``description`` and ``role`` are required)."""
    values = body.model_dump(exclude_unset=True)
    name = str(values.pop("name", "") or "").strip().lower()
    try:
        if await asyncio.to_thread(services.store.agent_get, name) is not None:
            raise AgentError(f"an agent named {name!r} already exists")
        return await asyncio.to_thread(services.agents.save, name, values)
    except AgentError as exc:
        raise _error(exc) from None


@router.put("/api/agents/{name}")
async def update_agent(name: str, body: AgentIn, _: AdminDep, services: ServicesDep) -> dict[str, Any]:
    """Change an agent."""
    values = body.model_dump(exclude_unset=True)
    values.pop("name", None)
    try:
        await asyncio.to_thread(services.agents.get, name)
        return await asyncio.to_thread(services.agents.save, name, values)
    except AgentError as exc:
        raise _error(exc) from None


@router.delete("/api/agents/{name}")
async def delete_agent(name: str, _: AdminDep, services: ServicesDep) -> Ok:
    """Delete an agent."""
    try:
        await asyncio.to_thread(services.agents.delete, name)
    except AgentError as exc:
        raise _error(exc) from None
    return Ok()


@router.post("/api/agents/{name}/run")
async def run_agent(name: str, body: RunIn, _: AdminDep, services: ServicesDep) -> dict[str, Any]:
    """Run a test task and wait for the answer; the dashboard acts as a web chat that delegates."""
    caller = ToolContext(services, "web:agents-test", "web", False)
    try:
        return await services.agents.run(name, body.task, body.context, caller)
    except AgentError as exc:
        raise _error(exc) from None


@router.get("/api/agents/{name}/runs")
async def agent_runs(name: str, _: AdminDep, services: ServicesDep) -> list[dict[str, Any]]:
    """Recent runs with their output."""
    try:
        await asyncio.to_thread(services.agents.get, name)
    except AgentError as exc:
        raise _error(exc) from None
    return await asyncio.to_thread(services.store.agent_runs, name, 20)
