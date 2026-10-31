"""Dashboard API for instructions: what MCP servers, toolsets and tools are for (spec 9.3)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from aifred.gateways.api import AdminDep, Ok, ServicesDep
from aifred.instructions import InstructionError
from aifred.vault import VaultError

router = APIRouter()


class InstructionIn(BaseModel):
    """An instruction text; ``enabled`` false keeps it but leaves it out of the prompt."""

    text: str = Field(min_length=1, max_length=4000)
    enabled: bool = True
    score: str = Field(default="", max_length=20, pattern=r"^(\d{1,2}/\d{1,2})?$")   # "7/8": the evaluation score the text had when accepted


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
    mode: str = "generate"
    current: str = Field(default="", max_length=4000)
    use_memory: bool = False
    use_sessions: bool = False


class CaseIn(BaseModel):
    """One test request and what the assistant should do with it."""

    request: str = Field(min_length=1, max_length=300)
    expect: str = Field(pattern="^(this|other|none)$")


class GenerateIn(BaseModel):
    """What a new skill or sub-agent should do, in the admin's own words."""

    kind: str
    description: str = Field(min_length=10, max_length=1500)


class ImproveIn(BaseModel):
    """A sub-agent or skill text to draft, rewrite or shorten; ``context`` is what the draft may be based on."""

    kind: str
    mode: str = "improve"
    text: str = Field(default="", max_length=4000)
    context: str = Field(default="", max_length=3000)
    hint: str = Field(default="", max_length=500)


class EvaluateIn(BaseModel):
    """An instruction to test; ``cases`` are written when omitted and ``rounds`` refine it (0 to 3)."""

    scope: str
    text: str = Field(min_length=1, max_length=4000)
    cases: list[CaseIn] | None = Field(default=None, max_length=12)
    rounds: int = Field(default=0, ge=0, le=3)
    hint: str = Field(default="", max_length=500)


def _bad(exc: Exception, code: int = status.HTTP_422_UNPROCESSABLE_CONTENT) -> HTTPException:
    return HTTPException(code, str(exc))


@router.get("/api/instructions")
def overview(_: AdminDep, services: ServicesDep) -> dict[str, Any]:
    """Every MCP server and toolset with its tools, stored text, the undocumented flag and a preview of the Capabilities section."""
    return services.instructions.overview()


@router.post("/api/instructions/suggest")
async def suggest(body: SuggestIn, _: AdminDep, services: ServicesDep) -> dict[str, Any]:
    """Draft an instruction with the model; the draft is only returned, never saved."""
    tools = None
    try:
        if body.server is not None:
            data = {k: v for k, v in body.server.model_dump().items() if k != "name" and v is not None}
            server, headers = services.mcp.preview(body.server.name, data)
            result = await asyncio.to_thread(services.mcp_manager.test, server, headers)
            if not result["ok"]:
                raise InstructionError(f"cannot reach the server to read its tools: {result['error']}")
            tools = result["tool_info"]
        return await services.instructions.suggest(body.scope, body.hint, tools, mode=body.mode, current=body.current, use_memory=body.use_memory, use_sessions=body.use_sessions)
    except InstructionError as exc:
        raise _bad(exc) from None
    except VaultError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"the secret store is unavailable: {exc}") from None
    except ValueError as exc:  # McpConfigError from the draft's fields
        raise _bad(exc) from None


@router.post("/api/assist/evaluate")
async def evaluate(body: EvaluateIn, _: AdminDep, services: ServicesDep) -> dict[str, Any]:
    """Score an instruction by dry-running the model's tool choice (no tool is executed); optionally refine it. Nothing is saved."""
    try:
        return await services.assist.evaluate(body.scope, body.text, [c.model_dump() for c in body.cases] if body.cases else None, body.rounds, body.hint)
    except InstructionError as exc:
        raise _bad(exc) from None


@router.post("/api/assist/evaluate/stream")
async def evaluate_stream(body: EvaluateIn, _: AdminDep, services: ServicesDep) -> StreamingResponse:
    """Like ``/api/assist/evaluate`` but streams progress as server-sent events, ending with ``result`` or ``error``."""
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    async def work() -> None:
        try:
            result = await services.assist.evaluate(
                body.scope, body.text, [c.model_dump() for c in body.cases] if body.cases else None, body.rounds, body.hint, progress=queue.put_nowait,
            )
            queue.put_nowait({"step": "result", "result": result})
        except InstructionError as exc:
            queue.put_nowait({"step": "error", "message": str(exc)})
        finally:
            queue.put_nowait(None)

    async def stream() -> AsyncIterator[bytes]:
        task = asyncio.create_task(work())
        try:
            while (event := await queue.get()) is not None:
                yield b"data: " + json.dumps(event, ensure_ascii=False).encode() + b"\n\n"
        finally:
            task.cancel()

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/api/assist/improve")
async def improve(body: ImproveIn, _: AdminDep, services: ServicesDep) -> dict[str, Any]:
    """Draft, rewrite or shorten a sub-agent description or role, or a skill description. The draft is returned, never saved."""
    try:
        return await services.assist.improve(body.kind, body.mode, body.text, body.context, body.hint)
    except InstructionError as exc:
        raise _bad(exc) from None


@router.post("/api/assist/generate")
async def generate(body: GenerateIn, _: AdminDep, services: ServicesDep) -> dict[str, Any]:
    """Draft a whole skill or sub-agent from a description. The draft is returned, never saved: the editor opens with it filled in."""
    try:
        return await services.assist.generate(body.kind, body.description)
    except InstructionError as exc:
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
        services.instructions.set(scope, body.text, enabled=body.enabled, score=body.score)
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
