"""Tests for connectors (aifred.connectors, the OAuth client of a server and /api/connectors)."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from aifred import connectors
from aifred.app import Services
from aifred.connectors import Connector, ConnectorError, Permission
from aifred.mcp_oauth import CALLBACK_PATH, OAuthError
from tests.conftest import login
from tests.fake_mcp import FakeMcp
from tests.fake_oauth import FakeOAuth

SECRET = "s3cret-value-xyz"
READ, DRAFT = "scope.read", "scope.draft"


@pytest.fixture
def oauth_server() -> Iterator[FakeOAuth]:
    server = FakeOAuth(require_secret=SECRET).start()
    yield server
    server.stop()


@pytest.fixture
def mcp(fake_mcp: Callable[..., FakeMcp], oauth_server: FakeOAuth) -> FakeMcp:
    return fake_mcp(oauth_issuer=oauth_server.issuer, token_ok=lambda header: header.removeprefix("Bearer ") in oauth_server.access_tokens)


@pytest.fixture
def connector(mcp: FakeMcp, monkeypatch: pytest.MonkeyPatch) -> Connector:
    """A connector that points at the fake MCP server."""
    preset = Connector(
        id="mail", title="Mail", description="A test mailbox.", url=mcp.url,
        permissions=(Permission(READ, "Read", "Read mail.", True), Permission(DRAFT, "Draft", "Write drafts.", True), Permission("scope.send", "Send", "Send mail.")),
        steps=("Register a client with this redirect: {redirect}",), instruction="The test mailbox: use it for email questions.",
    )
    monkeypatch.setattr(connectors, "CONNECTORS", {preset.id: preset})
    return preset


@pytest.fixture
def api(client: TestClient) -> TestClient:
    client.headers["X-CSRF-Token"] = login(client)
    return client


def authorize(url: str) -> tuple[str, str]:
    response = httpx.get(url, follow_redirects=False)
    assert response.status_code == 302
    query = parse_qs(urlsplit(response.headers["location"]).query)
    return query["state"][0], query["code"][0]


def test_the_catalog_has_gmail_with_safe_defaults() -> None:
    gmail = connectors.CONNECTORS["gmail"]
    assert gmail.url == "https://gmailmcp.googleapis.com/mcp/v1"
    on = [p.id for p in gmail.permissions if p.default]
    assert on == ["https://www.googleapis.com/auth/gmail.readonly", "https://www.googleapis.com/auth/gmail.compose"]   # what Google's own guide asks for
    assert all(p.id.startswith("https://") for p in gmail.permissions) and "https://mail.google.com/" not in [p.id for p in gmail.permissions]   # no full access
    assert "{redirect}" in " ".join(gmail.steps)


def test_a_plain_http_address_is_flagged_unless_it_is_localhost() -> None:
    assert connectors.redirect_warning("http://192.168.0.5:8700" + CALLBACK_PATH)
    assert connectors.redirect_warning("http://aifred.lan:8700" + CALLBACK_PATH)
    assert not connectors.redirect_warning("http://localhost:8700" + CALLBACK_PATH)
    assert not connectors.redirect_warning("http://127.0.0.1:8700" + CALLBACK_PATH)
    assert not connectors.redirect_warning("https://aifred.example.org" + CALLBACK_PATH)


def test_saving_creates_the_server_and_keeps_the_client_encrypted(services: Services, connector: Connector) -> None:
    connectors.save(services, connector, "client-1", SECRET, [READ, DRAFT])
    server = services.mcp.get("mail")
    assert server is not None and server.oauth and server.url == connector.url
    assert services.instructions.get("mcp:mail")["text"] == connector.instruction
    info = services.mcp.oauth.client_info("mail")
    assert info == {"client_id": "client-1", "has_secret": True, "scope": f"{READ} {DRAFT}"}
    assert SECRET not in str(services.mcp.public(server)) and SECRET not in str(info)


def test_the_stored_secret_is_kept_when_none_is_sent(services: Services, connector: Connector) -> None:
    connectors.save(services, connector, "client-1", SECRET, [READ])
    connectors.save(services, connector, "client-2", None, [READ, DRAFT])
    connectors.save(services, connector, "client-3", "  ", [READ])
    info = services.mcp.oauth.client_info("mail")
    assert info == {"client_id": "client-3", "has_secret": True, "scope": READ}
    assert services.mcp.oauth._client_record("mail")["client_secret"] == SECRET


def test_bad_input_is_refused(services: Services, connector: Connector) -> None:
    with pytest.raises(ConnectorError, match="at least one"):
        connectors.save(services, connector, "client-1", SECRET, [])
    with pytest.raises(ConnectorError, match="does not offer"):
        connectors.save(services, connector, "client-1", SECRET, [READ, "scope.root"])
    with pytest.raises(ConnectorError, match="secret is required"):
        connectors.save(services, connector, "client-1", "", [READ])
    with pytest.raises(ConnectorError, match="client id"):
        connectors.save(services, connector, "  ", SECRET, [READ])
    assert services.mcp.get("mail") is None and services.mcp.oauth.client_info("mail") is None


def test_a_server_of_the_same_name_with_another_address_is_not_overwritten(services: Services, connector: Connector) -> None:
    services.mcp.save("mail", {"url": "https://elsewhere.example.org/mcp"})
    with pytest.raises(ConnectorError, match="another address"):
        connectors.save(services, connector, "client-1", SECRET, [READ])
    assert services.mcp.get("mail").url == "https://elsewhere.example.org/mcp"   # type: ignore[union-attr]
    assert connectors.state_of(services, connector, "http://localhost:8700" + CALLBACK_PATH)["conflict"] is True


def test_a_sign_in_uses_the_own_client_and_sends_the_secret(services: Services, connector: Connector, oauth_server: FakeOAuth) -> None:
    connectors.save(services, connector, "client-1", SECRET, [READ, DRAFT])
    url = services.mcp.oauth.begin("mail", connector.url, "http://localhost:8700" + CALLBACK_PATH)
    state, code = authorize(url)
    query = oauth_server.authorize_queries[-1]
    assert query["client_id"] == "client-1" and query["scope"] == f"{READ} {DRAFT}"
    assert services.mcp.oauth.finish(state, code) == "mail"
    assert oauth_server.token_forms[-1]["client_secret"] == SECRET
    assert services.mcp.oauth.status("mail")["authorized"]


def test_a_refresh_sends_the_secret_too(services: Services, connector: Connector, oauth_server: FakeOAuth, monkeypatch: pytest.MonkeyPatch) -> None:
    oauth_server.expires_in = 30   # inside the refresh margin
    connectors.save(services, connector, "client-1", SECRET, [READ])
    state, code = authorize(services.mcp.oauth.begin("mail", connector.url, "http://localhost:8700" + CALLBACK_PATH))
    services.mcp.oauth.finish(state, code)
    first = services.mcp.oauth.token("mail")
    assert oauth_server.token_forms[-1]["grant_type"] == "refresh_token" and oauth_server.token_forms[-1]["client_secret"] == SECRET
    assert first != "at-1"


def test_without_the_secret_the_provider_refuses_and_nothing_is_stored(services: Services, connector: Connector) -> None:
    services.config.update({"MCP_OAUTH_CLIENT_ID": "client-1"})   # a client with no secret of its own
    services.mcp.save("mail", {"url": connector.url, "oauth": True})
    state, code = authorize(services.mcp.oauth.begin("mail", connector.url, "http://localhost:8700" + CALLBACK_PATH))
    with pytest.raises(OAuthError, match="client_secret is missing"):
        services.mcp.oauth.finish(state, code)
    assert not services.mcp.oauth.status("mail")["authorized"]


def test_google_gets_offline_access_so_that_a_refresh_token_is_issued(services: Services, connector: Connector, monkeypatch: pytest.MonkeyPatch) -> None:
    found = {"authorization_endpoint": "https://accounts.google.com/o/oauth2/v2/auth", "token_endpoint": "https://oauth2.googleapis.com/token", "resource": connector.url, "cimd": False, "scopes": []}
    monkeypatch.setattr(services.mcp.oauth, "discover", lambda url: found)
    connectors.save(services, connector, "client-1", SECRET, [READ])
    query = parse_qs(urlsplit(services.mcp.oauth.begin("mail", connector.url, "http://localhost:8700" + CALLBACK_PATH)).query)
    assert query["access_type"] == ["offline"] and query["prompt"] == ["consent"] and query["client_id"] == ["client-1"]
    other = dict(found, authorization_endpoint="https://login.example.org/authorize")
    monkeypatch.setattr(services.mcp.oauth, "discover", lambda url: other)
    query = parse_qs(urlsplit(services.mcp.oauth.begin("mail", connector.url, "http://localhost:8700" + CALLBACK_PATH)).query)
    assert "access_type" not in query and "prompt" not in query


def test_removing_a_connector_drops_the_server_tokens_and_client(services: Services, connector: Connector) -> None:
    connectors.save(services, connector, "client-1", SECRET, [READ])
    state, code = authorize(services.mcp.oauth.begin("mail", connector.url, "http://localhost:8700" + CALLBACK_PATH))
    services.mcp.oauth.finish(state, code)
    connectors.remove(services, connector)
    assert services.mcp.get("mail") is None and services.mcp.oauth.client_info("mail") is None
    assert not services.mcp.oauth.status("mail")["authorized"] and not services.instructions.get("mcp:mail")
    with pytest.raises(ConnectorError, match="not set up"):
        connectors.remove(services, connector)


def test_the_api_round_trip(api: TestClient, connector: Connector) -> None:
    listed = api.get("/api/connectors").json()
    assert [c["id"] for c in listed] == ["mail"] and listed[0]["configured"] is False and listed[0]["redirect_uri"].endswith(CALLBACK_PATH)
    assert "https://testserver" in listed[0]["steps"][0] and listed[0]["redirect_warning"] == ""   # the test client is https
    saved = api.put("/api/connectors/mail", json={"client_id": "client-1", "client_secret": SECRET, "scopes": [READ, DRAFT]})
    assert saved.status_code == 200 and saved.json()["configured"] is True and saved.json()["client"] == {"client_id": "client-1", "has_secret": True, "scope": f"{READ} {DRAFT}"}
    assert SECRET not in saved.text and SECRET not in api.get("/api/connectors").text and SECRET not in api.get("/api/mcp/servers").text
    started = api.post("/api/mcp/servers/mail/oauth/start")
    assert started.status_code == 200
    state, code = authorize(started.json()["url"])
    assert api.get(f"{CALLBACK_PATH}?state={state}&code={code}", follow_redirects=False).status_code == 303
    assert api.get("/api/connectors").json()[0]["authorized"] is True
    assert api.delete("/api/connectors/mail").status_code == 200
    assert api.get("/api/connectors").json()[0]["configured"] is False
    assert api.delete("/api/connectors/mail").status_code == 404


def test_the_api_reports_bad_input_and_unknown_connectors(api: TestClient, connector: Connector) -> None:
    assert api.put("/api/connectors/ghost", json={"client_id": "x", "client_secret": "y", "scopes": [READ]}).status_code == 404
    refused = api.put("/api/connectors/mail", json={"client_id": "x", "client_secret": "y", "scopes": []})
    assert refused.status_code == 422 and "at least one" in refused.json()["detail"]
    assert api.put("/api/connectors/mail", json={"client_id": "x", "scopes": [READ]}).status_code == 422   # no secret yet


def test_the_api_needs_a_signed_in_admin(client: TestClient, connector: Connector) -> None:
    assert client.get("/api/connectors").status_code == 401
    assert client.put("/api/connectors/mail", json={"client_id": "x", "client_secret": "y", "scopes": [READ]}).status_code in (401, 403)
    assert client.delete("/api/connectors/mail").status_code in (401, 403)
