"""OAuth 2.1 sign-in for MCP servers: authorization code with PKCE, discovery and refresh (spec 9.2).

The admin presses *Authorize*; the dashboard sends the browser to the server's authorization endpoint and receives the
code on ``/api/mcp/oauth/callback``. AIFRED then exchanges it for tokens and keeps them encrypted in the vault.

* **Discovery.** The server's ``/.well-known/oauth-protected-resource`` (or the ``resource_metadata`` address in its 401
  answer) names the authorization server, whose own metadata gives the endpoints. Only ``https`` metadata is followed,
  and ``http`` only on the host of the MCP server itself (a homelab server); redirects are never followed.
* **Client identity.** Dynamic client registration is deprecated, so AIFRED uses a *client ID metadata document*: a client
  id that is an https address serving AIFRED's metadata (``MCP_OAUTH_PUBLIC_URL`` makes AIFRED serve it), or a client id
  registered by hand (``MCP_OAUTH_CLIENT_ID`` or one per server). A client registered with a provider such as Google
  also has a secret: it is kept encrypted in the vault next to its id and scope and sent only to the token endpoint.
* **Safety.** ``state`` is single-use and expires, the code verifier never leaves the process, the token goes only to the
  endpoint found in discovery, and tokens are never logged or shown.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

if TYPE_CHECKING:
    from aifred.config import RuntimeConfig
    from aifred.vault import Vault

logger = logging.getLogger(__name__)

PENDING_TTL = 600.0
MAX_PENDING = 20
MAX_BODY = 256 * 1024
REFRESH_MARGIN = 60.0
TIMEOUT = httpx.Timeout(10.0, connect=5.0)
CIMD_PATH = "/api/mcp/oauth/client.json"
CALLBACK_PATH = "/api/mcp/oauth/callback"
MANUAL_REDIRECT = "http://localhost:8700" + CALLBACK_PATH   # an address every provider accepts; the browser cannot open it, so the admin copies it back


class OAuthError(Exception):
    """Signing in failed; the message is meant for the admin."""


def secret_name(server: str) -> str:
    """Vault name of a server's tokens."""
    return f"mcp_oauth:{server}"


def client_secret_name(server: str) -> str:
    """Vault name of a server's own OAuth client (id, secret and scope)."""
    return f"mcp_oauth_client:{server}"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def pkce_pair() -> tuple[str, str]:
    """A code verifier and its S256 challenge."""
    verifier = secrets.token_urlsafe(64)
    return verifier, _b64url(hashlib.sha256(verifier.encode()).digest())


@dataclass
class Pending:
    """A sign-in in progress."""

    server: str
    verifier: str
    redirect_uri: str
    client_id: str
    token_endpoint: str
    resource: str
    scope: str
    created: float


