"""Tests for the OpenAI-compatible API (aifred.gateways.api_openai)."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from aifred.app import Services
from aifred.gateways import api_openai
from tests.conftest import login
from tests.fake_llm import Break, FakeLLM, Reasoning, Status, Text, ToolCall


def services_of(client: TestClient) -> Services:
    services: Services = client.app.state.services  # type: ignore[attr-defined]
    return services


@pytest.fixture
def api(client: TestClient, fake_llm: Callable[[], FakeLLM]) -> TestClient:
    """The app with one fake chat provider and a `chat` API key sent as the default header."""
    s = services_of(client)
    fake = fake_llm()
    client.fake = fake  # type: ignore[attr-defined]
    p = s.providers.create({"slug": "fake", "type": "local", "base_url": fake.url})
    s.providers.save_models(p.id, [{"model_id": "test-model"}])
    s.store.model_set_test(p.id, "test-model", True)
    s.llm.set_route("default", [{"provider": "fake", "model": "test-model"}])
    key, _ = s.auth.create_api_key("bot", "chat", None)
    client.headers["Authorization"] = f"Bearer {key}"
    return client


def post(api: TestClient, body: dict[str, Any] | None = None, **kw: Any) -> Any:
    payload = {"messages": [{"role": "user", "content": "hello"}]} | (body or {})
    return api.post("/v1/chat/completions", json=payload, **kw)


def sse(api: TestClient, body: dict[str, Any]) -> tuple[list[Any], list[str]]:
    """Return the decoded `data:` payloads (with the literal [DONE]) and the comment lines."""
    with api.stream("POST", "/v1/chat/completions", json={"messages": [{"role": "user", "content": "hello"}], "stream": True} | body) as resp:
        assert resp.status_code == 200 and resp.headers["content-type"].startswith("text/event-stream")
        lines = [line for line in resp.iter_lines() if line]
    data = [line[6:] if line[6:] == "[DONE]" else json.loads(line[6:]) for line in lines if line.startswith("data: ")]
    return data, [line for line in lines if line.startswith(":")]


def chat_of(api: TestClient) -> FakeLLM:
    return api.fake  # type: ignore[attr-defined,no-any-return]


# Authentication ----------------------------------------------------------------------------------------


def error_of(resp: Any) -> dict[str, Any]:
    body = resp.json()
    assert set(body) == {"error"} and set(body["error"]) == {"message", "type", "param", "code"}  # OpenAI's shape
    return body["error"]  # type: ignore[no-any-return]


def test_requires_a_valid_key(client: TestClient) -> None:
    for headers in ({}, {"Authorization": "Bearer aif_wrong"}, {"Authorization": "Basic abc"}, {"Authorization": "Bearer "}):
        resp = client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "x"}]}, headers=headers)
        assert resp.status_code == 401 and error_of(resp)["code"] == "invalid_api_key" and resp.headers["www-authenticate"] == "Bearer"
        assert client.get("/v1/models", headers=headers).status_code == 401


def test_a_dashboard_session_is_not_enough(client: TestClient) -> None:
    login(client)
    assert client.get("/v1/models").status_code == 401  # /v1 takes API keys only


def test_revoked_and_expired_keys_are_refused(api: TestClient) -> None:
    s = services_of(api)
    key, key_id = s.auth.create_api_key("temp", "chat", None)
    assert api.get("/v1/models", headers={"Authorization": f"Bearer {key}"}).status_code == 200
    s.auth.revoke_api_key(key_id)
    assert api.get("/v1/models", headers={"Authorization": f"Bearer {key}"}).status_code == 401


def test_admin_keys_work_too(api: TestClient) -> None:
    key, _ = services_of(api).auth.create_api_key("root", "admin", None)
    assert api.get("/v1/models", headers={"Authorization": f"Bearer {key}"}).status_code == 200


def test_models_lists_routes_and_tested_chat_models(api: TestClient) -> None:
    s = services_of(api)
    p = s.providers.by_slug("fake")
    assert p is not None
    s.providers.save_models(p.id, [{"model_id": "untested"}, {"model_id": "embed-x", "kind": "embedding"}])
    s.store.model_set_test(p.id, "embed-x", True)
    body = api.get("/v1/models").json()
    ids = [m["id"] for m in body["data"]]
    assert body["object"] == "list" and ids == ["default", "review", "fake/test-model"]
    assert all(m["object"] == "model" and m["owned_by"] == "aifred" for m in body["data"])


# Non-streaming -----------------------------------------------------------------------------------------


def test_a_plain_completion(api: TestClient) -> None:
    chat_of(api).queue(Text("Hi there!", usage=(21, 4)))
    resp = post(api)
    assert resp.status_code == 200
    body = resp.json()
    assert body["object"] == "chat.completion" and body["id"].startswith("chatcmpl-") and isinstance(body["created"], int)
    assert body["model"] == "fake/test-model"
    assert body["choices"] == [{"index": 0, "message": {"role": "assistant", "content": "Hi there!"}, "finish_reason": "stop"}]
    assert body["usage"] == {"prompt_tokens": 21, "completion_tokens": 4, "total_tokens": 25}  # the real counts
    assert services_of(api).store.get_session("api:default") is not None


def test_only_the_last_user_message_is_used_and_the_server_keeps_the_history(api: TestClient) -> None:
    fake = chat_of(api)
    post(api, {"messages": [
        {"role": "system", "content": "you are a pirate"}, {"role": "user", "content": "first question"},
        {"role": "assistant", "content": "client-side answer"}, {"role": "user", "content": "second question"},
    ]})
    sent = fake.requests[0].body["messages"]
    assert [m["role"] for m in sent] == ["system", "user"] and sent[1]["content"] == "second question"
    assert "pirate" not in sent[0]["content"] and "first question" not in json.dumps(sent)  # client history and system prompt are ignored
    post(api, {"messages": [{"role": "user", "content": "third question"}]})
    again = [m["content"] for m in fake.requests[1].body["messages"] if m["role"] != "system"]
    assert again == ["second question", "ok", "third question"]  # the server remembered the first turn


def test_client_tools_and_other_parameters_are_ignored(api: TestClient) -> None:
    fake = chat_of(api)
    resp = post(api, {"tools": [{"type": "function", "function": {"name": "evil", "parameters": {}}}], "temperature": 2, "max_tokens": 5, "top_p": 0.1, "response_format": {"type": "json_object"}})
    assert resp.status_code == 200
    assert "evil" not in json.dumps(fake.requests[0].body.get("tools", []))
    assert fake.requests[0].body["temperature"] == 0.6  # the configured value, not the client's


def test_content_parts_are_accepted_as_text(api: TestClient) -> None:
    fake = chat_of(api)
    resp = post(api, {"messages": [{"role": "user", "content": [{"type": "text", "text": "part one"}, {"type": "text", "text": "part two"}]}]})
    assert resp.status_code == 200 and fake.requests[0].body["messages"][-1]["content"] == "part one\npart two"


def test_session_selection_and_namespacing(api: TestClient) -> None:
    s = services_of(api)
    post(api, {"user": "alice"})
    post(api, headers={"X-AIFRED-Session": "from-header"})
    post(api, {"user": "ignored"}, headers={"X-AIFRED-Session": "wins"})
    assert {r["id"] for r in s.store.list_sessions()} == {"api:alice", "api:from-header", "api:wins"}
    for bad in ("cli:1", "../x", "a b", "x" * 65, "web:abc", "tg:1:2"):
        resp = post(api, headers={"X-AIFRED-Session": bad})
        assert resp.status_code == 400 and error_of(resp)["code"] == "invalid_session", bad
    assert post(api, {"user": "bad name!"}).status_code == 400
    assert not any(r["id"].startswith(("cli:", "tg:", "web:")) for r in s.store.list_sessions())  # never another gateway's session


def test_the_model_field_picks_a_route_for_that_request(api: TestClient, fake_llm: Callable[[], FakeLLM]) -> None:
    s = services_of(api)
    other = fake_llm()
    p = s.providers.create({"slug": "other", "type": "local", "base_url": other.url})
    s.providers.save_models(p.id, [{"model_id": "m2"}])
    s.store.model_set_test(p.id, "m2", True)
    s.llm.set_route("fast", [{"provider": "other", "model": "m2"}])
    other.queue(Text("fast answer"), Text("pinned answer"))
    resp = post(api, {"model": "fast"})
    assert resp.json()["choices"][0]["message"]["content"] == "fast answer" and resp.json()["model"] == "other/m2"
    assert post(api, {"model": "other/m2"}).json()["choices"][0]["message"]["content"] == "pinned answer"
    for default in ("default", "", "aifred", None):
        assert post(api, {"model": default}).json()["model"] == "fake/test-model"
    assert s.agent.route_spec("api:default", "api") == "default"  # a request never changes the session's route


def test_unknown_models_are_404(api: TestClient) -> None:
    for model in ("gpt-4", "nowhere/model", "other"):
        resp = post(api, {"model": model})
        assert resp.status_code == 404 and error_of(resp)["code"] == "model_not_found", model
    assert chat_of(api).requests == []


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"messages": []}, None), ({"messages": [{"role": "assistant", "content": "x"}]}, "missing_user_message"),
        ({"messages": [{"role": "user", "content": "  "}]}, None), ({"messages": [{"role": "user", "content": None}]}, None),
        ({"messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]}]}, "unsupported_content"),
        ({"messages": [{"role": "user", "content": "x" * 100_001}]}, "string_above_max_length"), ({"n": 2}, "unsupported_parameter"),
        ({"messages": "hello"}, None), ({"messages": [{"content": "no role"}]}, None),
    ],
)
def test_bad_requests_are_400(api: TestClient, body: dict[str, Any], code: str | None) -> None:
    resp = api.post("/v1/chat/completions", json=({"messages": [{"role": "user", "content": "x"}]} | body))
    assert resp.status_code == 400
    if code:
        assert error_of(resp)["code"] == code
    else:
        error_of(resp)


def test_malformed_bodies_are_400(api: TestClient) -> None:
    for content in (b"not json", b"[1, 2]", b"", b"null"):
        resp = api.post("/v1/chat/completions", content=content, headers={"content-type": "application/json"})
        assert resp.status_code == 400 and error_of(resp)["type"] == "invalid_request_error"
    assert api.post("/v1/chat/completions").status_code == 400


def test_no_provider_is_503(client: TestClient) -> None:
    key, _ = services_of(client).auth.create_api_key("bot", "chat", None)
    resp = client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "x"}]}, headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 503 and error_of(resp)["code"] == "no_provider" and "dashboard" in error_of(resp)["message"]


def test_upstream_failure_is_502(api: TestClient) -> None:
    chat_of(api).always = Status(402)
    resp = post(api)
    assert resp.status_code == 502 and error_of(resp)["code"] == "upstream_error" and "quota exhausted" in error_of(resp)["message"]


def test_server_side_tools_run_invisibly(api: TestClient) -> None:
    chat_of(api).queue(ToolCall("memory", {"action": "add", "target": "user", "content": "Prefers tea"}), Text("Noted."))
    resp = post(api)
    assert resp.json()["choices"][0]["message"]["content"] == "Noted." and "tool_calls" not in resp.json()["choices"][0]["message"]
    assert services_of(api).memory.snapshot("user").entries == ["Prefers tea"]
    assert resp.json()["usage"]["total_tokens"] > 15  # both model calls are counted


# Streaming ---------------------------------------------------------------------------------------------


def test_a_streamed_completion(api: TestClient) -> None:
    chat_of(api).queue(Text("Hello world", chunks=3))
    data, comments = sse(api, {})
    assert data[-1] == "[DONE]" and comments == []
    chunks = [d for d in data[:-1]]
    assert all(c["object"] == "chat.completion.chunk" and c["id"] == chunks[0]["id"] for c in chunks)
    assert chunks[0]["choices"][0]["delta"] == {"role": "assistant", "content": ""} and chunks[0]["choices"][0]["finish_reason"] is None
    assert "".join(c["choices"][0]["delta"].get("content", "") for c in chunks) == "Hello world"
    assert chunks[-1]["choices"][0]["delta"] == {} and chunks[-1]["choices"][0]["finish_reason"] == "stop"
    assert all("usage" not in c for c in chunks)


def test_streamed_usage_on_request(api: TestClient) -> None:
    chat_of(api).queue(Text("x", usage=(30, 2)))
    data, _ = sse(api, {"stream_options": {"include_usage": True}})
    usage_chunk = data[-2]
    assert usage_chunk["choices"] == [] and usage_chunk["usage"] == {"prompt_tokens": 30, "completion_tokens": 2, "total_tokens": 32}
    assert data[-1] == "[DONE]"


def test_tools_do_not_appear_in_the_stream(api: TestClient) -> None:
    chat_of(api).queue(ToolCall("session_search", {"query": "x"}), Text("Only the answer."))
    data, _ = sse(api, {})
    text = json.dumps(data)
    assert "session_search" not in text and "tool_calls" not in text
    assert "".join(c["choices"][0]["delta"].get("content", "") for c in data[:-1]) == "Only the answer."


def test_a_failure_before_the_first_token_is_a_normal_http_error(api: TestClient) -> None:
    chat_of(api).always = Status(503)
    resp = api.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "x"}], "stream": True})
    assert resp.status_code == 502 and resp.headers["content-type"].startswith("application/json") and error_of(resp)["code"] == "upstream_error"


def test_a_failure_in_the_middle_ends_with_an_error_chunk_and_no_retry(api: TestClient) -> None:
    fake = chat_of(api)
    fake.queue(Break("partial answer"))
    data, _ = sse(api, {})
    assert data[-1] == "[DONE]"
    assert "partial answer" in "".join(c["choices"][0]["delta"].get("content", "") for c in data[:-2] if "choices" in c)
    error = data[-2]
    assert set(error) == {"error"} and error["error"]["code"] == "upstream_error"
    assert len(fake.requests) == 1  # the streaming API does not retry mid-stream (spec 6.1)


def test_keep_alive_comments_while_the_model_is_busy(api: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api_openai, "KEEPALIVE_SECONDS", 0.15)
    chat_of(api).queue(Reasoning(pieces=4, gap=0.3, then="done thinking"))
    data, comments = sse(api, {})
    assert len(comments) >= 2 and all(c == ": keep-alive" for c in comments)
    assert "".join(c["choices"][0]["delta"].get("content", "") for c in data[:-1]) == "done thinking" and data[-1] == "[DONE]"


def test_a_closed_stream_frees_the_session(api: TestClient) -> None:
    chat_of(api).queue(Text("a long answer " * 20, chunks=20), Text("second answer"))
    with api.stream("POST", "/v1/chat/completions", json={"messages": [{"role": "user", "content": "one"}], "stream": True}, headers={"X-AIFRED-Session": "s1"}) as resp:
        next(resp.iter_lines())  # read the first line, then walk away
    follow = post(api, {"messages": [{"role": "user", "content": "two"}]}, headers={"X-AIFRED-Session": "s1"}, timeout=10)
    assert follow.status_code == 200
