"""HTTP API: health, authentication, API keys and settings (spec 4.1, 4.3, 10.3, 10.5).

Every ``/api/*`` route requires either the ``aifred_session`` cookie (plus the
``X-CSRF-Token`` header on state-changing requests) or an ``admin`` API key as a
Bearer token. Only ``/health`` and ``/api/auth/login`` are unauthenticated.
"""

from __future__ import annotations

import hmac
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from aifred import __version__
from aifred.app import Services
from aifred.auth import ApiKey, AuthError, LoginSession, RateLimited
from aifred.config import SettingError

logger = logging.getLogger(__name__)

COOKIE_NAME = "aifred_session"
CSRF_HEADER = "X-CSRF-Token"
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

router = APIRouter()


def get_services(request: Request) -> Services:
    """Return the process services."""
    services: Services = request.app.state.services
    return services


ServicesDep = Annotated[Services, Depends(get_services)]


@dataclass(frozen=True)
class Principal:
    """The authenticated caller.

    Attributes:
        session: Login session when authenticated by cookie.
        api_key: API key when authenticated by Bearer token.
    """

    session: LoginSession | None = None
    api_key: ApiKey | None = None


def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    return token.strip() if scheme.lower() == "bearer" and token.strip() else None


def _set_session_cookie(request: Request, response: Response, token: str, expires: float) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=max(0, int(expires - time.time())),
        path="/",
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
    )


def require_admin(request: Request, response: Response, services: ServicesDep) -> Principal:
    """Authenticate an admin request (cookie with CSRF, or an ``admin`` API key).

    Raises:
        HTTPException: 401 when unauthenticated, 403 on a scope or CSRF failure.
    """
    token = _bearer(request)
    if token is not None:
        key = services.auth.api_key(token)
        if key is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid API key", {"WWW-Authenticate": "Bearer"})
        if key.scope != "admin":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "this API key has no admin scope")
        return Principal(api_key=key)

    cookie = request.cookies.get(COOKIE_NAME, "")
    session = services.auth.session(cookie)
    if session is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not authenticated")
    if request.method not in _SAFE_METHODS:
        sent = request.headers.get(CSRF_HEADER, "")
        if not hmac.compare_digest(sent.encode(), session.csrf_token.encode()):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "missing or invalid CSRF token")
    _set_session_cookie(request, response, cookie, session.expires)
    return Principal(session=session)


AdminDep = Annotated[Principal, Depends(require_admin)]


# Health


class Health(BaseModel):
    """Liveness response."""

    status: Literal["ok"] = "ok"
    version: str = __version__


@router.get("/health")
def health() -> Health:
    """Unauthenticated liveness check."""
    return Health()


# Authentication


class LoginRequest(BaseModel):
    """Login credentials."""

    username: str = Field(min_length=1, max_length=256)
    password: str = Field(min_length=1, max_length=1024)


class SessionInfo(BaseModel):
    """The current dashboard session."""

    username: str
    csrf_token: str
    expires: float


class PasswordChange(BaseModel):
    """Password change request."""

    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=1, max_length=1024)


class LoginSessionInfo(BaseModel):
    """An active login session."""

    id: str
    created: float
    last_seen: float
    expires: float
    ip: str | None
    user_agent: str | None
    current: bool


class Ok(BaseModel):
    """Generic success response."""

    ok: bool = True


@router.post("/api/auth/login")
def login(body: LoginRequest, request: Request, response: Response, services: ServicesDep) -> SessionInfo:
    """Check the admin credentials and issue the session cookie."""
    ip = request.client.host if request.client else None
    try:
        token, session = services.auth.login(body.username, body.password, ip, request.headers.get("user-agent"))
    except RateLimited as exc:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Too many login attempts. Try again in {exc.retry_after} seconds.",
            {"Retry-After": str(exc.retry_after)},
        ) from None
    except AuthError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid username or password") from None
    _set_session_cookie(request, response, token, session.expires)
    return SessionInfo(username=services.auth.username or "", csrf_token=session.csrf_token, expires=session.expires)


def _require_cookie_session(principal: Principal) -> LoginSession:
    if principal.session is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "this endpoint needs a dashboard session")
    return principal.session


@router.get("/api/auth/session")
def current_session(principal: AdminDep, services: ServicesDep) -> SessionInfo:
    """Return the current session and its CSRF token (used after a page reload)."""
    session = _require_cookie_session(principal)
    return SessionInfo(username=services.auth.username or "", csrf_token=session.csrf_token, expires=session.expires)


@router.post("/api/auth/logout")
def logout(principal: AdminDep, request: Request, response: Response, services: ServicesDep) -> Ok:
    """Invalidate the current session on the server and clear the cookie."""
    session = _require_cookie_session(principal)
    services.auth.logout(session.token_hash)
    response.delete_cookie(
        COOKIE_NAME, path="/", httponly=True, samesite="strict", secure=request.url.scheme == "https"
    )
    return Ok()


