"""A fake OpenBao server (health, AppRole login, Transit) on a real socket.

Status codes mirror what a real OpenBao 2.4 answered when probed: 403 for a bad token or
a key the policy does not allow, 400 for a corrupt ciphertext, 503 while sealed.
"""

from __future__ import annotations

import base64
import os
import threading
import time
from collections import Counter
from typing import Any

import uvicorn
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class FakeOpenBao:
    """Scriptable OpenBao.

    Attributes:
        sealed: Whether the server answers 503.
        calls: Request counts by route.
        tokens: Currently valid client tokens.
        static_tokens: Tokens that are always valid until removed.
    """

    ROLE_ID, SECRET_ID, KEY = "role-1", "secret-1", "aifred"

    def __init__(self) -> None:
        self.sealed = False
        self.calls: Counter[str] = Counter()
        self.tokens: set[str] = set()
        self.static_tokens: set[str] = {"static-token"}
        self.logins = 0
        self._aead = AESGCM(AESGCM.generate_key(bit_length=256))
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self.port = 0
        self.app = self._build()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def expire_tokens(self) -> None:
        """Invalidate every token issued by AppRole."""
        self.tokens.clear()

    def start(self) -> FakeOpenBao:
        config = uvicorn.Config(self.app, host="127.0.0.1", port=0, log_level="error", lifespan="off")
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        deadline = time.time() + 10
        while not self._server.started:
            if time.time() > deadline:
                raise RuntimeError("fake OpenBao did not start")
            time.sleep(0.01)
        self.port = self._server.servers[0].sockets[0].getsockname()[1]
        return self

    def stop(self) -> None:
        if self._server:
            self._server.should_exit = True
        if self._thread:
            self._thread.join(timeout=10)

    def _sealed(self) -> JSONResponse:
        return JSONResponse({"errors": ["Vault is sealed"]}, status_code=503)

    def _authorised(self, request: Request, key: str) -> bool:
        token = request.headers.get("x-vault-token", "")
        return (token in self.tokens or token in self.static_tokens) and key == self.KEY

    def _build(self) -> FastAPI:
        app = FastAPI()

        @app.get("/v1/sys/health")
        async def health() -> JSONResponse:
            self.calls["health"] += 1
            if self.sealed:
                return JSONResponse({"initialized": True, "sealed": True}, status_code=503)
            return JSONResponse({"initialized": True, "sealed": False, "version": "fake"})

        @app.post("/v1/auth/approle/login")
        async def login(request: Request) -> Any:
            self.calls["login"] += 1
            if self.sealed:
                return self._sealed()
            body = await request.json()
            if (body.get("role_id"), body.get("secret_id")) != (self.ROLE_ID, self.SECRET_ID):
                return JSONResponse({"errors": ["invalid role or secret ID"]}, status_code=400)
            self.logins += 1
            token = f"s.{os.urandom(8).hex()}"
            self.tokens.add(token)
            return {"auth": {"client_token": token, "lease_duration": 3600, "renewable": True}}

        @app.post("/v1/transit/encrypt/{key}")
        async def encrypt(key: str, request: Request) -> Any:
            self.calls["encrypt"] += 1
            if self.sealed:
                return self._sealed()
            if not self._authorised(request, key):
                return JSONResponse({"errors": ["permission denied"]}, status_code=403)
            plaintext = base64.b64decode((await request.json())["plaintext"])
            nonce = os.urandom(12)
            blob = nonce + self._aead.encrypt(nonce, plaintext, None)
            return {"data": {"ciphertext": "vault:v1:" + base64.b64encode(blob).decode(), "key_version": 1}}

        @app.post("/v1/transit/decrypt/{key}")
        async def decrypt(key: str, request: Request) -> Any:
            self.calls["decrypt"] += 1
            if self.sealed:
                return self._sealed()
            if not self._authorised(request, key):
                return JSONResponse({"errors": ["permission denied"]}, status_code=403)
            ciphertext = (await request.json())["ciphertext"]
            try:
                raw = base64.b64decode(ciphertext.removeprefix("vault:v1:"))
                plain = self._aead.decrypt(raw[:12], raw[12:], None)
            except (InvalidTag, ValueError):
                return JSONResponse({"errors": ["invalid ciphertext length"]}, status_code=400)
            return {"data": {"plaintext": base64.b64encode(plain).decode()}}

        return app
