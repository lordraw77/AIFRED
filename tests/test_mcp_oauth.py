"""Tests for OAuth sign-in to MCP servers (aifred.mcp_oauth and its wiring)."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from aifred.app import Services
from aifred.mcp import McpConfigError
from aifred.mcp_client import McpError, OAuthMcpClient
from aifred.mcp_oauth import CALLBACK_PATH, CIMD_PATH, OAuthError, pkce_pair, secret_name
from tests.conftest import login
from tests.fake_mcp import FakeMcp
from tests.fake_oauth import FakeOAuth

REDIRECT = "http://127.0.0.1:8700" + CALLBACK_PATH


@pytest.fixture
def oauth_server() -> Iterator[FakeOAuth]:
    server = FakeOAuth().start()
    yield server
    server.stop()


@pytest.fixture
def mcp(fake_mcp: Callable[..., FakeMcp], oauth_server: FakeOAuth) -> FakeMcp:
    return fake_mcp(oauth_issuer=oauth_server.issuer, token_ok=lambda header: header.removeprefix("Bearer ") in oauth_server.access_tokens)


def authorize(url: str) -> tuple[str, str]:
    """What the browser does: open the authorization page and follow its redirect; returns the state and the code."""
    response = httpx.get(url, follow_redirects=False)
    assert response.status_code == 302
    query = parse_qs(urlsplit(response.headers["location"]).query)
    return query["state"][0], query["code"][0]


def sign_in(services: Services, mcp: FakeMcp, name: str = "remote") -> None:
    services.config.update({"MCP_OAUTH_CLIENT_ID": "aifred-test"})
    url = services.mcp.oauth.begin(name, mcp.url, REDIRECT)
    state, code = authorize(url)
    assert services.mcp.oauth.finish(state, code) == name


def test_pkce_pair_is_s256() -> None:
    import base64
    import hashlib

    verifier, challenge = pkce_pair()
    assert challenge == base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode() and len(verifier) >= 43


def test_discovery_reads_the_protected_resource_and_the_authorization_server(services: Services, mcp: FakeMcp, oauth_server: FakeOAuth) -> None:
    found = services.mcp.oauth.discover(mcp.url)
    assert found["authorization_endpoint"] == f"{oauth_server.issuer}/authorize" and found["token_endpoint"] == f"{oauth_server.issuer}/token"
    assert found["resource"] == mcp.url and found["cimd"] is True and found["scopes"] == ["mcp"]


def test_discovery_refuses_what_it_cannot_trust(services: Services, mcp: FakeMcp, oauth_server: FakeOAuth, fake_mcp: Callable[..., FakeMcp]) -> None:
    plain = fake_mcp()                                                              # no OAuth metadata at all
    with pytest.raises(OAuthError, match="does not publish OAuth metadata"):
        services.mcp.oauth.discover(plain.url)
    oauth_server.pkce = False
    with pytest.raises(OAuthError, match="PKCE"):
        services.mcp.oauth.discover(mcp.url)
    oauth_server.pkce = True
    elsewhere = fake_mcp(oauth_issuer="http://auth.example.org")                    # http on a host that is not the MCP server's
    with pytest.raises(OAuthError, match="not https"):
        services.mcp.oauth.discover(elsewhere.url)
    unreachable = fake_mcp(oauth_issuer="https://127.0.0.1:1")
    with pytest.raises(OAuthError, match="cannot read the metadata"):
        services.mcp.oauth.discover(unreachable.url)


def test_begin_builds_a_pkce_request_with_the_resource(services: Services, mcp: FakeMcp, oauth_server: FakeOAuth) -> None:
    services.config.update({"MCP_OAUTH_CLIENT_ID": "aifred-test"})
    url = services.mcp.oauth.begin("remote", mcp.url, REDIRECT)
    query = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
    assert url.startswith(f"{oauth_server.issuer}/authorize?") and query["response_type"] == "code" and query["client_id"] == "aifred-test"
    assert query["code_challenge_method"] == "S256" and len(query["code_challenge"]) == 43 and query["redirect_uri"] == REDIRECT
    assert query["resource"] == mcp.url and query["scope"] == "mcp" and len(query["state"]) >= 32


def test_the_client_identity_comes_from_a_setting_or_a_metadata_document(services: Services, mcp: FakeMcp, oauth_server: FakeOAuth) -> None:
    with pytest.raises(OAuthError, match="no client id"):
        services.mcp.oauth.begin("remote", mcp.url, REDIRECT)
    services.config.update({"MCP_OAUTH_PUBLIC_URL": "https://aifred.example.org"})
    url = services.mcp.oauth.begin("remote", mcp.url, REDIRECT)
    assert parse_qs(urlsplit(url).query)["client_id"] == [f"https://aifred.example.org{CIMD_PATH}"]
    document = services.mcp.oauth.client_metadata()
    assert document and document["client_id"] == f"https://aifred.example.org{CIMD_PATH}" and document["redirect_uris"] == [f"https://aifred.example.org{CALLBACK_PATH}"]
    assert document["token_endpoint_auth_method"] == "none"
    oauth_server.cimd = False                                                       # a provider without metadata documents needs a registered id
    with pytest.raises(OAuthError, match="does not accept client metadata documents"):
        services.mcp.oauth.begin("remote", mcp.url, REDIRECT)


def test_full_sign_in_stores_tokens_and_the_server_accepts_them(services: Services, mcp: FakeMcp, oauth_server: FakeOAuth, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    sign_in(services, mcp)
    status = services.mcp.oauth.status("remote")
    assert status["authorized"] and status["scope"] == "mcp" and status["expires_at"]
    token = services.mcp.oauth.token("remote")
    assert token == "at-1" and "at-1" not in str(status)
    form = oauth_server.token_forms[0]
    assert form["grant_type"] == "authorization_code" and form["client_id"] == "aifred-test" and form["resource"] == mcp.url and len(form["code_verifier"]) >= 43
    assert "at-1" not in caplog.text and "rt-1" not in caplog.text                  # tokens are never logged
    assert services.mcp._vault.get(secret_name("remote"))                            # and they are in the vault


def test_state_is_single_use_and_unknown_state_is_refused(services: Services, mcp: FakeMcp) -> None:
    services.config.update({"MCP_OAUTH_CLIENT_ID": "aifred-test"})
    state, code = authorize(services.mcp.oauth.begin("remote", mcp.url, REDIRECT))
    assert services.mcp.oauth.finish(state, code) == "remote"
    with pytest.raises(OAuthError, match="unknown or has expired"):
        services.mcp.oauth.finish(state, code)                                      # replay
    with pytest.raises(OAuthError, match="unknown or has expired"):
        services.mcp.oauth.finish("forged", "code")


def test_a_sign_in_expires(services: Services, mcp: FakeMcp, monkeypatch: pytest.MonkeyPatch) -> None:
    services.config.update({"MCP_OAUTH_CLIENT_ID": "aifred-test"})
    state, code = authorize(services.mcp.oauth.begin("remote", mcp.url, REDIRECT))
    now = services.mcp.oauth._clock()
    monkeypatch.setattr(services.mcp.oauth, "_clock", lambda: now + 3600)
    with pytest.raises(OAuthError, match="expired"):
        services.mcp.oauth.finish(state, code)


def test_a_refused_code_exchange_reports_the_reason_and_stores_nothing(services: Services, mcp: FakeMcp, oauth_server: FakeOAuth) -> None:
    services.config.update({"MCP_OAUTH_CLIENT_ID": "aifred-test"})
    state, code = authorize(services.mcp.oauth.begin("remote", mcp.url, REDIRECT))
    oauth_server.fail_token = True
    with pytest.raises(OAuthError, match="refused on purpose"):
        services.mcp.oauth.finish(state, code)
    assert not services.mcp.oauth.status("remote")["authorized"]


def test_the_token_is_refreshed_before_it_expires(services: Services, mcp: FakeMcp, oauth_server: FakeOAuth) -> None:
    oauth_server.expires_in = 30                                                    # inside the refresh margin at once
    sign_in(services, mcp)
    assert services.mcp.oauth.token("remote") == "at-2"                             # refreshed, and the refresh token rotated
    assert oauth_server.token_forms[-1]["grant_type"] == "refresh_token" and oauth_server.token_forms[-1]["resource"] == mcp.url
    oauth_server.fail_token = True
    with pytest.raises(OAuthError, match="refused on purpose"):
        services.mcp.oauth.token("remote")


def test_a_long_lived_token_is_not_refreshed_and_a_missing_one_is_an_error(services: Services, mcp: FakeMcp, oauth_server: FakeOAuth) -> None:
    with pytest.raises(OAuthError, match="not authorized"):
        services.mcp.oauth.token("remote")
    sign_in(services, mcp)
    assert services.mcp.oauth.token("remote") == "at-1" and len(oauth_server.token_forms) == 1
    services.mcp.oauth.forget("remote")
    with pytest.raises(OAuthError, match="not authorized"):
        services.mcp.oauth.token("remote")


def test_the_client_sends_a_fresh_token_on_every_request(services: Services, mcp: FakeMcp, oauth_server: FakeOAuth) -> None:
    oauth_server.expires_in = 30
    sign_in(services, mcp)
    services.mcp.save("remote", {"url": mcp.url, "oauth": True})
    server = services.mcp.get("remote")
    assert server is not None and server.oauth
    client = services.mcp_manager._default_factory(server, {})
    assert isinstance(client, OAuthMcpClient)
    client.connect()
    assert [t.name for t in client.list_tools().tools][:2] == ["echo", "add"]
    sent = {r.headers.get("authorization") for r in mcp.requests if r.verb == "POST" and r.method != "ping"}
    assert sent <= {"Bearer at-2", "Bearer at-3", "Bearer at-4", "Bearer at-5", "Bearer at-6"} and "Bearer at-1" not in sent   # each call refreshed
    client.close()


def test_a_client_without_sign_in_fails_with_a_401_style_error(services: Services, mcp: FakeMcp) -> None:
    services.mcp.save("remote", {"url": mcp.url, "oauth": True})
    server = services.mcp.get("remote")
    assert server is not None
    client = services.mcp_manager._default_factory(server, {})
    with pytest.raises(McpError, match="not authorized") as caught:
        client.connect()
    assert caught.value.status == 401


def test_oauth_is_for_web_servers_and_is_dropped_with_the_server(services: Services, mcp: FakeMcp) -> None:
    import os
    import sys

    services.config.update({"MCP_STDIO_COMMANDS": [os.path.basename(sys.executable)]})
    with pytest.raises(McpConfigError, match="not for commands"):
        services.mcp.save("local", {"command": [sys.executable, "x.py"], "oauth": True})
    with pytest.raises(McpConfigError, match="true or false"):
        services.mcp.save("remote", {"url": mcp.url, "oauth": "yes"})
    sign_in(services, mcp)
    services.mcp.save("remote", {"url": mcp.url, "oauth": True})
    assert services.mcp.export()["servers"]["remote"]["oauth"] is True
    services.mcp.delete("remote")
    assert not services.mcp.oauth.status("remote")["authorized"]


# The dashboard API --------------------------------------------------------------------------------------


@pytest.fixture
def api(client: TestClient) -> TestClient:
    client.headers["X-CSRF-Token"] = login(client)
    return client


def test_api_sign_in_round_trip(api: TestClient, mcp: FakeMcp) -> None:
    services: Services = api.app.state.services  # type: ignore[attr-defined]
    services.config.update({"MCP_OAUTH_CLIENT_ID": "aifred-test"})
    saved = api.put("/api/mcp/servers/remote", json={"url": mcp.url, "oauth": True, "instruction": "Remote tools"})
    assert saved.status_code == 200 and saved.json()["oauth"] is True and saved.json()["oauth_status"]["authorized"] is False   # saved without the test
    started = api.post("/api/mcp/servers/remote/oauth/start")
    assert started.status_code == 200
    state, code = authorize(started.json()["url"])
    done = api.get(f"{CALLBACK_PATH}?state={state}&code={code}", follow_redirects=False)
    assert done.status_code == 303 and done.headers["location"] == "/#/mcp"
    listed = {s["name"]: s for s in api.get("/api/mcp/servers").json()}["remote"]
    assert listed["oauth_status"]["authorized"] is True and "at-1" not in str(listed)
    assert api.post("/api/mcp/servers/remote/oauth/forget").status_code == 200
    assert not services.mcp.oauth.status("remote")["authorized"]
    assert api.post("/api/mcp/servers/ghost/oauth/start").status_code == 404
    assert api.post("/api/mcp/servers/ghost/oauth/forget").status_code == 404


def test_api_callback_errors_are_shown_safely(api: TestClient, client: TestClient) -> None:
    refused = api.get(f"{CALLBACK_PATH}?error=access_denied&error_description=%3Cscript%3Ealert(1)%3C/script%3E")
    assert refused.status_code == 400 and "<script>" not in refused.text and "&lt;script&gt;" in refused.text
    assert api.get(f"{CALLBACK_PATH}?state=forged&code=x").status_code == 400
    assert api.get(CALLBACK_PATH).status_code == 400
    assert TestClient(client.app).get(f"{CALLBACK_PATH}?state=s&code=c").status_code == 401     # only the signed-in admin completes a sign-in


def test_api_start_reports_discovery_problems(api: TestClient, fake_mcp: Callable[..., FakeMcp]) -> None:
    plain = fake_mcp()
    api.put("/api/mcp/servers/plain", json={"url": plain.url, "oauth": True, "skip_test": True, "instruction": "x"})
    started = api.post("/api/mcp/servers/plain/oauth/start")
    assert started.status_code == 422 and "OAuth metadata" in started.json()["detail"]
    api.put("/api/mcp/servers/notoauth", json={"url": plain.url, "skip_test": True, "instruction": "x"})
    assert api.post("/api/mcp/servers/notoauth/oauth/start").status_code == 404


def test_the_client_metadata_document_is_public_only_when_configured(api: TestClient, client: TestClient) -> None:
    anon = TestClient(client.app)
    assert anon.get(CIMD_PATH).status_code == 404
    services: Services = api.app.state.services  # type: ignore[attr-defined]
    services.config.update({"MCP_OAUTH_PUBLIC_URL": "https://aifred.example.org"})
    document = anon.get(CIMD_PATH)
    assert document.status_code == 200 and document.json()["client_name"] == "AIFRED"


def test_redirects_are_never_followed_during_discovery(services: Services) -> None:
    from aifred.mcp_oauth import OAuthService

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.method == "POST":
            return httpx.Response(401)
        return httpx.Response(302, headers={"location": "http://127.0.0.1:9/internal-secret"})

    service = OAuthService(services.mcp._vault, services.config, transport=httpx.MockTransport(handler))
    with pytest.raises(OAuthError, match="does not publish OAuth metadata"):
        service.discover("https://mcp.example.org/mcp")
    assert seen and not any("internal-secret" in u for u in seen)                  # a redirect to an internal address is not followed


def test_only_a_limited_number_of_sign_ins_can_be_pending(services: Services, mcp: FakeMcp) -> None:
    services.config.update({"MCP_OAUTH_CLIENT_ID": "aifred-test"})
    for _ in range(20):
        services.mcp.oauth.begin("remote", mcp.url, REDIRECT)
    with pytest.raises(OAuthError, match="too many sign-ins"):
        services.mcp.oauth.begin("remote", mcp.url, REDIRECT)
