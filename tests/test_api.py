"""Tests for the HTTP API (aifred.gateways.api)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from aifred.app import create_app
from aifred.config import Settings
from tests.conftest import ADMIN_PASSWORD, ADMIN_USER, login


def test_health_is_public(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/config"),
        ("PUT", "/api/config"),
        ("GET", "/api/keys"),
        ("POST", "/api/keys"),
        ("GET", "/api/auth/session"),
        ("POST", "/api/auth/logout"),
    ],
)
def test_api_requires_auth(client: TestClient, method: str, path: str) -> None:
    assert client.request(method, path, json={}).status_code == 401


def test_docs_are_not_exposed(client: TestClient) -> None:
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


def test_login_cookie_flags(client: TestClient) -> None:
    resp = client.post("/api/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASSWORD})
    assert resp.status_code == 200
    cookie = resp.headers["set-cookie"]
    assert "aifred_session=" in cookie
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie and "Secure" in cookie
    assert resp.json()["username"] == ADMIN_USER


def test_login_failure_and_rate_limit(client: TestClient) -> None:
    for _ in range(5):
        resp = client.post("/api/auth/login", json={"username": ADMIN_USER, "password": "wrong password!!"})
        assert resp.status_code == 401
    resp = client.post("/api/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASSWORD})
    assert resp.status_code == 429
    assert int(resp.headers["retry-after"]) > 0
    assert "Try again" in resp.json()["detail"]


def test_csrf_required_for_writes(client: TestClient) -> None:
    csrf = login(client)
    assert client.get("/api/config").status_code == 200
    body = {"values": {"LLM_TEMPERATURE": 0.3}}
    assert client.put("/api/config", json=body).status_code == 403
    assert client.put("/api/config", json=body, headers={"X-CSRF-Token": "forged"}).status_code == 403
    assert client.put("/api/config", json=body, headers={"X-CSRF-Token": csrf}).status_code == 200


def test_session_endpoint_returns_csrf(client: TestClient) -> None:
    csrf = login(client)
    assert client.get("/api/auth/session").json()["csrf_token"] == csrf


def test_logout_invalidates_server_side(client: TestClient) -> None:
    csrf = login(client)
    cookie = client.cookies.get("aifred_session")
    assert client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 200
    client.cookies.set("aifred_session", cookie or "")  # replay the old cookie
    assert client.get("/api/config").status_code == 401


def test_config_round_trip_hides_secrets(client: TestClient) -> None:
    csrf = login(client)
    headers = {"X-CSRF-Token": csrf}
    resp = client.put(
        "/api/config",
        json={"values": {"TELEGRAM_TOKEN": "999:VERY-SECRET", "NOTIFICATIONS": "verbose"}},
        headers=headers,
    )
    assert resp.status_code == 200
    assert "VERY-SECRET" not in resp.text
    items = {i["key"]: i for i in resp.json()}
    assert items["TELEGRAM_TOKEN"]["value"] == "••••" and items["TELEGRAM_TOKEN"]["set"] is True
    assert items["NOTIFICATIONS"]["value"] == "verbose"

    exported = client.get("/api/config/export").json()
    assert exported == {"version": 1, "settings": {"NOTIFICATIONS": "verbose"}}

    bad = client.put("/api/config", json={"values": {"MAX_ITERATIONS": "many"}}, headers=headers)
    assert bad.status_code == 422 and "MAX_ITERATIONS" in bad.json()["detail"]

    imp = client.post("/api/config/import", json={"version": 1, "settings": {"TELEGRAM_TOKEN": "x"}}, headers=headers)
    assert imp.status_code == 422
    imp = client.post("/api/config/import", json={"version": 1, "settings": {"LLM_THINKING": "on"}}, headers=headers)
    assert imp.status_code == 200


def test_api_keys_lifecycle_and_scopes(client: TestClient) -> None:
    csrf = login(client)
    headers = {"X-CSRF-Token": csrf}
    created = client.post("/api/keys", json={"name": "ops", "scope": "admin"}, headers=headers)
    assert created.status_code == 201
    admin_key = created.json()["key"]
    chat_key = client.post("/api/keys", json={"name": "bot"}, headers=headers).json()["key"]

    listing = client.get("/api/keys").text
    assert admin_key not in listing and chat_key not in listing

    bearer = TestClient(client.app, base_url="https://testserver")
    assert bearer.get("/api/config", headers={"Authorization": f"Bearer {admin_key}"}).status_code == 200
    # Bearer requests need no CSRF token
    put = bearer.put(
        "/api/config", json={"values": {"LOG_LEVEL": "WARNING"}}, headers={"Authorization": f"Bearer {admin_key}"}
    )
    assert put.status_code == 200
    assert bearer.get("/api/config", headers={"Authorization": f"Bearer {chat_key}"}).status_code == 403
    assert bearer.get("/api/config", headers={"Authorization": "Bearer aif_nope"}).status_code == 401

    key_id = created.json()["id"]
    assert client.delete(f"/api/keys/{key_id}", headers=headers).status_code == 200
    assert client.delete(f"/api/keys/{key_id}", headers=headers).status_code == 404
    assert bearer.get("/api/config", headers={"Authorization": f"Bearer {admin_key}"}).status_code == 401

    past = client.post("/api/keys", json={"name": "old", "expires_at": "2000-01-01T00:00:00Z"}, headers=headers)
    assert past.status_code == 422


def test_password_change(client: TestClient) -> None:
    csrf = login(client)
    headers = {"X-CSRF-Token": csrf}
    resp = client.post(
        "/api/auth/password", json={"current_password": "wrong password!!", "new_password": "x" * 12}, headers=headers
    )
    assert resp.status_code == 403
    resp = client.post(
        "/api/auth/password", json={"current_password": ADMIN_PASSWORD, "new_password": "short"}, headers=headers
    )
    assert resp.status_code == 422
    resp = client.post(
        "/api/auth/password",
        json={"current_password": ADMIN_PASSWORD, "new_password": "the new password"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert client.get("/api/config").status_code == 200  # current session kept
    assert login(client, "the new password")


def test_login_sessions_listing(client: TestClient) -> None:
    csrf = login(client)
    sessions = client.get("/api/auth/sessions").json()
    assert len(sessions) == 1 and sessions[0]["current"] is True
    resp = client.delete(f"/api/auth/sessions/{sessions[0]['id']}", headers={"X-CSRF-Token": csrf})
    assert resp.status_code == 200
    assert client.get("/api/config").status_code == 401


def test_restart_keeps_state(settings: Settings) -> None:
    with TestClient(create_app(settings), base_url="https://testserver") as c:
        csrf = login(c)
        c.put("/api/config", json={"values": {"HISTORY_LIMIT": 80}}, headers={"X-CSRF-Token": csrf})
    with TestClient(create_app(settings), base_url="https://testserver") as c:
        login(c)
        items = {i["key"]: i["value"] for i in c.get("/api/config").json()}
        assert items["HISTORY_LIMIT"] == 80


def test_missing_bootstrap_fails_fast(settings: Settings) -> None:
    empty = settings.model_copy(update={"admin_user": None, "admin_password": None})
    with pytest.raises(ValueError, match="No admin"):
        create_app(empty)


def test_request_urls_are_not_logged(client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    """httpx logs full URLs at INFO; a fetched URL may carry a secret in its query string."""
    import logging

    caplog.set_level(logging.DEBUG)
    logging.getLogger("httpx").info('HTTP Request: GET https://example.com/?api_key=SHOULD-NOT-APPEAR "HTTP/1.1 200 OK"')
    assert "SHOULD-NOT-APPEAR" not in caplog.text
