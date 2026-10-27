"""A scriptable fake OpenAI-compatible LLM server.

It runs on a real socket (uvicorn in a thread) so streaming, stalls and broken
connections behave as with a real provider. Queue :class:`Step` objects with
:meth:`FakeLLM.queue`; each chat request consumes one step, defaulting to a short
text answer.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse


@dataclass
class Text:
    """Answer with text split into ``chunks`` pieces."""

    text: str = "ok"
    chunks: int = 2
    usage: tuple[int, int] | None = (10, 5)
    pieces: list[str] | None = None


@dataclass
class ToolCall:
    """Answer with a tool call whose arguments arrive in two pieces."""

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    id: str | None = "call_abc123xyz"
    usage: tuple[int, int] = (12, 7)


@dataclass
class Status:
    """Fail with an HTTP error."""

    code: int
    body: Any = None
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class Silence:
    """Send headers, then stay silent for ``seconds`` before any data."""

    seconds: float = 5.0


@dataclass
class StallAfter:
    """Send some text, then stay silent."""

    text: str = "partial"
    seconds: float = 5.0


@dataclass
class Break:
    """Send some text, then drop the connection."""

    text: str = "partial"


@dataclass
class StreamError:
    """Send an error object inside the SSE stream."""

    error: dict[str, Any]


@dataclass
class Reasoning:
    """Stream ``pieces`` reasoning-only chunks ``gap`` seconds apart, then answer ``then``."""

    pieces: int = 3
    gap: float = 0.2
    then: str = "ok"


Step = Reasoning | Text | ToolCall | Status | Silence | StallAfter | Break | StreamError


@dataclass
class Received:
    """A request the fake server saw."""

    body: dict[str, Any]
    headers: dict[str, str]


SYNONYMS = [
    ("car", "automobile", "vehicle", "auto", "macchina"),
    ("backup", "snapshot", "dump", "copia"),
    ("upgrade", "update", "aggiornamento", "aggiornare"),
    ("error", "failure", "fault", "errore"),
    ("fast", "quick", "rapid", "veloce"),
    ("disk", "storage", "drive", "disco"),
]
_CANONICAL = {word: group[0] for group in SYNONYMS for word in group}
_STOPWORDS = frozenset("the a an to of and or is are in on for with how do does i my what why when it this that be".split())


def fake_embedding(text: str, dim: int = 256) -> list[float]:
    """A deterministic bag-of-words embedding where synonyms share a dimension (L2-normalised)."""
    import hashlib
    import math
    import re

    vector = [0.0] * dim
    for word in re.findall(r"\w+", text.lower()):
        if word in _STOPWORDS:
            continue
        canonical = _CANONICAL.get(word, word)
        vector[int(hashlib.md5(canonical.encode()).hexdigest(), 16) % dim] += 1.0
    norm = math.sqrt(sum(v * v for v in vector))
    return [v / norm for v in vector] if norm else vector


def _sse(obj: Any) -> bytes:
    return b"data: " + (obj if isinstance(obj, bytes) else json.dumps(obj).encode()) + b"\n\n"


def _chunk(delta: dict[str, Any], finish: str | None = None) -> bytes:
    return _sse({"id": "x", "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]})


class FakeLLM:
    """Fake provider endpoint.

    Attributes:
        url: Base URL (``http://127.0.0.1:<port>/v1``).
        requests: Chat requests received, in order.
        models: Model list returned by ``GET /v1/models`` (and Ollama's ``/api/tags``).
        api_key: When set, requests must carry ``Authorization: Bearer <api_key>``.
    """

    def __init__(self) -> None:
        self.requests: list[Received] = []
        self.steps: list[Step] = []
        self.models: list[dict[str, Any]] = [{"id": "test-model"}]
        self.api_key: str | None = None
        self.models_status = 200
        self.always: Step | None = None
        self.embed_requests: list[Received] = []
        self.rerank_requests: list[Received] = []
        self.rerank_steps: list[Status] = []
        self.rerank_fn: Any = None
        self.embed_steps: list[Status] = []
        self.embed_dim = 256
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self.port = 0
        self.app = self._build()

    # Control

    def queue(self, *steps: Step) -> FakeLLM:
        """Append scripted answers."""
        self.steps.extend(steps)
        return self

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    @property
    def root(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> FakeLLM:
        config = uvicorn.Config(self.app, host="127.0.0.1", port=0, log_level="error", lifespan="off")
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        deadline = time.time() + 10
        while not self._server.started:
            if time.time() > deadline:
                raise RuntimeError("fake LLM did not start")
            time.sleep(0.01)
        self.port = self._server.servers[0].sockets[0].getsockname()[1]
        return self

    def stop(self) -> None:
        if self._server:
            self._server.should_exit = True
        if self._thread:
            self._thread.join(timeout=10)

    # Application

    def _authorised(self, request: Request) -> bool:
        return self.api_key is None or request.headers.get("authorization") == f"Bearer {self.api_key}"

    def _build(self) -> FastAPI:
        app = FastAPI()

        @app.get("/v1/models")
        async def models(request: Request) -> JSONResponse:
            if not self._authorised(request):
                return JSONResponse({"error": {"message": "Invalid API key"}}, status_code=401)
            return JSONResponse({"object": "list", "data": self.models}, status_code=self.models_status)

        @app.get("/api/tags")
        async def tags() -> JSONResponse:
            return JSONResponse({"models": [{"name": m["id"], "model": m["id"]} for m in self.models]})

        @app.post("/v1/chat/completions")
        async def chat(request: Request) -> Any:
            body = await request.json()
            self.requests.append(Received(body, {k.lower(): v for k, v in request.headers.items()}))
            if not self._authorised(request):
                return JSONResponse({"error": {"message": "Incorrect API key provided", "code": "invalid_api_key"}}, status_code=401)
            step: Step = self.steps.pop(0) if self.steps else (self.always or Text())
            if isinstance(step, Status):
                body_out = step.body if step.body is not None else {"error": {"message": f"status {step.code}"}}
                return JSONResponse(body_out, status_code=step.code, headers=step.headers)
            return StreamingResponse(self._stream(step), media_type="text/event-stream")

        @app.post("/v1/embeddings")
        async def embeddings(request: Request) -> Any:
            body = await request.json()
            self.embed_requests.append(Received(body, {k.lower(): v for k, v in request.headers.items()}))
            if not self._authorised(request):
                return JSONResponse({"error": {"message": "Incorrect API key provided"}}, status_code=401)
            if self.embed_steps:
                step = self.embed_steps.pop(0)
                body_out = step.body if step.body is not None else {"error": {"message": f"status {step.code}"}}
                return JSONResponse(body_out, status_code=step.code, headers=step.headers)
            inputs = body["input"] if isinstance(body["input"], list) else [body["input"]]
            tokens = sum(len(str(t).split()) for t in inputs)
            return JSONResponse(
                {
                    "object": "list", "model": body.get("model"),
                    "data": [{"object": "embedding", "index": i, "embedding": fake_embedding(str(t), self.embed_dim)} for i, t in enumerate(inputs)],
                    "usage": {"prompt_tokens": tokens, "total_tokens": tokens},
                }
            )

        @app.post("/v1/rerank")
        async def rerank(request: Request) -> Any:
            body = await request.json()
            self.rerank_requests.append(Received(body, {k.lower(): v for k, v in request.headers.items()}))
            if not self._authorised(request):
                return JSONResponse({"error": {"message": "Incorrect API key provided"}}, status_code=401)
            if self.rerank_steps:
                step = self.rerank_steps.pop(0)
                return JSONResponse(step.body if step.body is not None else {"error": {"message": f"status {step.code}"}}, status_code=step.code)
            words = set(str(body["query"]).lower().split())
            scores = [len(words & set(str(d).lower().split())) / (1 + len(str(d).split())) for d in body["documents"]]
            if self.rerank_fn is not None:
                scores = self.rerank_fn(body["query"], body["documents"])
            order = sorted(range(len(scores)), key=lambda i: -scores[i])[: body.get("top_n") or len(scores)]
            return JSONResponse({"results": [{"index": i, "relevance_score": scores[i]} for i in order]})

        return app

    async def _stream(self, step: Step) -> AsyncIterator[bytes]:
        if isinstance(step, Silence):
            await asyncio.sleep(step.seconds)
            yield _sse(b"[DONE]")
            return
        if isinstance(step, Reasoning):
            for _ in range(step.pieces):
                yield _chunk({"role": "assistant", "reasoning_content": "thinking..."})
                await asyncio.sleep(step.gap)
            yield _chunk({"content": step.then})
            yield _chunk({}, "stop")
            yield _sse(b"[DONE]")
            return
        if isinstance(step, StreamError):
            yield _sse({"error": step.error})
            return
        if isinstance(step, StallAfter):
            yield _chunk({"role": "assistant", "content": step.text})
            await asyncio.sleep(step.seconds)
            return
        if isinstance(step, Break):
            yield _chunk({"role": "assistant", "content": step.text})
            await asyncio.sleep(0.05)
            raise RuntimeError("connection dropped by the fake server")
        if isinstance(step, ToolCall):
            args = json.dumps(step.arguments)
            half = max(1, len(args) // 2)
            first: dict[str, Any] = {"index": 0, "type": "function", "function": {"name": step.name, "arguments": ""}}
            if step.id:
                first["id"] = step.id
            yield _chunk({"role": "assistant", "tool_calls": [first]})
            yield _chunk({"tool_calls": [{"index": 0, "function": {"arguments": args[:half]}}]})
            yield _chunk({"tool_calls": [{"index": 0, "function": {"arguments": args[half:]}}]})
            yield _chunk({}, "tool_calls")
            yield _sse({"choices": [], "usage": {"prompt_tokens": step.usage[0], "completion_tokens": step.usage[1]}})
            yield _sse(b"[DONE]")
            return
        assert isinstance(step, Text)
        pieces = step.pieces
        if pieces is None:
            size = max(1, -(-len(step.text) // step.chunks))
            pieces = [step.text[i : i + size] for i in range(0, len(step.text), size)] or [""]
        yield b": keep-alive\n\n"
        for i, piece in enumerate(pieces):
            delta: dict[str, Any] = {"content": piece}
            if i == 0:
                delta["role"] = "assistant"
            yield _chunk(delta)
            await asyncio.sleep(0)
        yield _chunk({"reasoning_content": "hidden reasoning"})
        yield _chunk({}, "stop")
        if step.usage:
            yield _sse({"choices": [], "usage": {"prompt_tokens": step.usage[0], "completion_tokens": step.usage[1]}})
        yield _sse(b"[DONE]")
