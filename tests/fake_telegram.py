"""A scriptable fake Telegram Bot API on a real socket (same approach as the fake LLM)."""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

TOKEN = "123456:TEST-TOKEN-abc"


@dataclass
class FakeTelegram:
    """Records every call; ``push`` queues an update for the next ``getUpdates``.

    Attributes:
        sent: ``(chat_id, text)`` for every ``sendMessage``.
        actions: ``(chat_id, action)`` for every ``sendChatAction``.
        offsets: The ``offset`` of every ``getUpdates``.
        failures: Queued ``(status, body)`` answers for upcoming ``getUpdates`` calls.
    """

    token: str = TOKEN
    sent: list[tuple[int, str]] = field(default_factory=list)
    actions: list[tuple[int, str]] = field(default_factory=list)
    offsets: list[int] = field(default_factory=list)
    failures: list[tuple[int, dict[str, Any]]] = field(default_factory=list)
    port: int = 0
    _updates: list[dict[str, Any]] = field(default_factory=list)
    _next: int = 1
    _server: uvicorn.Server | None = None
    _thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def push(self, text: str | None, *, user: int = 1, chat: int | None = None, kind: str = "private", **extra: Any) -> None:
        message: dict[str, Any] = {"message_id": self._next, "from": {"id": user}, "chat": {"id": chat if chat is not None else user, "type": kind}}
        if text is not None:
            message["text"] = text
        message.update(extra)
        self._updates.append({"update_id": self._next, "message": message})
        self._next += 1

    def texts(self, chat: int | None = None) -> list[str]:
        return [t for c, t in self.sent if chat is None or c == chat]

    def start(self) -> FakeTelegram:
        app = FastAPI()

        @app.post("/bot{token}/{method}")
        async def call(token: str, method: str, request: Request) -> JSONResponse:
            if token != self.token:
                return JSONResponse({"ok": False, "description": f"Unauthorized: bot{token}"}, status_code=401)
            body = await request.json()
            if method == "getMe":
                return JSONResponse({"ok": True, "result": {"username": "aifred_bot"}})
            if method == "sendMessage":
                self.sent.append((body["chat_id"], body["text"]))
                return JSONResponse({"ok": True, "result": {}})
            if method == "sendChatAction":
                self.actions.append((body["chat_id"], body["action"]))
                return JSONResponse({"ok": True, "result": True})
            if method == "getUpdates":
                self.offsets.append(body["offset"])
                if self.failures:
                    status, payload = self.failures.pop(0)
                    return JSONResponse(payload, status_code=status)
                deadline = time.monotonic() + min(body.get("timeout", 0), 0.3)
                while True:
                    ready = [u for u in self._updates if u["update_id"] >= body["offset"]]
                    if ready or time.monotonic() > deadline:
                        return JSONResponse({"ok": True, "result": ready})
                    await asyncio.sleep(0.02)
            return JSONResponse({"ok": False, "description": "unknown method"}, status_code=404)

        config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error", lifespan="off")
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        deadline = time.time() + 10
        while not self._server.started:
            if time.time() > deadline:
                raise RuntimeError("fake Telegram did not start")
            time.sleep(0.01)
        self.port = self._server.servers[0].sockets[0].getsockname()[1]
        return self

    def stop(self) -> None:
        if self._server:
            self._server.should_exit = True
        if self._thread:
            self._thread.join(timeout=10)
