"""Dashboard API for LLM providers, models, routes, health and usage (spec 6.1)."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from aifred.gateways.api import AdminDep, Ok, ServicesDep
from aifred.llm import LLMError
from aifred.providers import PRESETS, ProviderError
from aifred.router import NoProvider, RouteError, RouteExhausted

logger = logging.getLogger(__name__)

router = APIRouter()


def _unprocessable(exc: Exception) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))


# Providers


class ProviderIn(BaseModel):
    """Provider fields; omitted fields keep their value on update."""

    slug: str | None = None
    type: str | None = None
    base_url: str | None = None
    headers: dict[str, str] | None = None
    extra_body: dict[str, Any] | None = None
    config: dict[str, Any] | None = None
    connect_timeout: float | None = None
    first_token_timeout: float | None = None
    stall_timeout: float | None = None
    total_timeout: float | None = None
    verify_tls: bool | str | None = None
    enabled: bool | None = None
    budget_tokens_daily: int | None = Field(default=None, ge=1)
    budget_tokens_monthly: int | None = Field(default=None, ge=1)

    def fields(self) -> dict[str, Any]:
        """Only the fields the client sent."""
        return self.model_dump(exclude_unset=True)


class ProviderCreate(ProviderIn):
    """New provider, optionally with its first key."""

    api_key: str | None = Field(default=None, max_length=2048)
    key_label: str = ""
    skip_test: bool = False


class KeyIn(BaseModel):
    """An API key value (write-only)."""

    value: str = Field(min_length=1, max_length=2048)
    label: str | None = Field(default=None, max_length=64)


class ModelIn(BaseModel):
    """Model overrides."""

    model_id: str
    context_window: int | None = None
    thinking_control: str | None = None
    price_in: float | None = None
    price_out: float | None = None
    streaming: bool | None = None
    vision: bool | None = None

    def fields(self) -> dict[str, Any]:
        """Only the fields the client sent."""
        return self.model_dump(exclude_unset=True)


@router.get("/api/providers/presets")
def list_presets(_: AdminDep) -> list[dict[str, Any]]:
    """Provider types with their default base URLs and notes."""
    return [
        {
            "type": p.type, "label": p.label, "base_url": p.base_url, "key": p.key, "notes": p.notes,
            "headers": p.headers, "config_fields": list(p.config_fields),
        }
        for p in PRESETS.values()
    ]


@router.get("/api/providers")
def list_providers(_: AdminDep, services: ServicesDep) -> list[dict[str, Any]]:
    """List providers with their keys (values are never returned)."""
    return [services.providers.public(p) for p in services.providers.list()]


@router.post("/api/providers", status_code=status.HTTP_201_CREATED)
async def create_provider(body: ProviderCreate, _: AdminDep, services: ServicesDep) -> dict[str, Any]:
    """Create a provider; the connection is tested first unless ``skip_test`` is set."""
    data = body.fields()
    for name in ("api_key", "key_label", "skip_test"):
        data.pop(name, None)
    svc = services.providers
    try:
        if not body.skip_test:
            probe = svc.preview(data)
            await svc.test_connection(probe, body.api_key)
        provider = svc.create(data, body.api_key, body.key_label)
    except ProviderError as exc:
        raise _unprocessable(exc) from None
    return svc.public(provider)


@router.put("/api/providers/{provider_id}")
def update_provider(provider_id: int, body: ProviderIn, _: AdminDep, services: ServicesDep) -> dict[str, Any]:
    """Update a provider's configuration."""
    try:
        provider = services.providers.update(provider_id, body.fields())
    except ProviderError as exc:
        raise _unprocessable(exc) from None
    return services.providers.public(provider)


@router.delete("/api/providers/{provider_id}")
def delete_provider(provider_id: int, _: AdminDep, services: ServicesDep) -> Ok:
    """Delete a provider, its keys, models and route entries."""
    try:
        services.providers.delete(provider_id)
    except ProviderError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None
    return Ok()


@router.post("/api/providers/{provider_id}/test")
async def test_provider(provider_id: int, _: AdminDep, services: ServicesDep) -> dict[str, Any]:
    """Check the endpoint and key by listing models."""
    try:
        count = await services.providers.test_connection(services.providers.get(provider_id))
    except ProviderError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "models": count}


@router.post("/api/providers/{provider_id}/keys", status_code=status.HTTP_201_CREATED)
def add_key(provider_id: int, body: KeyIn, _: AdminDep, services: ServicesDep) -> dict[str, Any]:
    """Add an API key (write-only)."""
    try:
        services.providers.add_key(provider_id, body.value, body.label or "")
        return services.providers.public(services.providers.get(provider_id))
    except ProviderError as exc:
        raise _unprocessable(exc) from None


@router.put("/api/providers/{provider_id}/keys/{key_id}")
def replace_key(provider_id: int, key_id: int, body: KeyIn, _: AdminDep, services: ServicesDep) -> dict[str, Any]:
    """Replace a key's value, which also clears its invalid state."""
    try:
        services.providers.replace_key(key_id, body.value, body.label)
        return services.providers.public(services.providers.get(provider_id))
    except ProviderError as exc:
        raise _unprocessable(exc) from None


