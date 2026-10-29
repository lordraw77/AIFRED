"""A fake OAuth authorization server on a real socket: metadata, an authorize endpoint that redirects at once, PKCE, refresh."""

from __future__ import annotations

import base64
import hashlib
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlencode

import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse


@dataclass
class FakeOAuth:
    """Configure a field, then ``start()``.

    Attributes:
        expires_in: Lifetime in seconds of the access tokens it issues.
        cimd: Whether the metadata says client ID metadata documents are accepted.
        pkce: Whether S256 is advertised.
        fail_token: Make the token endpoint refuse every request.
        require_secret: When set, the token endpoint refuses a request that does not carry this ``client_secret``.
        access_tokens: Valid access tokens.
        authorize_queries: Query parameters of every authorize request.
        token_forms: Form of every token request.
    """

    expires_in: int = 3600
    cimd: bool = True
    pkce: bool = True
    fail_token: bool = False
    require_secret: str = ""
    access_tokens: set[str] = field(default_factory=set)
    authorize_queries: list[dict[str, str]] = field(default_factory=list)
    token_forms: list[dict[str, str]] = field(default_factory=list)
    port: int = 0
    _codes: dict[str, dict[str, str]] = field(default_factory=dict)
    _refresh: dict[str, str] = field(default_factory=dict)
    _issued: int = 0
    _server: uvicorn.Server | None = None
    _thread: threading.Thread | None = None

    @property
    def issuer(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def _issue(self, client_id: str) -> dict[str, Any]:
        self._issued += 1
        access, refresh = f"at-{self._issued}", f"rt-{self._issued}"
        self.access_tokens.add(access)
        self._refresh[refresh] = client_id
        return {"access_token": access, "refresh_token": refresh, "token_type": "Bearer", "expires_in": self.expires_in, "scope": "mcp"}

    def _build(self) -> FastAPI:
        app = FastAPI()

        @app.get("/.well-known/oauth-authorization-server")
        async def metadata() -> dict[str, Any]:
            return {
                "issuer": self.issuer, "authorization_endpoint": f"{self.issuer}/authorize", "token_endpoint": f"{self.issuer}/token",
                "code_challenge_methods_supported": ["S256"] if self.pkce else [], "client_id_metadata_document_supported": self.cimd,
            }

        @app.get("/authorize")
        async def authorize(request: Request) -> Response:
            query = dict(request.query_params)
            self.authorize_queries.append(query)
            code = secrets.token_urlsafe(8)
            self._codes[code] = query
            return RedirectResponse(f"{query['redirect_uri']}?{urlencode({'code': code, 'state': query['state']})}", status_code=302)

        @app.post("/token")
        async def token(request: Request) -> Response:
            form = {k: v[0] for k, v in parse_qs((await request.body()).decode()).items()}
            self.token_forms.append(form)
            if self.require_secret and form.get("client_secret") != self.require_secret:
                return JSONResponse({"error": "invalid_client", "error_description": "client_secret is missing"}, status_code=401)
            if self.fail_token:
                return JSONResponse({"error": "invalid_grant", "error_description": "refused on purpose"}, status_code=400)
            if form.get("grant_type") == "authorization_code":
                original = self._codes.pop(form.get("code", ""), None)
                challenge = base64.urlsafe_b64encode(hashlib.sha256(form.get("code_verifier", "").encode()).digest()).rstrip(b"=").decode()
                if not original or original["code_challenge"] != challenge or form.get("redirect_uri") != original["redirect_uri"] or form.get("client_id") != original["client_id"]:
                    return JSONResponse({"error": "invalid_grant"}, status_code=400)
                return JSONResponse(self._issue(form["client_id"]))
            if form.get("grant_type") == "refresh_token" and form.get("refresh_token") in self._refresh:
                self._refresh.pop(form["refresh_token"])
                return JSONResponse(self._issue(form["client_id"]))
            return JSONResponse({"error": "invalid_grant"}, status_code=400)

        return app

    def start(self) -> FakeOAuth:
        config = uvicorn.Config(self._build(), host="127.0.0.1", port=0, log_level="error", lifespan="off")
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        deadline = time.time() + 10
        while not self._server.started:
            if time.time() > deadline:
                raise RuntimeError("fake OAuth server did not start")
            time.sleep(0.01)
        self.port = self._server.servers[0].sockets[0].getsockname()[1]
        return self

    def stop(self) -> None:
        if self._server:
            self._server.should_exit = True
        if self._thread:
            self._thread.join(timeout=5)
