"""Connectors: ready-made MCP servers of well-known products, set up in a few steps (spec 14.3).

A connector is a preset for an ordinary MCP server: its address, the sign-in it needs, the permissions (scopes) it can ask
for and the steps to take at the product's side. Setting one up creates (or updates) the MCP server of the same name and
stores the OAuth client you registered; the tokens, tools and instructions are then handled like those of any MCP server.
New products are added by appending to ``CONNECTORS``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from aifred.mcp import McpConfigError
from aifred.mcp_oauth import MANUAL_REDIRECT, OAuthError

if TYPE_CHECKING:
    from aifred.app import Services

LOCAL_HOSTS = ("localhost", "127.0.0.1", "[::1]")


class ConnectorError(Exception):
    """A connector cannot be set up as asked; the message is meant for the admin."""


@dataclass(frozen=True)
class Permission:
    """One scope a connector can ask for."""

    id: str
    label: str
    detail: str
    default: bool = False


@dataclass(frozen=True)
class Connector:
    """The preset of one product.

    Attributes:
        id: Short name, also the name of the MCP server it creates.
        steps: What to do at the product's side; ``{redirect}`` is replaced by AIFRED's callback address.
        instruction: The "what is this for?" text of the MCP server.
    """

    id: str
    title: str
    description: str
    url: str
    permissions: tuple[Permission, ...]
    steps: tuple[str, ...]
    instruction: str
    docs: str = ""
    notes: tuple[str, ...] = ()

    def public(self, redirect: str) -> dict[str, Any]:
        return {
            "id": self.id, "title": self.title, "description": self.description, "url": self.url, "docs": self.docs,
            "permissions": [{"id": p.id, "label": p.label, "detail": p.detail, "default": p.default} for p in self.permissions],
            "steps": [step.replace("{redirect}", redirect) for step in self.steps], "notes": list(self.notes),
        }


_GMAIL = "https://www.googleapis.com/auth/gmail."

CONNECTORS: dict[str, Connector] = {c.id: c for c in (
    Connector(
        id="gmail",
        title="Gmail",
        description="Search and read your mail, write drafts and label messages, through Google's Gmail MCP server. It cannot send mail or delete it.",
        url="https://gmailmcp.googleapis.com/mcp/v1",
        docs="https://developers.google.com/workspace/gmail/api/guides/configure-mcp-server",
        permissions=(
            Permission(_GMAIL + "readonly", "Read mail", "Search and read messages and threads, list labels and drafts.", True),
            Permission(_GMAIL + "compose", "Write drafts", "Create drafts. Nothing is sent: you send them from Gmail.", True),
            Permission(_GMAIL + "modify", "Organise", "Label and unlabel messages and threads (this scope also lets Google move mail to the trash)."),
        ),
        steps=(
            "In the Google Cloud console create or pick a project and enable two APIs for it: the Gmail API and the Gmail MCP API.",
            "Open Google Auth Platform > Branding and set up the consent screen if it is not yet: any app name, your email, and the audience Internal (or External, adding yourself as a test user under Audience).",
            "Open Google Auth Platform > Clients > Create client, choose the application type Web application, add this authorized redirect URI: {redirect} and tick Use this client for an AI-powered agent.",
            "Under Data access add the permissions you choose below, then Save.",
            "Copy the client id and the client secret into the form below, tick the permissions and press Save, then Authorize.",
        ),
        instruction="The user's Gmail mailbox: use it when the user asks about their email (find or read messages and threads, write a draft, label a message). It cannot send or delete mail. Never use it for anything else.",
        notes=(
            "Google issues sign-ins that last only 7 days to an app whose publishing status is Testing: publish the app (or use an Internal audience in Google Workspace) to stay signed in.",
            "Each permission is a real capability of the assistant in your mailbox. Start with the first two and add more only when you need them.",
        ),
    ),
)}


def redirect_warning(redirect: str) -> str:
    """Why Google would refuse this callback address (an empty string when it would accept it)."""
    parts = urlsplit(redirect)
    if parts.scheme == "https" or parts.hostname in LOCAL_HOSTS or (parts.hostname or "").endswith(".localhost"):
        return ""
    return "plain http on a name that is not localhost"


def _manual_note(why: str, own: str) -> str:
    return (f"The dashboard is open at {own}, which is {why}: Google does not accept that as a redirect address. Register the address above instead. "
            "After you press Authorize and accept, the browser tries to open it and shows an error page: copy that page's address from the address bar into the box below and press Finish. "
            "(Or serve AIFRED over https and set MCP_OAUTH_PUBLIC_URL on the Config page, and the address follows it.)")


def _live(services: Services, name: str) -> dict[str, Any] | None:
    return next((s for s in services.mcp_manager.status() if s["name"] == name), None)


def state_of(services: Services, connector: Connector, redirect: str) -> dict[str, Any]:
    """The preset together with how far its set-up has gone. ``redirect`` is this dashboard's own callback address."""
    server = services.mcp.get(connector.id)
    why = redirect_warning(redirect)
    chosen = MANUAL_REDIRECT if why else redirect   # an address Google refuses is replaced by one it accepts, answered by pasting
    data = connector.public(chosen)
    data["redirect_uri"] = chosen
    data["flow"] = "paste" if why else "automatic"
    data["redirect_note"] = _manual_note(why, redirect) if why else ""
    mine = server is not None and server.url == connector.url
    data["conflict"] = server is not None and not mine
    data["configured"] = mine
    data["client"] = services.mcp.oauth.client_info(connector.id) if mine else None
    data["authorized"] = bool(mine and services.mcp.oauth.status(connector.id)["authorized"])
    live = _live(services, connector.id) if mine else None
    data["state"] = live["state"] if live else ("needs sign-in" if mine and not data["authorized"] else "")
    data["error"] = live.get("error", "") if live else ""
    data["tools"] = live.get("tools", 0) if live else 0
    return data


