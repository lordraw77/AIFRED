"""Tests for the dashboard files and the status and conversation endpoints (aifred.web, aifred.gateways.api_system)."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aifred.app import Services
from aifred.web import STATIC
from tests.conftest import login
from tests.fake_llm import FakeLLM, Text


@pytest.fixture
def api(client: TestClient) -> TestClient:
    client.headers["X-CSRF-Token"] = login(client)
    return client


def services_of(client: TestClient) -> Services:
    services: Services = client.app.state.services  # type: ignore[attr-defined]
    return services


# Files


def test_index_is_served_without_login_and_with_a_strict_policy(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/html")
    policy = response.headers["content-security-policy"]
    assert "default-src 'none'" in policy and "script-src 'self'" in policy and "style-src 'self'" in policy and "frame-ancestors 'none'" in policy
    assert "unsafe-inline" not in policy and "unsafe-eval" not in policy and "http" not in policy
    assert response.headers["x-content-type-options"] == "nosniff" and response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"


def test_index_has_no_inline_code_and_no_external_resources() -> None:
    html = (STATIC / "index.html").read_text()
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html), "inline script"
    assert " style=" not in html and "<style" not in html and "onclick=" not in html
    assert not re.search(r"""(?:src|href)=["']https?://""", html), "external resource"
    for path in re.findall(r"""(?:src|href)=["'](/static/[^"']+)""", html):
        assert (STATIC / path.removeprefix("/static/")).is_file(), path


def test_assets_are_served(client: TestClient) -> None:
    for path, kind in (("/static/app.css", "text/css"), ("/static/js/main.js", "javascript"), ("/static/brand/favicon.svg", "image/svg+xml"), ("/static/brand/icon-192.png", "image/png")):
        response = client.get(path)
        assert response.status_code == 200 and kind in response.headers["content-type"], path
        assert "content-security-policy" in response.headers
    assert client.get("/favicon.ico").status_code == 200
    assert client.get("/static/../pyproject.toml").status_code in (404, 400)
    assert client.get("/static/js/nope.js").status_code == 404


def test_manifest_points_at_existing_icons() -> None:
    manifest = json.loads((STATIC / "manifest.webmanifest").read_text())
    assert manifest["name"] == "AIFRED" and manifest["icons"]
    for icon in manifest["icons"]:
        assert icon["src"].startswith("/static/") and (STATIC / icon["src"].removeprefix("/static/")).is_file(), icon["src"]


def test_scripts_never_build_html_from_data() -> None:
    """Message text must always go through textContent (spec 10.4): these APIs would let data become markup."""
    forbidden = ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function", "setAttribute(\"style\"", "srcdoc")
    for script in sorted((STATIC / "js").rglob("*.js")):
        text = script.read_text()
        for word in forbidden:
            assert word not in text, f"{script.name} uses {word}"
    assert not list(STATIC.rglob("*.html")) or [p.name for p in STATIC.rglob("*.html")] == ["index.html"]


def test_page_modules_exist_for_every_route() -> None:
    main = (STATIC / "js" / "main.js").read_text()
    for module in re.findall(r'import\("\./pages/([a-z]+)\.js"\)', main):
        assert (STATIC / "js" / "pages" / f"{module}.js").is_file(), module


def test_api_responses_are_not_cached(api: TestClient) -> None:
    response = api.get("/api/status")
    assert response.headers["cache-control"] == "no-store" and response.headers["x-content-type-options"] == "nosniff"
    assert "content-security-policy" not in response.headers  # only pages and assets carry it


def test_assets_are_revalidated_so_an_update_shows_at_once(api: TestClient) -> None:
    response = api.get("/static/js/pages/models.js")
    assert response.headers["cache-control"] == "no-cache" and response.headers["etag"]
    assert api.get("/static/js/pages/models.js", headers={"If-None-Match": response.headers["etag"]}).status_code == 304


# Status


def test_status_requires_login(client: TestClient) -> None:
    assert client.get("/api/status").status_code == 401
    assert client.get("/api/sessions").status_code == 401


def test_status(api: TestClient) -> None:
    status = api.get("/api/status").json()
    assert status["version"] and status["uptime"] >= 0 and status["sessions"] == 0 and status["messages"] == 0
    assert status["telegram"]["state"] == "stopped" and status["telegram"]["open"] is True
    assert status["mcp"] == [] and status["rag"] is None and status["vault"]["ok"] is True
    assert status["db_bytes"] > 0 and Path(status["data_dir"]).is_dir()
    assert "TOKEN" not in json.dumps(status)


# Conversations


def test_conversations(api: TestClient, fake_llm: Callable[[], FakeLLM]) -> None:
    services = services_of(api)
    fake = fake_llm()
    fake.always = Text("An answer that is long enough to need a preview.")
    p = services.providers.create({"slug": "fake", "type": "local", "base_url": fake.url})
    services.providers.save_models(p.id, [{"model_id": "m"}])
    services.store.model_set_test(p.id, "m", True)
    services.llm.set_route("default", [{"provider": "fake", "model": "m"}])
    with api.stream("POST", "/api/chat", json={"session_id": "web:abc", "message": "hello"}) as response:
        assert response.status_code == 200
        response.read()
    rows = api.get("/api/sessions").json()
    assert [r["id"] for r in rows] == ["web:abc"] and rows[0]["message_count"] == 2 and rows[0]["source"] == "web"
    assert rows[0]["preview"].startswith("An answer that is long")
    assert api.get("/api/sessions?source=telegram").json() == []
    assert [r["id"] for r in api.get("/api/sessions?source=web&limit=1").json()] == ["web:abc"]
    messages = api.get("/api/sessions/web:abc/messages").json()
    assert [m["role"] for m in messages] == ["user", "assistant"] and messages[0]["content"] == "hello"
    assert api.get("/api/sessions/web:abc/messages?limit=1").json()[0]["role"] == "assistant"
    assert api.put("/api/sessions/web:abc", json={"title": "Renamed"}).status_code == 200
    assert api.get("/api/sessions").json()[0]["title"] == "Renamed"
    assert api.put("/api/sessions/web:none", json={"title": "x"}).status_code == 404
    assert api.put("/api/sessions/web:abc", json={"title": ""}).status_code == 422
    stats = api.get("/api/status").json()
    assert stats["sessions"] == 1 and stats["messages"] == 2 and stats["sources"] == ["web"]
    assert api.delete("/api/sessions/web:abc").status_code == 200
    assert api.delete("/api/sessions/web:abc").status_code == 404
    assert api.get("/api/sessions").json() == [] and api.get("/api/sessions/web:abc/messages").json() == []
