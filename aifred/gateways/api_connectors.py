"""Dashboard API for connectors: ready-made MCP servers of well-known products (spec 14.3)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from aifred import connectors
from aifred.gateways.api import AdminDep, Ok, ServicesDep
from aifred.gateways.api_mcp import _redirect_uri
from aifred.vault import VaultError

router = APIRouter()


class ConnectorIn(BaseModel):
    """The OAuth client registered at the product and the permissions to ask for.

    ``client_secret`` left out (or empty) keeps the stored secret.
    """

    client_id: str = Field(max_length=300)
    client_secret: str | None = Field(default=None, max_length=300)
    scopes: list[str] = Field(max_length=20)


def _connector(connector_id: str) -> connectors.Connector:
    found = connectors.CONNECTORS.get(connector_id)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no connector named {connector_id!r}")
    return found


@router.get("/api/connectors")
def list_connectors(request: Request, _: AdminDep, services: ServicesDep) -> list[dict[str, Any]]:
    """Every connector with the state of its set-up."""
    redirect = _redirect_uri(request, services)
    return [connectors.state_of(services, c, redirect) for c in connectors.CONNECTORS.values()]


@router.put("/api/connectors/{connector_id}")
async def save_connector(connector_id: str, body: ConnectorIn, request: Request, _: AdminDep, services: ServicesDep) -> dict[str, Any]:
    """Create or update the MCP server of a connector; signing in is the next step (Authorize)."""
    connector = _connector(connector_id)
    try:
        connectors.save(services, connector, body.client_id, body.client_secret, body.scopes)
    except connectors.ConnectorError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    except VaultError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"the secret store is unavailable: {exc}") from None
    services.store.journal_add("settings", "mcp_server", f"Connector {connector.title!r} set up")
    return connectors.state_of(services, connector, _redirect_uri(request, services))


@router.delete("/api/connectors/{connector_id}")
def remove_connector(connector_id: str, _: AdminDep, services: ServicesDep) -> Ok:
    """Remove the MCP server of a connector with its tokens and client."""
    try:
        connectors.remove(services, _connector(connector_id))
    except connectors.ConnectorError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None
    return Ok()