def save(services: Services, connector: Connector, client_id: str, client_secret: str | None, scopes: list[str]) -> None:
    """Create or update the MCP server of a connector and keep its OAuth client.

    Raises:
        ConnectorError: If the input is not valid or the name is taken by another server.
    """
    allowed = {p.id for p in connector.permissions}
    chosen = [scope for scope in dict.fromkeys(scopes) if scope in allowed]
    if len(chosen) != len(set(scopes)):
        raise ConnectorError("a permission that this connector does not offer was asked for")
    if not chosen:
        raise ConnectorError("choose at least one permission")
    existing = services.mcp.get(connector.id)
    if existing is not None and existing.url != connector.url:
        raise ConnectorError(f"an MCP server named {connector.id!r} already exists with another address: rename or delete it first")
    has_secret = bool(services.mcp.oauth.client_info(connector.id) and services.mcp.oauth.client_info(connector.id)["has_secret"])
    if not (client_secret or "").strip() and not has_secret:
        raise ConnectorError("the client secret is required")
    try:
        services.mcp.oauth.set_client(connector.id, client_id, client_secret if (client_secret or "").strip() else None, " ".join(chosen))
        services.mcp.save(connector.id, {"url": connector.url, "oauth": True, "enabled": True})
        if not services.instructions.get(f"mcp:{connector.id}"):
            services.instructions.check(f"mcp:{connector.id}", connector.instruction)
            services.instructions.set(f"mcp:{connector.id}", connector.instruction)
            services.agent.reset_all_prompts()
    except (OAuthError, McpConfigError) as exc:
        raise ConnectorError(str(exc)) from None
    services.mcp_manager.reload()


def remove(services: Services, connector: Connector) -> None:
    """Delete the MCP server of a connector with its tokens and client."""
    server = services.mcp.get(connector.id)
    if server is None or server.url != connector.url:
        raise ConnectorError(f"{connector.title} is not set up")
    services.mcp.delete(connector.id)
    services.mcp_manager.reload()
