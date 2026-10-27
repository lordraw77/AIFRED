"""Dashboard API for MCP servers (spec 9.2, 10.5)."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from aifred.gateways.api import AdminDep, Ok, ServicesDep
from aifred.instructions import InstructionError
from aifred.mcp import McpConfigError
from aifred.vault import VaultError

router = APIRouter()


class McpServerIn(BaseModel):
    """Server fields; omitted fields keep their value on update.

    ``headers`` replaces the whole set: a value of ``null`` keeps the stored value of that header.
    """

    url: str | None = None
    command: str | list[str] | None = None   # a stdio server: the program and its arguments (needs MCP_STDIO_COMMANDS)
    enabled: bool | None = None
    headers: dict[str, str | None] | None = None
    verify_tls: bool | str | None = None
    timeout: float | None = None
    connect_timeout: float | None = None
    protocol_version: str | None = None
    include: list[str] | None = None
    exclude: list[str] | None = None
    max_tools: int | None = None
    instruction: str | None = None
    skip_test: bool = False

    def fields(self) -> dict[str, Any]:
        """Only the fields the client sent."""
        data = self.model_dump(exclude_unset=True)
        data.pop("skip_test", None)
        data.pop("instruction", None)
        return data


def _unprocessable(exc: Exception) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))


@router.get("/api/mcp/servers")
def list_servers(_: AdminDep, services: ServicesDep) -> list[dict[str, Any]]:
    """Servers with their live state; header values are never returned."""
    live = {s["name"]: s for s in services.mcp_manager.status()}
    return [live.get(s.name) or {**services.mcp.public(s), "state": "connecting", "error": "", "protocol": "", "tools": 0, "latency_ms": None, "dropped": []}
            for s in services.mcp.list()]


@router.put("/api/mcp/servers/{name}")
async def save_server(name: str, body: McpServerIn, _: AdminDep, services: ServicesDep) -> dict[str, Any]:
    """Create or update a server; the connection is tested first unless ``skip_test`` is set."""
    data = body.fields()
    text = (body.instruction or "").strip()
    try:
        if not text and services.mcp.get(name) is None and not services.instructions.get(f"mcp:{name}"):
            raise McpConfigError("What is this for? is required: write when the agent should use this server's tools")
        if text:
            services.instructions.check(f"mcp:{name}", text)
        if not body.skip_test and data.get("enabled", True):
            server, headers = services.mcp.preview(name, data)
            result = await asyncio.to_thread(services.mcp_manager.test, server, headers)
            if not result["ok"]:
                raise McpConfigError(f"connection test failed: {result['error']}")
        server = services.mcp.save(name, data)
        if text:
            services.instructions.set(f"mcp:{name}", text)
            services.agent.reset_all_prompts()
    except (McpConfigError, InstructionError) as exc:
        raise _unprocessable(exc) from None
    except VaultError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"the secret store is unavailable: {exc}") from None
    services.mcp_manager.reload()
    return services.mcp.public(server)


@router.delete("/api/mcp/servers/{name}")
def delete_server(name: str, _: AdminDep, services: ServicesDep) -> Ok:
    """Remove a server and its header values."""
    try:
        services.mcp.delete(name)
    except McpConfigError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None
    services.mcp_manager.reload()
    return Ok()


@router.post("/api/mcp/servers/{name}/test")
async def test_server(name: str, _: AdminDep, services: ServicesDep) -> dict[str, Any]:
    """Connect to a saved server with a throw-away client."""
    server = services.mcp.get(name)
    if server is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no MCP server named {name!r}")
    try:
        headers = await asyncio.to_thread(services.mcp.headers, name)
    except VaultError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"the secret store is unavailable: {exc}") from None
    return await asyncio.to_thread(services.mcp_manager.test, server, headers)


@router.post("/api/mcp/reload")
def reload_servers(_: AdminDep, services: ServicesDep) -> Ok:
    """Re-read the definitions and reconnect to changed servers."""
    services.mcp_manager.reload()
    return Ok()


@router.get("/api/mcp/export")
def export_servers(_: AdminDep, services: ServicesDep) -> dict[str, Any]:
    """The import format without header values."""
    return services.mcp.export()