class OAuthService:
    """Sign-in and token upkeep for every MCP server that uses OAuth.

    Args:
        vault: Encrypted secrets (tokens live here).
        config: Runtime settings.
        transport: httpx transport (tests).
        clock: Wall clock (tests).
    """

    def __init__(self, vault: Vault, config: RuntimeConfig, *, transport: httpx.BaseTransport | None = None, clock: Any = time.time) -> None:
        self._vault = vault
        self._config = config
        self._transport = transport
        self._clock = clock
        self._pending: dict[str, Pending] = {}
        self._lock = threading.Lock()

    # HTTP

    def _client(self) -> httpx.Client:
        return httpx.Client(follow_redirects=False, timeout=TIMEOUT, transport=self._transport)

    @staticmethod
    def _allowed(url: str, server_url: str) -> None:
        parts, origin = urlsplit(url), urlsplit(server_url)
        if not parts.hostname or parts.username or parts.password:
            raise OAuthError(f"the address {url!r} is not valid")
        if parts.scheme == "https":
            return
        if parts.scheme == "http" and parts.hostname == origin.hostname:
            return
        raise OAuthError(f"{url} is not https: AIFRED only follows https authorization servers (http only on the MCP server's own host)")

    def _get_json(self, client: httpx.Client, url: str, server_url: str) -> dict[str, Any]:
        self._allowed(url, server_url)
        try:
            response = client.get(url, headers={"Accept": "application/json"})
        except httpx.HTTPError as exc:
            raise OAuthError(f"cannot reach {url}: {type(exc).__name__}") from None
        if response.status_code != 200 or len(response.content) > MAX_BODY:
            raise OAuthError(f"{url} answered HTTP {response.status_code}")
        try:
            body = response.json()
        except ValueError:
            raise OAuthError(f"{url} did not return JSON") from None
        if not isinstance(body, dict):
            raise OAuthError(f"{url} did not return a JSON object")
        return body

    # Discovery

    def discover(self, server_url: str) -> dict[str, Any]:
        """Find the authorization server of an MCP server and read its endpoints.

        Returns:
            ``authorization_endpoint``, ``token_endpoint``, ``resource``, ``cimd`` (whether client ID metadata documents are
            accepted) and ``scopes``.

        Raises:
            OAuthError: If the server does not advertise OAuth or its metadata is unusable.
        """
        origin = urlsplit(server_url)
        base = f"{origin.scheme}://{origin.netloc}"
        candidates = []
        with self._client() as client:
            try:
                challenge = client.post(server_url, json={"jsonrpc": "2.0", "id": 1, "method": "ping"}, headers={"Accept": "application/json, text/event-stream"})
                header = challenge.headers.get("www-authenticate", "") if challenge.status_code == 401 else ""
            except httpx.HTTPError:
                header = ""
            for part in header.replace(",", " ").split():
                if part.startswith("resource_metadata="):
                    candidates.append(part.split("=", 1)[1].strip('"'))
            path = origin.path.rstrip("/")
            candidates += [f"{base}/.well-known/oauth-protected-resource{path}", f"{base}/.well-known/oauth-protected-resource"]
            resource_meta: dict[str, Any] | None = None
            for candidate in dict.fromkeys(candidates):
                try:
                    resource_meta = self._get_json(client, candidate, server_url)
                    break
                except OAuthError:
                    continue
            if resource_meta is None:
                raise OAuthError("the server does not publish OAuth metadata (/.well-known/oauth-protected-resource)")
            issuers = resource_meta.get("authorization_servers")
            if not isinstance(issuers, list) or not issuers or not isinstance(issuers[0], str):
                raise OAuthError("the server's metadata names no authorization server")
            issuer = issuers[0].rstrip("/")
            self._allowed(issuer, server_url)
            meta: dict[str, Any] | None = None
            ip = urlsplit(issuer)
            for path_ in (f"/.well-known/oauth-authorization-server{ip.path}", f"/.well-known/openid-configuration{ip.path}", "/.well-known/oauth-authorization-server"):
                try:
                    meta = self._get_json(client, f"{ip.scheme}://{ip.netloc}{path_}", server_url)
                    break
                except OAuthError:
                    continue
        if meta is None:
            raise OAuthError(f"cannot read the metadata of the authorization server {issuer}")
        for key in ("authorization_endpoint", "token_endpoint"):
            if not isinstance(meta.get(key), str):
                raise OAuthError(f"the authorization server's metadata has no {key}")
            self._allowed(meta[key], server_url)
        methods = meta.get("code_challenge_methods_supported")
        if not isinstance(methods, list) or "S256" not in methods:
            raise OAuthError("the authorization server does not support PKCE with S256, which AIFRED requires")
        scopes = resource_meta.get("scopes_supported")
        return {
            "authorization_endpoint": meta["authorization_endpoint"], "token_endpoint": meta["token_endpoint"],
            "resource": str(resource_meta.get("resource") or server_url), "cimd": bool(meta.get("client_id_metadata_document_supported")),
            "scopes": [s for s in scopes if isinstance(s, str)] if isinstance(scopes, list) else [],
        }

    # Identity

    def client_id(self, override: str = "") -> str:
        """The client id to present: a per-server one, the global one, or the metadata document's address."""
        if override.strip():
            return override.strip()
        configured = str(self._config.get("MCP_OAUTH_CLIENT_ID") or "").strip()
        if configured:
            return configured
        public = str(self._config.get("MCP_OAUTH_PUBLIC_URL") or "").strip().rstrip("/")
        if public:
            return f"{public}{CIMD_PATH}"
        raise OAuthError("no client id: set MCP_OAUTH_CLIENT_ID (a client registered with the server's provider) or MCP_OAUTH_PUBLIC_URL (the https address where AIFRED is reachable, so it can serve a client metadata document)")

    def client_metadata(self) -> dict[str, Any] | None:
        """The client ID metadata document AIFRED serves, or ``None`` when no public address is configured."""
        public = str(self._config.get("MCP_OAUTH_PUBLIC_URL") or "").strip().rstrip("/")
        if not public:
            return None
        return {
            "client_id": f"{public}{CIMD_PATH}", "client_name": "AIFRED", "client_uri": public,
            "redirect_uris": [f"{public}{CALLBACK_PATH}"], "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"], "token_endpoint_auth_method": "none",
        }

    def set_client(self, server: str, client_id: str, client_secret: str | None, scope: str = "") -> None:
        """Keep the OAuth client of one server. ``client_secret`` of ``None`` keeps the stored secret.

        Raises:
            OAuthError: If the client id is empty or the values are unreasonably long.
        """
        client_id = client_id.strip()
        if not client_id or len(client_id) > 300:
            raise OAuthError("the client id is required")
        previous = self._client_record(server)
        secret = previous.get("client_secret", "") if client_secret is None else client_secret.strip()
        if len(secret) > 300 or len(scope) > 2000:
            raise OAuthError("the client secret or the scope is too long")
        self._vault.set(client_secret_name(server), json.dumps({"client_id": client_id, "client_secret": secret, "scope": scope.strip()}))

    def _client_record(self, server: str) -> dict[str, str]:
        raw = self._vault.get(client_secret_name(server))
        try:
            data = json.loads(raw) if raw else None
        except ValueError:
            return {}
        return {k: str(v) for k, v in data.items()} if isinstance(data, dict) else {}

    def client_info(self, server: str) -> dict[str, Any] | None:
        """The id and scope of a server's own client, and whether it has a secret (never the secret)."""
        record = self._client_record(server)
        if not record.get("client_id"):
            return None
        return {"client_id": record["client_id"], "has_secret": bool(record.get("client_secret")), "scope": record.get("scope", "")}

    def forget_client(self, server: str) -> None:
        """Drop a server's own client."""
        self._vault.delete(client_secret_name(server))

    # Sign-in

    def begin(self, server: str, server_url: str, redirect_uri: str, client_override: str = "", scope: str = "") -> str:
        """Start a sign-in and return the address to open in the browser.

        Raises:
            OAuthError: If discovery fails or no client id is available.
        """
        found = self.discover(server_url)
        own = self._client_record(server)
        client_override = client_override.strip() or own.get("client_id", "")
        client_id = self.client_id(client_override)
        if client_id.startswith("https://") and client_id.endswith(CIMD_PATH) and not found["cimd"] and not client_override.strip() and not str(self._config.get("MCP_OAUTH_CLIENT_ID") or "").strip():
            raise OAuthError("this authorization server does not accept client metadata documents: register AIFRED with it and set MCP_OAUTH_CLIENT_ID")
        verifier, challenge = pkce_pair()
        state = secrets.token_urlsafe(32)
        scope = scope.strip() or own.get("scope", "") or " ".join(found["scopes"])
        with self._lock:
            now = self._clock()
            for key in [k for k, p in self._pending.items() if now - p.created > PENDING_TTL]:
                del self._pending[key]
            if len(self._pending) >= MAX_PENDING:
                raise OAuthError("too many sign-ins in progress; try again in a few minutes")
            self._pending[state] = Pending(server, verifier, redirect_uri, client_id, found["token_endpoint"], found["resource"], scope, now)
        query = {
            "response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri, "state": state,
            "code_challenge": challenge, "code_challenge_method": "S256", "resource": found["resource"],
        }
        if scope:
            query["scope"] = scope
        if urlsplit(found["authorization_endpoint"]).hostname == "accounts.google.com":
            query.update({"access_type": "offline", "prompt": "consent"})   # without these Google issues no refresh token
        return f"{found['authorization_endpoint']}{'&' if '?' in found['authorization_endpoint'] else '?'}{urlencode(query)}"

    def finish(self, state: str, code: str) -> str:
        """Complete a sign-in: swap the code for tokens and store them. Returns the server name.

        Raises:
            OAuthError: For an unknown or expired state, or a refusal by the token endpoint.
        """
        with self._lock:
            pending = self._pending.pop(state, None)
        if pending is None or self._clock() - pending.created > PENDING_TTL:
            raise OAuthError("this sign-in is unknown or has expired; press Authorize again")
        tokens = self._token_request(pending.token_endpoint, self._with_secret(pending.server, {
            "grant_type": "authorization_code", "code": code, "redirect_uri": pending.redirect_uri, "client_id": pending.client_id,
            "code_verifier": pending.verifier, "resource": pending.resource,
        }))
        self._store(pending.server, tokens, pending.client_id, pending.token_endpoint, pending.resource, pending.scope)
        return pending.server

    def _with_secret(self, server: str, form: dict[str, str]) -> dict[str, str]:
        secret = self._client_record(server).get("client_secret", "")
        return {**form, "client_secret": secret} if secret else form

    def _token_request(self, endpoint: str, form: dict[str, str]) -> dict[str, Any]:
        with self._client() as client:
            try:
                response = client.post(endpoint, data=form, headers={"Accept": "application/json"})
            except httpx.HTTPError as exc:
                raise OAuthError(f"cannot reach the token endpoint: {type(exc).__name__}") from None
        try:
            body = response.json() if len(response.content) <= MAX_BODY else {}
        except ValueError:
            body = {}
        if response.status_code != 200 or not isinstance(body, dict) or not isinstance(body.get("access_token"), str):
            detail = str(body.get("error_description") or body.get("error") or f"HTTP {response.status_code}")[:200] if isinstance(body, dict) else f"HTTP {response.status_code}"
            raise OAuthError(f"the token endpoint refused the request: {detail}")
        return body

    def _store(self, server: str, tokens: dict[str, Any], client_id: str, endpoint: str, resource: str, scope: str, previous: dict[str, Any] | None = None) -> None:
        expires_in = tokens.get("expires_in")
        record = {
            "client_id": client_id, "token_endpoint": endpoint, "resource": resource, "scope": str(tokens.get("scope") or scope),
            "access_token": tokens["access_token"],
            "refresh_token": str(tokens.get("refresh_token") or (previous or {}).get("refresh_token") or ""),
            "expires_at": self._clock() + float(expires_in) if isinstance(expires_in, (int, float)) and not isinstance(expires_in, bool) else 0.0,
        }
        self._vault.set(secret_name(server), json.dumps(record))

    # Tokens

    def _record(self, server: str) -> dict[str, Any] | None:
        raw = self._vault.get(secret_name(server))
        try:
            data = json.loads(raw) if raw else None
        except ValueError:
            return None
        return data if isinstance(data, dict) and isinstance(data.get("access_token"), str) else None

    def status(self, server: str) -> dict[str, Any]:
        """Whether the server is signed in (never the tokens themselves)."""
        record = self._record(server)
        if not record:
            return {"authorized": False, "expires_at": None, "scope": ""}
        return {"authorized": True, "expires_at": record["expires_at"] or None, "scope": record.get("scope", "")}

    def token(self, server: str) -> str:
        """A valid access token, refreshed when it is about to expire.

        Raises:
            OAuthError: If the server is not signed in or the refresh failed (sign in again).
        """
        record = self._record(server)
        if not record:
            raise OAuthError("not authorized: press Authorize on the MCP page")
        expires = float(record.get("expires_at") or 0)
        if not expires or expires - self._clock() > REFRESH_MARGIN:
            return str(record["access_token"])
        if not record.get("refresh_token"):
            raise OAuthError("the access token expired and there is no refresh token: authorize again")
        tokens = self._token_request(record["token_endpoint"], self._with_secret(server, {
            "grant_type": "refresh_token", "refresh_token": record["refresh_token"], "client_id": record["client_id"], "resource": record["resource"],
        }))
        self._store(server, tokens, record["client_id"], record["token_endpoint"], record["resource"], record.get("scope", ""), record)
        logger.info("Refreshed the OAuth token of MCP server %s", server)
        return str(tokens["access_token"])

    def forget(self, server: str) -> None:
        """Drop the stored tokens (the server must be authorized again)."""
        self._vault.delete(secret_name(server))


def callback_params(url: str) -> dict[str, str]:
    """Query parameters of a callback address (test helper and for clarity at call sites)."""
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


def pasted_answer(text: str) -> tuple[str, str]:
    """The ``state`` and ``code`` in the address the admin copied from the browser after accepting.

    Raises:
        OAuthError: If the text is not such an address, or it carries the provider's refusal.
    """
    text = text.strip()
    if not text or len(text) > 4096:
        raise OAuthError("paste the address the browser ended up on after you accepted")
    params = callback_params(text if "?" in text else f"?{text}")
    if params.get("error"):
        raise OAuthError(f"the authorization server said: {params.get('error_description') or params['error']}")
    if not params.get("state") or not params.get("code"):
        raise OAuthError("that address has no code in it: copy the whole address from the browser's address bar")
    return params["state"], params["code"]