@router.post("/api/auth/password")
def change_password(body: PasswordChange, principal: AdminDep, services: ServicesDep) -> Ok:
    """Change the admin password; every other session is invalidated."""
    keep = principal.session.token_hash if principal.session else None
    try:
        services.auth.change_password(body.current_password, body.new_password, keep_session=keep)
    except AuthError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    return Ok()


@router.get("/api/auth/sessions")
def list_login_sessions(principal: AdminDep, services: ServicesDep) -> list[LoginSessionInfo]:
    """List active dashboard sessions."""
    current = principal.session.token_hash[:16] if principal.session else None
    return [LoginSessionInfo(**s, current=s["id"] == current) for s in services.auth.sessions()]  # type: ignore[arg-type]


@router.delete("/api/auth/sessions/{session_id}")
def revoke_login_session(session_id: str, _: AdminDep, services: ServicesDep) -> Ok:
    """Invalidate a dashboard session."""
    if not services.auth.revoke_session(session_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")
    return Ok()


# API keys


class ApiKeyCreate(BaseModel):
    """API key request."""

    name: str = Field(min_length=1, max_length=64)
    scope: Literal["chat", "admin"] = "chat"
    expires_at: datetime | None = None


class ApiKeyInfo(BaseModel):
    """An API key without its secret."""

    id: int
    name: str
    scope: Literal["chat", "admin"]
    created: float
    expires: float | None
    last_used: float | None
    revoked: float | None


class ApiKeyCreated(ApiKeyInfo):
    """A newly issued key; ``key`` is shown only in this response."""

    key: str


@router.get("/api/keys")
def list_api_keys(_: AdminDep, services: ServicesDep) -> list[ApiKeyInfo]:
    """List API keys (hashes and values are never returned)."""
    return [ApiKeyInfo(**row) for row in services.store.api_key_list()]


@router.post("/api/keys", status_code=status.HTTP_201_CREATED)
def create_api_key(body: ApiKeyCreate, _: AdminDep, services: ServicesDep) -> ApiKeyCreated:
    """Issue an API key; the plaintext is returned once."""
    expires = body.expires_at.timestamp() if body.expires_at else None
    if expires is not None and expires <= time.time():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "expires_at must be in the future")
    key, key_id = services.auth.create_api_key(body.name.strip(), body.scope, expires)
    row = next(r for r in services.store.api_key_list() if r["id"] == key_id)
    return ApiKeyCreated(**row, key=key)


@router.delete("/api/keys/{key_id}")
def revoke_api_key(key_id: int, _: AdminDep, services: ServicesDep) -> Ok:
    """Revoke an API key."""
    if not services.auth.revoke_api_key(key_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "API key not found or already revoked")
    return Ok()


# Settings


class ConfigItem(BaseModel):
    """One setting as shown on the Config page; secrets are masked."""

    key: str
    group: str
    kind: str
    description: str
    default: Any
    value: Any
    secret: bool
    choices: list[str] | None = None
    set: bool | None = None


class ConfigUpdate(BaseModel):
    """Settings to change; an empty secret clears it."""

    values: dict[str, Any]


class ConfigExport(BaseModel):
    """Import/export format (non-secret settings only)."""

    version: Literal[1] = 1
    settings: dict[str, Any]


@router.get("/api/config")
def get_config(_: AdminDep, services: ServicesDep) -> list[ConfigItem]:
    """Return every setting with its value and default."""
    return [ConfigItem(**item) for item in services.config.describe()]


@router.put("/api/config")
def put_config(body: ConfigUpdate, _: AdminDep, services: ServicesDep) -> list[ConfigItem]:
    """Validate and apply settings live; nothing is applied if any value is invalid."""
    try:
        services.config.update(body.values)
    except SettingError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    return [ConfigItem(**item) for item in services.config.describe()]


@router.get("/api/config/export")
def export_config(_: AdminDep, services: ServicesDep) -> ConfigExport:
    """Export the non-secret settings that differ from the defaults."""
    return ConfigExport(settings=services.config.export())


@router.post("/api/config/import")
def import_config(body: ConfigExport, _: AdminDep, services: ServicesDep) -> list[ConfigItem]:
    """Import non-secret settings; secrets are rejected."""
    try:
        services.config.update(body.settings, allow_secrets=False)
    except SettingError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    return [ConfigItem(**item) for item in services.config.describe()]


# System


@router.get("/api/system/vault")
def vault_status(_: AdminDep, services: ServicesDep) -> dict[str, Any]:
    """Secrets backend, whether it is reachable, and secrets still in another format."""
    return services.vault.status()
