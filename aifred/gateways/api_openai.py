"""OpenAI-compatible API: ``/v1/models`` and ``/v1/chat/completions`` (spec 10.3).

Stateful by design: the server owns the conversation. Only the last ``user`` message of a request is
used; earlier messages and the client's ``tools`` are ignored. The session is the ``X-AIFRED-Session``
header, else the ``user`` field, else ``default``, always stored as ``api:<name>``.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, ValidationError

from aifred.agent import DoneEvent, ErrorEvent, Event, TokenEvent
from aifred.auth import ApiKey
from aifred.gateways.api import ServicesDep
from aifred.router import NoProvider, RouteError

router = APIRouter(prefix="/v1")

SESSION_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
MAX_MESSAGE_CHARS = 100_000
KEEPALIVE_SECONDS = 15.0
_END = object()


class OpenAIError(Exception):
    """An error rendered in OpenAI's format (``{"error": {...}}``).

    Attributes:
        status: HTTP status.
        message: Human-readable message.
        kind: OpenAI error ``type``.
        code: OpenAI error ``code``.
    """

    def __init__(self, status: int, message: str, kind: str = "invalid_request_error", code: str | None = None, headers: dict[str, str] | None = None) -> None:
        super().__init__(message)
        self.status, self.message, self.kind, self.code, self.headers = status, message, kind, code, headers or {}


def openai_error_response(_: Request, exc: Exception) -> JSONResponse:
    """Exception handler registered by the application."""
    assert isinstance(exc, OpenAIError)
    return JSONResponse(
        {"error": {"message": exc.message, "type": exc.kind, "param": None, "code": exc.code}}, status_code=exc.status, headers=exc.headers
    )


def require_key(request: Request, services: ServicesDep) -> ApiKey:
    """Authenticate with an API key of scope ``chat`` or ``admin`` (cookies are not accepted here)."""
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    key = services.auth.api_key(token.strip()) if scheme.lower() == "bearer" and token.strip() else None
    if key is None:
        raise OpenAIError(401, "Incorrect API key provided.", "invalid_request_error", "invalid_api_key", {"WWW-Authenticate": "Bearer"})
    return key


ApiKeyDep = Annotated[ApiKey, Depends(require_key)]


class ChatMessage(BaseModel):
    """A message of the request; only the last ``user`` one is used."""

    model_config = ConfigDict(extra="ignore")
    role: str
    content: str | list[dict[str, Any]] | None = None


class ChatRequest(BaseModel):
    """The parts of a chat-completions request that AIFRED honours; everything else is ignored."""

    model_config = ConfigDict(extra="ignore")
    model: str | None = None
    messages: list[ChatMessage]
    stream: bool = False
    stream_options: dict[str, Any] | None = None
    user: str | None = None
    n: int | None = 1


def last_user_text(messages: list[ChatMessage]) -> str:
    """The text of the last ``user`` message (text parts are joined; images are refused)."""
    for message in reversed(messages):
        if message.role != "user":
            continue
        content = message.content
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            parts = []
            for part in content:
                if part.get("type") in ("text", "input_text"):
                    parts.append(str(part.get("text", "")))
                else:
                    raise OpenAIError(400, f"Content parts of type {part.get('type')!r} are not supported; send text only.", code="unsupported_content")
            text = "\n".join(parts)
        else:
            text = ""
        if not text.strip():
            raise OpenAIError(400, "The last user message is empty.")
        if len(text) > MAX_MESSAGE_CHARS:
            raise OpenAIError(400, f"The message is longer than {MAX_MESSAGE_CHARS:,} characters.", code="string_above_max_length")
        return text
    raise OpenAIError(400, "The request needs at least one message with role 'user'.", code="missing_user_message")


def session_name(request: Request, body: ChatRequest) -> str:
    """The session name from the header, the ``user`` field or ``default``, validated."""
    name = request.headers.get("x-aifred-session") or body.user or "default"
    if not SESSION_NAME_RE.match(name):
        raise OpenAIError(400, "The session name may contain letters, digits, '.', '_' and '-' (1 to 64 characters).", code="invalid_session")
    return name


def _error_for(event: ErrorEvent) -> OpenAIError:
    if event.code == "no_provider":
        return OpenAIError(503, event.message, "server_error", "no_provider")
    if event.code == "route":
        return OpenAIError(404, event.message, code="model_not_found")
    if event.code == "empty":
        return OpenAIError(400, event.message)
    return OpenAIError(502, event.message, "server_error", "upstream_error")


async def _pump(events: AsyncIterator[Event], queue: asyncio.Queue[Any]) -> None:
    try:
        async for event in events:
            await queue.put(event)
    finally:
        await queue.put(_END)


def _sse(data: Any) -> bytes:
    return b"data: " + (data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode()) + b"\n\n"


@router.get("/models")
async def list_models(_: ApiKeyDep, services: ServicesDep) -> dict[str, Any]:
    """Routes and tested chat models, in OpenAI's list format."""
    def build() -> list[str]:
        ids = [n for n in services.store.route_names() if n != services.config.get("RAG_VISION_ROUTE")]
        for provider in services.providers.list():
            ids += [f"{provider.slug}/{m['model_id']}" for m in services.providers.models(provider.id) if m["kind"] == "chat" and m["test_ok"]]
        return ids

    created = int(time.time())
    return {"object": "list", "data": [{"id": i, "object": "model", "created": created, "owned_by": "aifred"} for i in await asyncio.to_thread(build)]}