@router.delete("/api/providers/{provider_id}/keys/{key_id}")
def delete_key(provider_id: int, key_id: int, _: AdminDep, services: ServicesDep) -> Ok:
    """Delete an API key."""
    try:
        services.providers.delete_key(provider_id, key_id)
    except ProviderError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None
    return Ok()


# Models


@router.post("/api/providers/{provider_id}/models/refresh")
async def refresh_models(provider_id: int, _: AdminDep, services: ServicesDep) -> list[dict[str, Any]]:
    """Re-read the provider's model list."""
    try:
        return await services.providers.refresh_models(provider_id)
    except ProviderError as exc:
        raise _unprocessable(exc) from None


@router.get("/api/providers/{provider_id}/models")
def get_models(provider_id: int, _: AdminDep, services: ServicesDep) -> list[dict[str, Any]]:
    """List a provider's models."""
    try:
        services.providers.get(provider_id)
    except ProviderError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None
    return services.providers.models(provider_id)


@router.put("/api/providers/{provider_id}/models")
def put_models(provider_id: int, body: list[ModelIn], _: AdminDep, services: ServicesDep) -> list[dict[str, Any]]:
    """Add models by hand or override their settings."""
    try:
        return services.providers.save_models(provider_id, [m.fields() for m in body])
    except ProviderError as exc:
        raise _unprocessable(exc) from None


@router.post("/api/providers/{provider_id}/models/{model_id:path}/test")
async def test_model(provider_id: int, model_id: str, _: AdminDep, services: ServicesDep) -> dict[str, Any]:
    """Send a minimal tool-calling request; only models that pass can join a route."""
    try:
        return await services.providers.test_model(provider_id, model_id)
    except ProviderError as exc:
        raise _unprocessable(exc) from None


# Routes


class RouteEntryIn(BaseModel):
    """A route entry."""

    provider: str
    model: str
    key_id: int | None = None
    timeouts: dict[str, float] = Field(default_factory=dict)


class RouteIn(BaseModel):
    """A route's ordered entries."""

    entries: list[RouteEntryIn]


def _route_view(services: Any, name: str) -> dict[str, Any]:
    return {
        "name": name,
        "entries": [
            {
                "id": e.id, "provider": e.provider.slug, "model": e.model, "key_id": e.key_id,
                "timeouts": e.timeouts, "context_window": e.context_window,
            }
            for e in services.llm.route_entries(name)
        ],
    }


@router.get("/api/routes")
def list_routes(_: AdminDep, services: ServicesDep) -> list[dict[str, Any]]:
    """List routes with their entries."""
    return [_route_view(services, n) for n in services.store.route_names()]


@router.get("/api/routes/{name}")
def get_route(name: str, _: AdminDep, services: ServicesDep) -> dict[str, Any]:
    """Return one route."""
    try:
        return _route_view(services, name)
    except RouteError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None


@router.put("/api/routes/{name}")
def put_route(name: str, body: RouteIn, _: AdminDep, services: ServicesDep) -> dict[str, Any]:
    """Create or replace a route; every model must have passed the tool-calling test."""
    try:
        services.llm.set_route(name, [e.model_dump() for e in body.entries])
    except ProviderError as exc:
        raise _unprocessable(exc) from None
    return _route_view(services, name)


@router.delete("/api/routes/{name}")
def delete_route(name: str, _: AdminDep, services: ServicesDep) -> Ok:
    """Delete a custom route."""
    try:
        services.llm.delete_route(name)
    except ProviderError as exc:
        raise _unprocessable(exc) from None
    return Ok()


@router.post("/api/routes/{name}/test")
async def test_route(name: str, _: AdminDep, services: ServicesDep) -> dict[str, Any]:
    """Run a one-word request through the route and report which entry answered."""
    try:
        entries = services.llm.resolve(name, name)
        result, entry, notices = await services.llm.complete(
            entries, [{"role": "user", "content": "Reply with the single word OK."}], route=name, source="test"
        )
    except NoProvider as exc:
        return {"ok": False, "error": str(exc), "failures": []}
    except RouteExhausted as exc:
        return {"ok": False, "error": str(exc), "failures": [{"entry": a, "reason": b} for a, b in exc.failures]}
    except (RouteError, LLMError) as exc:
        return {"ok": False, "error": str(exc), "failures": []}
    return {
        "ok": True, "entry": entry.label, "reply": result.content[:200], "latency_ms": result.latency_ms,
        "notices": notices,
    }


# Health and usage


@router.get("/api/llm/health")
def llm_health(_: AdminDep, services: ServicesDep) -> list[dict[str, Any]]:
    """Per-provider health, today's usage and route entry states."""
    return services.llm.health_snapshot()


@router.post("/api/llm/health/{entry_id}/reset")
def reset_health(entry_id: int, _: AdminDep, services: ServicesDep) -> Ok:
    """Clear the cooldown state of a route entry and its provider keys."""
    try:
        services.llm.reset_health(entry_id)
    except ProviderError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None
    return Ok()


@router.get("/api/usage")
def usage(_: AdminDep, services: ServicesDep, days: Annotated[int, Query(ge=1, le=366)] = 7) -> list[dict[str, Any]]:
    """Daily token usage by provider, model and agent."""
    return services.llm.usage_report(days)
