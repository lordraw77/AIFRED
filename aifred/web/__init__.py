"""The web dashboard: a single page of plain HTML, CSS and JS with no build step (spec 10.4)."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

STATIC = Path(__file__).parent / "static"

CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; "
    "connect-src 'self'; manifest-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)
"""No inline script or style and nothing from other origins: an injected tag could not run or phone home."""


class SecurityHeaders:
    """Pure ASGI middleware adding headers to every response (it never buffers, so SSE streams are untouched)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope["path"]

        async def wrapped(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("X-Content-Type-Options", "nosniff")
                headers.setdefault("Referrer-Policy", "no-referrer")
                headers.setdefault("X-Frame-Options", "DENY")
                if path == "/" or path.startswith("/static/"):
                    headers.setdefault("Content-Security-Policy", CSP)
                if path.startswith("/static/"):
                    headers.setdefault("Cache-Control", "no-cache")  # always revalidate (ETag): no stale page after an update
                if path.startswith("/api/"):
                    headers.setdefault("Cache-Control", "no-store")
            await send(message)

        await self.app(scope, receive, wrapped)


def mount(app: FastAPI) -> None:
    """Serve the dashboard at ``/`` and its assets under ``/static``."""
    app.add_middleware(SecurityHeaders)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html", media_type="text/html", headers={"Cache-Control": "no-cache"})

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> FileResponse:
        return FileResponse(STATIC / "brand" / "favicon.ico", media_type="image/x-icon")
