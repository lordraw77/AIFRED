"""Tests for the web chat endpoint (aifred.gateways.api_chat)."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from aifred.app import Services
from aifred.gateways import api_chat
from tests.conftest import login
from tests.fake_llm import FakeLLM, Reasoning, Status, Text, ToolCall

SESSION = "web:3f2b6c1e-0000-4000-8000-000000000001"


def services_of(client: TestClient) -> Services:
    services: Services = client.app.state.services  # type: ignore[attr-defined]
    return services


@pytest.fixture
def web(client: TestClient, fake_llm: Callable[[], FakeLLM]) -> TestClient:
    s = services_of(client)
    fake = fake_llm()
    client.fake = fake  # type: ignore[attr-defined]
    p = s.providers.create({"slug": "fake", "type": "local", "base_url": fake.url})
    s.providers.save_models(p.id, [{"model_id": "test-model"}])
    s.store.model_set_test(p.id, "test-model", True)
    s.llm.set_route("default", [{"provider": "fake", "model": "test-model"}])
    client.headers["X-CSRF-Token"] = login(client)
    return client


def events(web: TestClient, message: str, session: str = SESSION) -> list[dict[str, Any]]:
    with web.stream("POST", "/api/chat", json={"session_id": session, "message": message}) as resp:
        assert resp.status_code == 200 and resp.headers["content-type"].startswith("text/event-stream")
        return [json.loads(line[6:]) for line in resp.iter_lines() if line.startswith("data: ")]


def test_requires_an_admin(client: TestClient) -> None:
    assert client.post("/api/chat", json={"session_id": SESSION, "message": "hi"}).status_code == 401
    login(client)  # cookie without the CSRF header
    assert client.post("/api/chat", json={"session_id": SESSION, "message": "hi"}).status_code == 403
    chat_key, _ = services_of(client).auth.create_api_key("bot", "chat", None)
    assert client.post("/api/chat", json={"session_id": SESSION, "message": "hi"}, headers={"Authorization": f"Bearer {chat_key}"}).status_code == 403
    admin_key, _ = services_of(client).auth.create_api_key("root", "admin", None)
    assert client.post("/api/chat", json={"session_id": SESSION, "message": "/help"}, headers={"Authorization": f"Bearer {admin_key}"}).status_code == 200


def test_a_plain_turn_streams_events(web: TestClient) -> None:
    web.fake.queue(Text("Hello world", chunks=2))  # type: ignore[attr-defined]
    ev = events(web, "hi")
    assert [e["type"] for e in ev] == ["token", "token", "done"]
    assert "".join(e["text"] for e in ev if e["type"] == "token") == "Hello world"
    assert ev[-1]["content"] == "Hello world" and ev[-1]["usage"]["total_tokens"] == 15 and ev[-1]["model"] == "fake/test-model"
    assert [m["role"] for m in services_of(web).store.messages(SESSION)] == ["user", "assistant"]


def test_tool_use_is_visible_to_the_dashboard(web: TestClient) -> None:
    web.fake.queue(ToolCall("session_search", {"query": "x"}), Text("Found nothing."))  # type: ignore[attr-defined]
    ev = events(web, "look it up")
    assert [e["type"] for e in ev if e["type"] != "token"] == ["tool_call", "tool_result", "done"]
    call = next(e for e in ev if e["type"] == "tool_call")
    assert call["name"] == "session_search" and json.loads(call["arguments"]) == {"query": "x"}


def test_errors_arrive_as_events(web: TestClient) -> None:
    web.fake.always = Status(402)  # type: ignore[attr-defined]
    ev = events(web, "hi")
    assert [e["type"] for e in ev] == ["error"] and "quota exhausted" in ev[0]["message"] and ev[0]["code"] == "model"


def test_slash_commands_answer_with_one_event(web: TestClient) -> None:
    [ev] = events(web, "/think on")
    assert ev == {"type": "command", "text": "Reasoning is on for this conversation.", "session_id": None}
    assert services_of(web).agent.thinking(SESSION) is True
    [new] = events(web, "/new")
    assert new["type"] == "command" and new["session_id"].startswith("web:") and new["session_id"] != SESSION
    assert events(web, "/nonsense")[0]["text"].startswith("Unknown command /nonsense")
    assert "/model" in events(web, "/help")[0]["text"] and "/quit" not in events(web, "/help")[0]["text"]
    assert web.fake.requests == []  # type: ignore[attr-defined]  # commands never reach the model


def test_the_session_id_is_validated(web: TestClient) -> None:
    for bad in ("cli:1", "api:x", "web:", "web:a b", "tg:5:1", "../etc", "web:" + "x" * 65, ""):
        resp = web.post("/api/chat", json={"session_id": bad, "message": "hi"})
        assert resp.status_code == 422, bad
    assert web.post("/api/chat", json={"session_id": SESSION, "message": "   "}).status_code == 422
    assert web.post("/api/chat", json={"session_id": SESSION}).status_code == 422
    assert web.fake.requests == []  # type: ignore[attr-defined]


def test_dashboard_changes_apply_to_the_next_turn(web: TestClient) -> None:
    web.put("/api/config", json={"values": {"LLM_TEMPERATURE": 0.1}})
    web.fake.queue(Text("ok"))  # type: ignore[attr-defined]
    events(web, "hi")
    assert web.fake.requests[0].body["temperature"] == 0.1  # type: ignore[attr-defined]


def test_keep_alive_comments(web: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api_chat, "KEEPALIVE_SECONDS", 0.15)
    web.fake.queue(Reasoning(pieces=4, gap=0.3, then="done"))  # type: ignore[attr-defined]
    with web.stream("POST", "/api/chat", json={"session_id": SESSION, "message": "think"}) as resp:
        lines = [line for line in resp.iter_lines() if line]
    assert sum(1 for line in lines if line == ": keep-alive") >= 2 and lines[-1].startswith("data: ") and '"done"' in lines[-1]


def test_closing_the_stream_releases_the_session(web: TestClient) -> None:
    web.fake.queue(Text("a long answer " * 20, chunks=20), Text("second"))  # type: ignore[attr-defined]
    with web.stream("POST", "/api/chat", json={"session_id": SESSION, "message": "one"}) as resp:
        next(resp.iter_lines())
    assert [e["type"] for e in events(web, "two")][-1] == "done"
