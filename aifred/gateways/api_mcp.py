"""Dashboard API for MCP servers (spec 9.2, 10.5)."""

from __future__ import annotations

import asyncio
from typing import Any

import html

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

from aifred.gateways.api import AdminDep, Ok, ServicesDep
from aifred.instructions import InstructionError
from aifred.mcp import McpConfigError
from aifred.mcp_oauth import CALLBACK_PATH, MANUAL_REDIRECT, OAuthError, pasted_answer
from aifred.vault import VaultError

router = APIRouter()


class OAuthStartIn(BaseModel):
    """``manual``: use the address every provider accepts and copy the answer back (see ``oauth_complete``)."""

    manual: bool = False


class OAuthCompleteIn(BaseModel):
    url: str = Field(max_length=4096)


class McpServerIn(BaseModel):
    """Server fields; omitted fields keep their value on update.

    ``headers`` replaces the whole set: a value of ``null`` keeps the stored value of that header.
    """

    url: str | None = None
    command: str | list[str] | None = None   # a stdio server: the program and its arguments (needs MCP_STDIO_COMMANDS)
    enabled: bool | None = None
    oauth: bool | None = None   # sign in with OAuth in the browser (Authorize on the MCP page)
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
        current = services.mcp.get(name)
        wants_oauth = data["oauth"] if "oauth" in data else bool(current and current.oauth)
        signed_in = services.mcp.oauth.status(name)["authorized"]
        if not body.skip_test and data.get("enabled", True) and not (wants_oauth and not signed_in):    # a server that still needs signing in cannot be tested yet
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


# OAuth sign-in


def _redirect_uri(request: Request, services: Any) -> str:
    public = str(services.config.get("MCP_OAUTH_PUBLIC_URL") or "").strip().rstrip("/")
    return f"{public}{CALLBACK_PATH}" if public else f"{str(request.base_url).rstrip('/')}{CALLBACK_PATH}"


@router.post("/api/mcp/servers/{name}/oauth/start")
async def oauth_start(name: str, request: Request, _: AdminDep, services: ServicesDep, body: OAuthStartIn | None = None) -> dict[str, str]:
    """Begin signing in to a server: returns the address of its authorization page, to open in the browser.

    With ``manual`` the provider is told to return to an address the browser cannot open; the admin then pastes the
    address of the error page into ``/api/mcp/oauth/complete``.
    """
    server = services.mcp.get(name)
    if server is None or not server.oauth:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such server, or it is not set to use OAuth")
    try:
        redirect = MANUAL_REDIRECT if body and body.manual else _redirect_uri(request, services)
        url = await asyncio.to_thread(services.mcp.oauth.begin, name, server.url, redirect)
    except OAuthError as exc:
        raise _unprocessable(exc) from None
    return {"url": url}


@router.get("/api/mcp/oauth/callback")
async def oauth_callback(_: AdminDep, services: ServicesDep, state: str = "", code: str = "", error: str = "", error_description: str = "") -> Any:
    """Where the authorization server sends the browser back; completes the sign-in and returns to the MCP page."""
    def page(message: str, ok: bool = False) -> HTMLResponse:
        title = "Signed in" if ok else "Sign-in failed"
        return HTMLResponse(f"<!doctype html><meta charset=utf-8><title>{title}</title><body style='font-family:sans-serif;max-width:40em;margin:3em auto'>"
                            f"<h1>{title}</h1><p>{html.escape(message)}</p><p><a href='/#/mcp'>Back to AIFRED</a></p>", status_code=200 if ok else 400)

    if error:
        return page(f"The authorization server said: {error_description or error}")
    if not state or not code:
        return page("The answer from the authorization server is incomplete.")
    try:
        name = await asyncio.to_thread(services.mcp.oauth.finish, state, code)
    except OAuthError as exc:
        return page(str(exc))
    services.mcp_manager.reload()
    services.store.journal_add("settings", "mcp_server", f"MCP server {name!r} signed in with OAuth")
    return RedirectResponse("/#/mcp", status_code=303)


@router.post("/api/mcp/oauth/complete")
async def oauth_complete(body: OAuthCompleteIn, _: AdminDep, services: ServicesDep) -> dict[str, str]:
    """Finish a sign-in begun with ``manual``: the admin pastes the address the browser ended up on."""
    try:
        state, code = pasted_answer(body.url)
        name = await asyncio.to_thread(services.mcp.oauth.finish, state, code)
    except OAuthError as exc:
        raise _unprocessable(exc) from None
    services.mcp_manager.reload()
    services.store.journal_add("settings", "mcp_server", f"MCP server {name!r} signed in with OAuth")
    return {"name": name}


@router.post("/api/mcp/servers/{name}/oauth/forget")
def oauth_forget(name: str, _: AdminDep, services: ServicesDep) -> Ok:
    """Drop the stored tokens of a server; it has to be authorized again."""
    if services.mcp.get(name) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such server")
    services.mcp.oauth.forget(name)
    services.mcp_manager.reload()
    return Ok()


@router.get("/api/mcp/oauth/client.json")
def oauth_client_metadata(services: ServicesDep) -> dict[str, Any]:
    """AIFRED's client ID metadata document, which an authorization server fetches by its address (public on purpose)."""
    document = services.mcp.oauth.client_metadata()
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no public address is configured (MCP_OAUTH_PUBLIC_URL)")
    return document
