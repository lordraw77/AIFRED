"""Dashboard API for instructions: what MCP servers, toolsets and tools are for (spec 9.3)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from aifred.gateways.api import AdminDep, Ok, ServicesDep
from aifred.instructions import InstructionError
from aifred.vault import VaultError

router = APIRouter()


class InstructionIn(BaseModel):
    """An instruction text; ``enabled`` false keeps it but leaves it out of the prompt."""

    text: str = Field(min_length=1, max_length=4000)
    enabled: bool = True


class ServerDraft(BaseModel):
    """A server that is not saved yet, to draft its instruction from the tools it offers."""

    name: str
    url: str
    headers: dict[str, str | None] | None = None
    verify_tls: bool | str | None = None
    protocol_version: str | None = None


class SuggestIn(BaseModel):
    """What to draft an instruction for."""

    scope: str
    hint: str = Field(default="", max_length=500)
    server: ServerDraft | None = None


def _bad(exc: Exception, code: int = status.HTTP_422_UNPROCESSABLE_CONTENT) -> HTTPException:
    return HTTPException(code, str(exc))


@router.get("/api/instructions")
def overview(_: AdminDep, services: ServicesDep) -> dict[str, Any]:
    """Every MCP server and toolset with its tools, stored text, the undocumented flag and a preview of the Capabilities section."""
    return services.instructions.overview()


@router.post("/api/instructions/suggest")
async def suggest(body: SuggestIn, _: AdminDep, services: ServicesDep) -> dict[str, Any]:
    """Draft an instruction with the model; the draft is only returned, never saved."""
    import asyncio

    tools = None
    try:
        if body.server is not None:
            data = {k: v for k, v in body.server.model_dump().items() if k != "name" and v is not None}
            server, headers = services.mcp.preview(body.server.name, data)
            result = await asyncio.to_thread(services.mcp_manager.test, server, headers)
            if not result["ok"]:
                raise InstructionError(f"cannot reach the server to read its tools: {result['error']}")
            tools = result["tool_info"]
        return await services.instructions.suggest(body.scope, body.hint, tools)
    except InstructionError as exc:
        raise _bad(exc) from None
    except VaultError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"the secret store is unavailable: {exc}") from None
    except ValueError as exc:  # McpConfigError from the draft's fields
        raise _bad(exc) from None


@router.post("/api/instructions/reload")
def reload_prompts(_: AdminDep, services: ServicesDep) -> Ok:
    """Rebuild the cached system prompts so the next turn sees the current instructions."""
    services.agent.reset_all_prompts()
    return Ok()


@router.get("/api/instructions/{scope:path}")
def get_instruction(scope: str, _: AdminDep, services: ServicesDep) -> dict[str, Any]:
    """One stored instruction."""
    try:
        services.instructions.check(scope)
    except InstructionError as exc:
        raise _bad(exc) from None
    row = services.instructions.get(scope)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no instruction for {scope}")
    return row


@router.put("/api/instructions/{scope:path}")
def put_instruction(scope: str, body: InstructionIn, _: AdminDep, services: ServicesDep) -> dict[str, Any]:
    """Create or replace an instruction (journaled; undo from the Journey page)."""
    try:
        services.instructions.set(scope, body.text, enabled=body.enabled)
    except InstructionError as exc:
        raise _bad(exc) from None
    services.agent.reset_all_prompts()
    return services.instructions.get(scope) or {}


@router.delete("/api/instructions/{scope:path}")
def delete_instruction(scope: Annotated[str, "scope"], _: AdminDep, services: ServicesDep) -> Ok:
    """Remove an instruction."""
    try:
        removed = services.instructions.delete(scope)
    except InstructionError as exc:
        raise _bad(exc) from None
    if not removed:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no instruction for {scope}")
    services.agent.reset_all_prompts()
    return Ok()
