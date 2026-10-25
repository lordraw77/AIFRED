"""Tests for the LLM dashboard API (aifred.gateways.api_llm)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.conftest import login
from tests.fake_llm import FakeLLM, Status, Text, ToolCall

SECRET = "sk-VERY-SECRET-KEY-9999"


@pytest.fixture
def admin(client: TestClient) -> TestClient:
    client.headers["X-CSRF-Token"] = login(client)
    return client


def new_provider(admin: TestClient, fake: FakeLLM, slug: str = "lab", **kw: Any) -> dict[str, Any]:
    resp = admin.post("/api/providers", json={"slug": slug, "type": "local", "base_url": fake.url, **kw})
    assert resp.status_code == 201, resp.text
    return resp.json()  # type: ignore[no-any-return]


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/providers"),
        ("POST", "/api/providers"),
        ("GET", "/api/providers/presets"),
        ("GET", "/api/routes"),
        ("PUT", "/api/routes/default"),
        ("GET", "/api/llm/health"),
        ("GET", "/api/usage"),
    ],
)
def test_requires_admin(client: TestClient, method: str, path: str) -> None:
    assert client.request(method, path, json={}).status_code == 401


def test_presets(admin: TestClient) -> None:
    presets = {p["type"]: p for p in admin.get("/api/providers/presets").json()}
    assert presets["groq"]["base_url"] == "https://api.groq.com/openai/v1"
    assert presets["cloudflare"]["config_fields"] == ["account_id"]
    assert presets["ollama"]["key"] is False


def test_create_tests_connection_first(admin: TestClient, fake_llm: Callable[[], FakeLLM]) -> None:
    fake = fake_llm()
    fake.api_key = SECRET
    bad = admin.post("/api/providers", json={"slug": "lab", "type": "local", "base_url": fake.url, "api_key": "wrong-key-value"})
    assert bad.status_code == 422 and "rejected the API key" in bad.json()["detail"]
    assert admin.get("/api/providers").json() == []

    dead = admin.post("/api/providers", json={"slug": "lab", "type": "local", "base_url": "http://127.0.0.1:9/v1"})
    assert dead.status_code == 422 and "cannot reach" in dead.json()["detail"]
    saved = admin.post(
        "/api/providers", json={"slug": "lab", "type": "local", "base_url": "http://127.0.0.1:9/v1", "skip_test": True}
    )
    assert saved.status_code == 201

    good = admin.post("/api/providers", json={"slug": "lab2", "type": "local", "base_url": fake.url, "api_key": SECRET})
    assert good.status_code == 201
    assert good.json()["keys"][0]["last4"] == "9999" and SECRET not in good.text


def test_validation_errors_are_reported(admin: TestClient) -> None:
    resp = admin.post("/api/providers", json={"slug": "Bad Slug", "type": "local", "base_url": "http://x/v1", "skip_test": True})
    assert resp.status_code == 422 and "slug" in resp.json()["detail"]
    resp = admin.post("/api/providers", json={"slug": "x", "type": "local", "base_url": "http://x/v1", "budget_tokens_daily": 0})
    assert resp.status_code == 422


def test_secrets_never_leave_the_server(admin: TestClient, fake_llm: Callable[[], FakeLLM]) -> None:
    fake = fake_llm()
    p = new_provider(admin, fake, api_key=SECRET, verify_tls=False, headers={"X-Title": "AIFRED"})
    pid = p["id"]
    assert p["tls_unverified"] is True and p["headers"] == {"X-Title": "AIFRED"}
    added = admin.post(f"/api/providers/{pid}/keys", json={"value": "second-secret-8888", "label": "spare"})
    assert added.status_code == 201
    for text in (
        admin.get("/api/providers").text, added.text, admin.get("/api/llm/health").text,
        admin.get("/api/config").text, admin.get("/api/config/export").text,
        admin.get("/api/routes").text, admin.get(f"/api/providers/{pid}/models").text,
    ):
        assert SECRET not in text and "second-secret" not in text
    keys = admin.get("/api/providers").json()[0]["keys"]
    replaced = admin.put(f"/api/providers/{pid}/keys/{keys[1]['id']}", json={"value": "replacement-key-7777"})
    assert replaced.status_code == 200 and replaced.json()["keys"][1]["last4"] == "7777"
    assert admin.delete(f"/api/providers/{pid}/keys/{keys[1]['id']}").status_code == 200
    assert admin.delete(f"/api/providers/{pid}/keys/{keys[1]['id']}").status_code == 404


def test_update_and_delete_provider(admin: TestClient, fake_llm: Callable[[], FakeLLM]) -> None:
    p = new_provider(admin, fake_llm())
    upd = admin.put(f"/api/providers/{p['id']}", json={"enabled": False, "total_timeout": 120, "budget_tokens_daily": 5000})
    assert upd.status_code == 200
    body = upd.json()
    assert body["enabled"] is False and body["total_timeout"] == 120 and body["budget_tokens_daily"] == 5000
    assert body["slug"] == "lab"
    assert admin.put(f"/api/providers/{p['id']}", json={"total_timeout": 1}).status_code == 422
    assert admin.put("/api/providers/999", json={"enabled": True}).status_code == 422
    assert admin.delete(f"/api/providers/{p['id']}").status_code == 200
    assert admin.delete(f"/api/providers/{p['id']}").status_code == 404


def test_test_endpoint_reports_without_failing(admin: TestClient, fake_llm: Callable[[], FakeLLM]) -> None:
    fake = fake_llm()
    p = new_provider(admin, fake)
    assert admin.post(f"/api/providers/{p['id']}/test").json() == {"ok": True, "models": 1}
    fake.models_status = 500
    result = admin.post(f"/api/providers/{p['id']}/test").json()
    assert result["ok"] is False and "HTTP 500" in result["error"]


def test_models_refresh_test_and_route_flow(admin: TestClient, fake_llm: Callable[[], FakeLLM]) -> None:
    fake = fake_llm()
    fake.models = [{"id": "vendor/model-x", "context_length": 4096}]
    p = new_provider(admin, fake)
    pid = p["id"]
    refreshed = admin.post(f"/api/providers/{pid}/models/refresh").json()
    assert refreshed[0]["model_id"] == "vendor/model-x" and refreshed[0]["test_ok"] is None

    untested = admin.put("/api/routes/default", json={"entries": [{"provider": "lab", "model": "vendor/model-x"}]})
    assert untested.status_code == 422 and "tool-calling test" in untested.json()["detail"]

    fake.queue(ToolCall("ping", {"word": "pong"}))
    tested = admin.post(f"/api/providers/{pid}/models/vendor/model-x/test").json()  # ids contain slashes
    assert tested["ok"] is True
    overridden = admin.put(f"/api/providers/{pid}/models", json=[{"model_id": "vendor/model-x", "thinking_control": "qwen_template"}])
    assert overridden.json()[0]["thinking_control"] == "qwen_template"
    assert admin.put(f"/api/providers/{pid}/models", json=[{"model_id": "vendor/model-x", "thinking_control": "nope"}]).status_code == 422

    route = admin.put("/api/routes/default", json={"entries": [{"provider": "lab", "model": "vendor/model-x"}]})
    assert route.status_code == 200 and route.json()["entries"][0]["context_window"] == 4096
    assert [r["name"] for r in admin.get("/api/routes").json()] == ["default", "review"]
    assert admin.get("/api/routes/nope").status_code == 404

    fake.queue(Text("OK"))
    ran = admin.post("/api/routes/default/test").json()
    assert ran["ok"] is True and ran["entry"] == "lab/vendor/model-x" and ran["reply"] == "OK"


def test_route_test_reports_failures(admin: TestClient, fake_llm: Callable[[], FakeLLM]) -> None:
    empty = admin.post("/api/routes/default/test").json()
    assert empty["ok"] is False and "dashboard" in empty["error"]

    fake = fake_llm()
    p = new_provider(admin, fake)
    fake.queue(ToolCall("ping", {"word": "pong"}))
    admin.post(f"/api/providers/{p['id']}/models/test-model/test")
    admin.put("/api/routes/default", json={"entries": [{"provider": "lab", "model": "test-model"}]})
    fake.queue(Status(402))
    failed = admin.post("/api/routes/default/test").json()
    assert failed["ok"] is False and failed["failures"] == [{"entry": "lab", "reason": "quota exhausted"}]


def test_custom_route_lifecycle(admin: TestClient, fake_llm: Callable[[], FakeLLM]) -> None:
    fake = fake_llm()
    p = new_provider(admin, fake)
    fake.queue(ToolCall("ping", {"word": "pong"}))
    admin.post(f"/api/providers/{p['id']}/models/test-model/test")
    assert admin.put("/api/routes/cheap", json={"entries": [{"provider": "lab", "model": "test-model", "timeouts": {"first_token": 9}}]}).status_code == 200
    assert admin.get("/api/routes/cheap").json()["entries"][0]["timeouts"] == {"first_token": 9.0}
    assert admin.delete("/api/routes/cheap").status_code == 200
    assert admin.delete("/api/routes/default").status_code == 422


def test_health_reset_and_usage(admin: TestClient, fake_llm: Callable[[], FakeLLM]) -> None:
    fake = fake_llm()
    p = new_provider(admin, fake, api_key=SECRET)
    fake.queue(ToolCall("ping", {"word": "pong"}))
    admin.post(f"/api/providers/{p['id']}/models/test-model/test")
    admin.put("/api/routes/default", json={"entries": [{"provider": "lab", "model": "test-model"}]})
    fake.queue(Status(402), Text("fine"))
    assert admin.post("/api/routes/default/test").json()["ok"] is False

    [health] = admin.get("/api/llm/health").json()
    assert health["entries"][0]["state"] == "cooldown" or health["keys"][0]["state"] == "cooldown"
    entry_id = health["entries"][0]["entry_id"]
    assert admin.post(f"/api/llm/health/{entry_id}/reset").status_code == 200
    [health] = admin.get("/api/llm/health").json()
    assert health["entries"][0]["state"] == "healthy" and health["keys"][0]["state"] == "healthy"
    assert admin.post("/api/llm/health/9999/reset").status_code == 404

    assert admin.post("/api/routes/default/test").json()["ok"] is True
    rows = admin.get("/api/usage?days=3").json()
    assert rows[0]["provider"] == "lab" and rows[0]["calls"] >= 1 and rows[0]["errors"] >= 1
    assert admin.get("/api/usage?days=0").status_code == 422


def test_write_endpoints_need_csrf(client: TestClient, fake_llm: Callable[[], FakeLLM]) -> None:
    login(client)  # cookie only, no CSRF header
    resp = client.post("/api/providers", json={"slug": "x", "type": "local", "base_url": fake_llm().url})
    assert resp.status_code == 403