@router.post("/chat/completions", response_model=None)
async def chat_completions(request: Request, _: ApiKeyDep, services: ServicesDep) -> Any:
    """Run one turn of the agent and answer in OpenAI's format, streamed or not."""
    try:
        raw = await request.json()
        body = ChatRequest.model_validate(raw)
    except (ValueError, ValidationError) as exc:
        detail = exc.errors()[0]["msg"] if isinstance(exc, ValidationError) and exc.errors() else "the body must be a JSON object"
        raise OpenAIError(400, f"Invalid request: {detail}.") from None
    if body.n not in (None, 1):
        raise OpenAIError(400, "Only n=1 is supported.", code="unsupported_parameter")
    text = last_user_text(body.messages)
    session = f"api:{session_name(request, body)}"
    route = (body.model or "").strip()
    if route in ("", "aifred"):
        route = ""
    elif route != "default":
        try:
            await asyncio.to_thread(services.llm.resolve, route, "default")
        except NoProvider as exc:
            raise OpenAIError(503, str(exc), "server_error", "no_provider") from None
        except RouteError as exc:
            raise OpenAIError(404, f"The model {route!r} does not exist. {exc}", code="model_not_found") from None

    events = services.agent.run(session, text, "api", route=route or None)
    queue: asyncio.Queue[Any] = asyncio.Queue()
    task = asyncio.create_task(_pump(events, queue))
    # Wait a little for the first event so that a quick failure still gets a proper HTTP status; if the model needs
    # longer (tool rounds before the first token), commit to a stream and keep it alive from then on.
    try:
        first: Any = await asyncio.wait_for(queue.get(), KEEPALIVE_SECONDS)
    except asyncio.TimeoutError:
        first = None
    if isinstance(first, ErrorEvent):
        task.cancel()
        raise _error_for(first)
    completion_id, created, model = f"chatcmpl-{uuid.uuid4().hex[:24]}", int(time.time()), route or "aifred"

    if not body.stream:
        try:
            item = first if first is not None else await queue.get()
            content, usage, served = "", {}, ""
            while item is not _END:
                if isinstance(item, ErrorEvent):
                    raise _error_for(item)
                if isinstance(item, DoneEvent):
                    content, usage, served = item.content, item.usage, item.model
                item = await queue.get()
        finally:
            task.cancel()
        total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0} | usage
        return {
            "id": completion_id, "object": "chat.completion", "created": created, "model": served or model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
            "usage": total,
        }

    def chunk(delta: dict[str, Any], finish: str | None = None) -> dict[str, Any]:
        return {"id": completion_id, "object": "chat.completion.chunk", "created": created, "model": model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}

    include_usage = bool((body.stream_options or {}).get("include_usage"))

    async def stream() -> AsyncIterator[bytes]:
        try:
            yield _sse(chunk({"role": "assistant", "content": ""}))
            item = first
            while True:
                while item is None:  # nothing yet: tools may run for a while, so keep the connection alive meanwhile
                    try:
                        item = await asyncio.wait_for(queue.get(), KEEPALIVE_SECONDS)
                    except asyncio.TimeoutError:
                        yield b": keep-alive\n\n"
                if item is _END:
                    break
                if isinstance(item, TokenEvent):
                    yield _sse(chunk({"content": item.text}))
                elif isinstance(item, ErrorEvent):
                    # a failure after the response began cannot change the status code: end with an error chunk
                    err = _error_for(item)
                    yield _sse({"error": {"message": err.message, "type": err.kind, "param": None, "code": err.code}})
                    break
                elif isinstance(item, DoneEvent):
                    yield _sse(chunk({}, "stop"))
                    if include_usage:
                        yield _sse({"id": completion_id, "object": "chat.completion.chunk", "created": created, "model": item.model or model,
                                    "choices": [], "usage": item.usage})
                item = None
            yield b"data: [DONE]\n\n"
        finally:
            task.cancel()

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
