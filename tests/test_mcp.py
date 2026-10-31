"""Tests for MCP server definitions, the connection manager and their integration (aifred.mcp)."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from aifred.app import Services
from aifred.mcp import McpConfigError, McpServer, secret_name
from aifred.tools.registry import ToolContext, ToolError
from tests.conftest import login
from tests.fake_llm import FakeLLM, Text, ToolCall
from tests.fake_mcp import FakeMcp

Make = Callable[..., FakeMcp]


def wait_for(condition: Callable[[], bool], seconds: float = 10.0) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if condition():
            return
        time.sleep(0.02)
    raise AssertionError("condition not reached")


def state_of(services: Services, name: str) -> str:
    return next((s["state"] for s in services.mcp_manager.status() if s["name"] == name), "missing")


@pytest.fixture
def mcp_up(services: Services, fake_mcp: Make) -> Callable[..., FakeMcp]:
    """Define a server on a fake and wait until it is connected."""

    def add(name: str = "fake", **options: Any) -> FakeMcp:
        server = fake_mcp(**options)
        services.mcp.save(name, {"url": server.url, **({"verify_tls": False} if options.get("tls") else {})})
        services.mcp_manager.start()
        services.mcp_manager.reload()
        wait_for(lambda: state_of(services, name) == "connected")
        return server

    return add


# Definitions


def test_validation(services: Services) -> None:
    svc = services.mcp
    for name in ("Bad", "has-dash", "", "x" * 25):
        with pytest.raises(McpConfigError, match="name"):
            svc.save(name, {"url": "http://h/mcp"})
    for url in ("", "ftp://h/mcp", "http:///mcp", "javascript:alert(1)", "http://user:pw@h/mcp"):
        with pytest.raises(McpConfigError):
            svc.save("ok", {"url": url})
    bad: list[dict[str, Any]] = [
        {"headers": {"Content-Type": "x"}}, {"headers": {"Mcp-Session-Id": "x"}}, {"headers": {"bad name": "x"}}, {"headers": {"X": "a\nb"}},
        {"headers": {"X": ""}}, {"headers": []}, {"timeout": 0}, {"timeout": "soon"}, {"connect_timeout": 9999}, {"max_tools": 0}, {"max_tools": True},
        {"protocol_version": "1999-01-01"}, {"include": "echo"}, {"exclude": [1]}, {"verify_tls": "/no/such/ca.pem"}, {"enabled": "yes"},
    ]
    for extra in bad:
        with pytest.raises(McpConfigError):
            svc.save("ok", {"url": "http://h/mcp", **extra})
    assert svc.list() == [] and [e for e in services.store.journal_list() if e["action"] == "mcp_server"] == []


def test_save_encrypts_headers_and_never_returns_them(services: Services) -> None:
    svc = services.mcp
    svc.save("proxmox", {"url": "http://10.70.0.20:8080/mcp", "headers": {"Authorization": "Bearer SECRET-TOKEN-123", "X-Org": "acme"}})
    assert svc.headers("proxmox") == {"Authorization": "Bearer SECRET-TOKEN-123", "X-Org": "acme"}
    public = svc.public(svc.list()[0])
    assert public["headers"] == ["Authorization", "X-Org"] and "SECRET" not in str(public)
    assert "SECRET" not in str(services.store.mcp_server_list()) and "SECRET" not in str(services.store.journal_list())
    assert "SECRET" not in str(svc.export()) and svc.export()["servers"]["proxmox"]["headers"] == {"Authorization": None, "X-Org": None}
    blob = services.store.secret_get(secret_name("proxmox"))
    assert blob is not None and b"SECRET" not in blob


def test_update_keeps_or_replaces_header_values(services: Services) -> None:
    svc = services.mcp
    svc.save("s", {"url": "http://h/mcp", "headers": {"Authorization": "Bearer one", "X-A": "a"}})
    svc.save("s", {"timeout": 30})  # headers untouched
    assert svc.headers("s") == {"Authorization": "Bearer one", "X-A": "a"} and svc.list()[0].timeout == 30
    svc.save("s", {"headers": {"Authorization": None, "X-B": "b"}})  # None keeps the stored value, the rest is replaced
    assert svc.headers("s") == {"Authorization": "Bearer one", "X-B": "b"}
    with pytest.raises(McpConfigError, match="no stored value"):
        svc.save("s", {"headers": {"X-New": None}})
    svc.save("s", {"headers": {}})
    assert svc.headers("s") == {} and services.store.secret_get(secret_name("s")) is None and svc.list()[0].header_names == ()


def test_delete_removes_secrets_and_journals(services: Services) -> None:
    svc = services.mcp
    svc.save("gone", {"url": "https://h/mcp", "headers": {"X-A": "a"}, "verify_tls": False})
    summaries = [e["summary"] for e in services.store.journal_list(kind="settings")]
    assert "MCP server 'gone' added (TLS verification disabled)" in summaries
    svc.delete("gone")
    assert svc.list() == [] and services.store.secret_get(secret_name("gone")) is None
    assert services.store.journal_list(kind="settings")[0]["summary"] == "MCP server 'gone' deleted"
    with pytest.raises(McpConfigError, match="no MCP server"):
        svc.delete("gone")


def test_defaults_come_from_settings(services: Services) -> None:
    services.mcp.save("d", {"url": "http://h/mcp"})
    server = services.mcp.list()[0]
    assert (server.timeout, server.connect_timeout, server.max_tools) == (None, None, None)
    public = services.mcp.public(server)
    assert (public["timeout"], public["connect_timeout"], public["max_tools"]) == (120, 15, 40)
    services.config.update({"MCP_CALL_TIMEOUT": 77, "MCP_MAX_TOOLS_PER_SERVER": 9})
    assert services.mcp.public(server)["timeout"] == 77 and services.mcp.public(server)["max_tools"] == 9


def test_import_file_once(services: Services, tmp_path: Any) -> None:
    path = services.settings.data_dir / "mcp.json"
    path.write_text(json.dumps({"servers": {
        "proxmox": {"url": "http://10.0.0.1/mcp", "headers": {"Authorization": "Bearer IMPORT-SECRET"}},
        "docs": {"url": "https://docs.example.com/mcp", "include": ["search_docs"], "timeout": 60},
        "lab": {"url": "https://192.168.1.50:9443/mcp", "verify_tls": False},
        "broken": {"url": "nope"},
    }}))
    assert sorted(services.mcp.import_file(path)) == ["docs", "lab", "proxmox"]
    assert not path.exists()  # deleted so no header value stays in clear
    assert services.mcp.headers("proxmox") == {"Authorization": "Bearer IMPORT-SECRET"} and "IMPORT-SECRET" not in str(services.store.mcp_server_list())
    docs = next(s for s in services.mcp.list() if s.name == "docs")
    assert docs.include == ("search_docs",) and docs.timeout == 60
    path.write_text(json.dumps({"servers": {"again": {"url": "http://x/mcp"}}}))
    assert services.mcp.import_file(path) == [] and path.exists()  # only the first time


def test_import_file_problems(services: Services) -> None:
    path = services.settings.data_dir / "mcp.json"
    assert services.mcp.import_file(path) == []  # no file
    path.write_text("{not json")
    assert services.mcp.import_file(path) == [] and path.exists()
    path.write_text(json.dumps({"servers": []}))
    assert services.mcp.import_file(path) == []


# Manager


def test_connects_in_the_background_and_exposes_tools(services: Services, mcp_up: Callable[..., FakeMcp]) -> None:
    mcp_up("fake")
    status = services.mcp_manager.status()[0]
    assert status["state"] == "connected" and status["protocol"] == "2026-07-28" and status["tools"] == 6 and status["latency_ms"] is not None
    names = [t.name for t in services.mcp_manager.tools()]
    assert names == [f"mcp__fake__{n}" for n in ("echo", "add", "query", "picture", "broken", "ask")]  # the server's order, deterministic
    tool = next(t for t in services.mcp_manager.tools() if t.name == "mcp__fake__echo")
    assert tool.toolset == "mcp:fake" and not tool.restricted_ok
    assert tool.schema()["function"]["parameters"]["required"] == ["message"] and "additionalProperties" not in tool.schema()["function"]["parameters"]


def test_unreachable_server_never_blocks_and_recovers(services: Services, fake_mcp: Make) -> None:
    server = fake_mcp(token="t0k")
    services.mcp.save("late", {"url": server.url})  # no credentials yet: refused
    services.mcp_manager.start()
    wait_for(lambda: state_of(services, "late") == "error")
    status = services.mcp_manager.status()[0]
    assert "refused the credentials" in status["error"] and services.mcp_manager.tools() == []
    with pytest.raises(Exception, match="not connected.*refused the credentials"):
        services.mcp_manager.call("late", "echo", {})
    services.mcp.save("late", {"headers": {"Authorization": "Bearer t0k"}})
    services.mcp_manager.reload()  # the changed definition connects again at once
    wait_for(lambda: state_of(services, "late") == "connected")


def test_retry_backoff_grows_to_a_cap(services: Services, fake_mcp: Make) -> None:
    from aifred.mcp import RETRY_MAX, RETRY_START, ServerState

    server = fake_mcp()
    server.stop()
    services.mcp.save("down", {"url": server.url, "connect_timeout": 1})
    now = [100.0]
    manager = services.mcp_manager
    manager._clock = lambda: now[0]
    state = ServerState(services.mcp.list()[0])
    waits = []
    for _ in range(6):
        manager._connect(state)
        waits.append(state.next_try - now[0])
        now[0] = state.next_try
    assert waits == [RETRY_START, 10, 20, 40, RETRY_MAX, RETRY_MAX] and state.status == "error" and "cannot connect" in state.error


def test_filters_cap_and_hash_suffix(services: Services, mcp_up: Callable[..., FakeMcp]) -> None:
    server = mcp_up("fake")
    server.tools += [{"name": "a" * 80, "description": "", "inputSchema": {"type": "object"}}, {"name": "a" * 80 + "b", "description": "", "inputSchema": {"type": "object"}},
                     {"name": "x.y", "description": "", "inputSchema": {"type": "object"}}, {"name": "x_y", "description": "", "inputSchema": {"type": "object"}}]
    services.mcp.save("fake", {"max_tools": 500})
    services.mcp_manager.reload()
    wait_for(lambda: state_of(services, "fake") == "connected" and len(services.mcp_manager.tools()) == 10)
    names = [t.name for t in services.mcp_manager.tools()]
    assert all(len(n) <= 64 and n.replace("_", "").replace("-", "").isalnum() for n in names) and len(set(names)) == len(names)
    assert "mcp__fake__x_y" in names and any(n.startswith("mcp__fake__x_y_") for n in names)  # x.y mapped onto x_y collides: hash suffix
    services.mcp.save("fake", {"include": ["echo", "add"], "max_tools": 40})
    services.mcp_manager.reload()
    wait_for(lambda: sorted(t.name for t in services.mcp_manager.tools()) == ["mcp__fake__add", "mcp__fake__echo"])
    services.mcp.save("fake", {"include": [], "exclude": ["echo", "add"], "max_tools": 3})
    services.mcp_manager.reload()
    wait_for(lambda: len(services.mcp_manager.tools()) == 3)
    assert not {"mcp__fake__echo", "mcp__fake__add"} & {t.name for t in services.mcp_manager.tools()}


def test_disabled_and_removed_servers(services: Services, mcp_up: Callable[..., FakeMcp]) -> None:
    mcp_up("fake")
    services.mcp.save("fake", {"enabled": False})
    services.mcp_manager.reload()
    assert state_of(services, "fake") == "disabled" and services.mcp_manager.tools() == []
    services.mcp.save("fake", {"enabled": True})
    services.mcp_manager.reload()
    wait_for(lambda: state_of(services, "fake") == "connected")
    services.mcp.delete("fake")
    services.mcp_manager.reload()
    assert services.mcp_manager.status() == [] and services.mcp_manager.tools() == []


def test_tool_changes_are_journaled(services: Services, mcp_up: Callable[..., FakeMcp]) -> None:
    server = mcp_up("fake")
    state = services.mcp_manager._states["fake"]
    assert services.store.journal_list(kind="mcp") == []  # the first read is only recorded
    server.tools[0]["description"] = "Now it exfiltrates"
    server.tools.pop()
    server.tools.append({"name": "fresh", "description": "", "inputSchema": {"type": "object"}})
    services.mcp_manager._refresh(state)
    [entry] = services.store.journal_list(kind="mcp")
    assert "changed: echo" in entry["summary"] and "added: fresh" in entry["summary"] and "removed: ask" in entry["summary"]
    services.mcp_manager._refresh(state)
    assert len(services.store.journal_list(kind="mcp")) == 1  # nothing new, nothing logged


def test_cached_list_refreshes_after_ttl_and_after_errors(services: Services, mcp_up: Callable[..., FakeMcp]) -> None:
    server = mcp_up("fake", ttl_ms=10_000)
    state = services.mcp_manager._states["fake"]
    lists = server.methods().count("tools/list")
    assert not services.mcp_manager._due(state, time.monotonic()) and state.refresh_at - time.monotonic() > 5
    with pytest.raises(ToolError):
        services.mcp_manager.call("fake", "nope", {})
    assert state.stale and services.mcp_manager._due(state, time.monotonic())
    wait_for(lambda: server.methods().count("tools/list") > lists and not state.stale)


def test_losing_a_server_keeps_known_tools_and_reports_readable_errors(services: Services, mcp_up: Callable[..., FakeMcp]) -> None:
    server = mcp_up("fake")
    server.stop()
    state = services.mcp_manager._states["fake"]
    services.mcp_manager._refresh(state)
    assert state.status == "error" and state.client is None and len(services.mcp_manager.tools()) == 6
    with pytest.raises(Exception, match="MCP server 'fake' is not connected \\(cannot connect"):
        services.mcp_manager.call("fake", "echo", {"message": "x"})


def test_test_connection_reports_without_connecting_the_server(services: Services, fake_mcp: Make) -> None:
    server = fake_mcp()
    result = services.mcp_manager.test(McpServer("t", server.url), {})
    assert result["ok"] and result["protocol"] == "2026-07-28" and "echo" in result["tools"] and result["server"] == "fake-mcp"
    server.token = "x"
    failure = services.mcp_manager.test(McpServer("t", server.url), {})
    assert not failure["ok"] and "credentials" in failure["error"] and failure["tools"] == []
    assert services.mcp_manager.status() == []


# Registry and agent


def toolsets(services: Services, value: list[str], source: str = "cli") -> list[str]:
    services.config.update({"TOOLSETS": value})
    ctx = ToolContext(services, "cli:t", source)
    return [t.name for t in services.tools.for_context(ctx) if t.name.startswith("mcp__")]


def test_toolset_selection(services: Services, mcp_up: Callable[..., FakeMcp]) -> None:
    mcp_up("fake")
    mcp_up("other")
    assert toolsets(services, ["memory"]) == []
    assert len(toolsets(services, ["mcp"])) == 12
    only = toolsets(services, ["mcp:fake"])
    assert len(only) == 6 and all(n.startswith("mcp__fake__") for n in only)
    assert toolsets(services, ["mcp:nope"]) == []
    services.config.update({"TOOLSETS_API": ["mcp:other"]})
    assert {n.split("__")[1] for n in toolsets(services, ["mcp"], "api")} == {"other"}  # a gateway list wins


def test_never_exposed_to_restricted_callers(services: Services, mcp_up: Callable[..., FakeMcp]) -> None:
    mcp_up("fake")
    services.config.update({"TOOLSETS": ["mcp", "web", "search", "skills"], "TOOLSETS_TELEGRAM": ["mcp", "web"]})
    ctx = ToolContext(services, "tg:1:1", "telegram", restricted=True)
    assert [t for t in services.tools.for_context(ctx) if t.name.startswith("mcp__")] == []
    open_ctx = ToolContext(services, "tg:1:1", "telegram", restricted=False)
    assert len([t for t in services.tools.for_context(open_ctx) if t.name.startswith("mcp__")]) == 6


def test_registries_do_not_share_sources(settings: Any, fake_mcp: Make) -> None:
    first, second = Services.open(settings), None
    try:
        first.bootstrap()
        server = fake_mcp()
        first.mcp.save("fake", {"url": server.url})
        first.mcp_manager.start()
        wait_for(lambda: state_of(first, "fake") == "connected")
        settings2 = settings.model_copy(update={"data_dir": settings.data_dir.parent / "other"})
        second = Services.open(settings2)
        assert any(t.name.startswith("mcp__") for t in first.tools.all()) and not any(t.name.startswith("mcp__") for t in second.tools.all())
    finally:
        first.close()
        if second:
            second.close()


@pytest.mark.anyio
async def test_the_agent_calls_an_mcp_tool(services: Services, llm_ready: FakeLLM, mcp_up: Callable[..., FakeMcp]) -> None:
    server = mcp_up("fake")
    services.config.update({"TOOLSETS": ["mcp:fake"]})
    llm_ready.queue(ToolCall("mcp__fake__echo", {"message": "ping"}), Text("The server said hello."))
    events = [e async for e in services.agent.run("cli:mcp", "ask the server", "cli")]
    assert events[-1].content == "The server said hello."  # type: ignore[union-attr]
    names = [t["function"]["name"] for t in llm_ready.requests[0].body["tools"]]
    assert "mcp__fake__echo" in names and "memory" not in names
    followup = llm_ready.requests[1].body["messages"][-1]
    assert followup["role"] == "tool" and followup["content"] == "echo: ping"
    assert server.requests[-1].body["params"]["arguments"] == {"message": "ping"}


@pytest.mark.anyio
async def test_execute_errors_reach_the_model(services: Services, mcp_up: Callable[..., FakeMcp]) -> None:
    mcp_up("fake")
    services.config.update({"TOOLSETS": ["mcp"]})
    ctx = ToolContext(services, "cli:e", "cli")
    run = services.tools.execute
    assert await run(ctx, "mcp__fake__broken", "{}") == "Error: it broke"
    assert (await run(ctx, "mcp__fake__nope", "{}")).startswith("Error: unknown tool")
    assert (await run(ctx, "mcp__fake__echo", "[1]")).startswith("Error: invalid arguments")
    services.config.update({"TOOL_OUTPUT_LIMIT": 500})
    out = await run(ctx, "mcp__fake__echo", json.dumps({"message": "y" * 2000}))
    assert out.endswith("characters]") and "[truncated" in out


@pytest.mark.anyio
async def test_slash_command(services: Services, mcp_up: Callable[..., FakeMcp]) -> None:
    from aifred.commands import Commands, Context

    commands = Commands(services)
    ctx = Context("cli:c", "cli")
    assert (await commands.run("/mcp", ctx)).text.startswith("No MCP servers")  # type: ignore[union-attr]
    mcp_up("fake")
    services.mcp.save("lab", {"url": "https://127.0.0.1:1/mcp", "verify_tls": False, "connect_timeout": 1})
    reply = await commands.run("/mcp reload", ctx)
    assert reply is not None and "- fake: connected, 6 tools, protocol 2026-07-28" in reply.text and "- lab:" in reply.text and "[TLS not verified]" in reply.text
    assert (await commands.run("/mcp nonsense", ctx)).text == "Usage: /mcp, /mcp reload or /mcp add <name> <url> <what it is for>"  # type: ignore[union-attr]
    restricted = await commands.run("/mcp", Context("tg:1:1", "telegram", True))
    assert restricted is not None and "not available" in restricted.text


# Dashboard API


@pytest.fixture
def api(client: TestClient) -> TestClient:
    client.headers["X-CSRF-Token"] = login(client)
    return client


def test_api_requires_the_admin(client: TestClient) -> None:
    assert client.get("/api/mcp/servers").status_code in (401, 403)
    assert client.put("/api/mcp/servers/x", json={"url": "http://h/mcp"}).status_code in (401, 403, 422)


def test_api_lifecycle(api: TestClient, fake_mcp: Make) -> None:
    server = fake_mcp(token="tok")
    body = {"url": server.url, "headers": {"Authorization": "Bearer tok"}, "include": ["echo", "add"], "instruction": "Fake tools for tests."}
    saved = api.put("/api/mcp/servers/fake", json=body)
    assert saved.status_code == 200, saved.text
    assert saved.json()["headers"] == ["Authorization"] and "tok" not in saved.text and saved.json()["include"] == ["echo", "add"]
    services: Services = api.app.state.services  # type: ignore[attr-defined]
    wait_for(lambda: state_of(services, "fake") == "connected")
    listed = api.get("/api/mcp/servers").json()
    assert listed[0]["state"] == "connected" and listed[0]["tools"] == 2 and listed[0]["protocol"] == "2026-07-28" and "tok" not in str(listed)
    tested = api.post("/api/mcp/servers/fake/test").json()
    assert tested["ok"] and sorted(tested["tools"])[0] == "add"
    assert api.post("/api/mcp/servers/none/test").status_code == 404
    assert "tok" not in api.get("/api/mcp/export").text and api.get("/api/mcp/export").json()["servers"]["fake"]["include"] == ["echo", "add"]
    assert api.post("/api/mcp/reload").status_code == 200
    assert api.delete("/api/mcp/servers/fake").status_code == 200 and api.get("/api/mcp/servers").json() == []
    assert api.delete("/api/mcp/servers/fake").status_code == 404


def test_api_rejects_what_does_not_connect(api: TestClient, fake_mcp: Make) -> None:
    server = fake_mcp(token="tok")
    bad = api.put("/api/mcp/servers/fake", json={"url": server.url, "instruction": "Fake tools."})
    assert bad.status_code == 422 and "connection test failed" in bad.text and "credentials" in bad.text
    assert api.get("/api/mcp/servers").json() == []
    assert api.put("/api/mcp/servers/fake", json={"url": server.url, "skip_test": True, "instruction": "Fake tools."}).status_code == 200  # saved anyway on request
    invalid = api.put("/api/mcp/servers/Bad Name", json={"url": server.url, "skip_test": True, "instruction": "x"})
    assert invalid.status_code == 422 and "name" in invalid.text
    assert api.put("/api/mcp/servers/x", json={"url": "ftp://h", "skip_test": True, "instruction": "x"}).status_code == 422
    assert api.put("/api/mcp/servers/fake", json={"headers": {"X-Missing": None}, "skip_test": True}).status_code == 422


def test_api_flags_unverified_tls(api: TestClient, fake_mcp: Make) -> None:
    server = fake_mcp(tls=True)
    assert api.put("/api/mcp/servers/lab", json={"url": server.url, "instruction": "Lab."}).status_code == 422  # self-signed is refused
    ok = api.put("/api/mcp/servers/lab", json={"url": server.url, "verify_tls": False, "instruction": "Lab."})
    assert ok.status_code == 200 and ok.json()["tls_unverified"] is True
    custom = api.put("/api/mcp/servers/lab", json={"verify_tls": str(server.cert)})
    assert custom.status_code == 200 and custom.json()["tls_unverified"] is False


# Advanced MCP: subscriptions, questions and sampling


def tool_names(services: Services) -> set[str]:
    return {t.name for t in services.mcp_manager.tools()}


def test_a_list_changed_notification_refreshes_the_tools_at_once(services: Services, mcp_up: Callable[..., FakeMcp]) -> None:
    server = mcp_up("fake")                    # the cache lives 5 minutes: only the stream can explain a quick refresh
    wait_for(lambda: any(r.method == "subscriptions/listen" for r in server.requests))
    assert "mcp__fake__fresh" not in tool_names(services)
    server.tools.append({"name": "fresh", "description": "New", "inputSchema": {"type": "object"}})
    server.events.put("tools")
    wait_for(lambda: "mcp__fake__fresh" in tool_names(services))


def test_subscriptions_can_be_switched_off(services: Services, fake_mcp: Make) -> None:
    services.config.update({"MCP_SUBSCRIPTIONS": False})
    server = fake_mcp()
    services.mcp.save("fake", {"url": server.url})
    services.mcp_manager.start()
    wait_for(lambda: state_of(services, "fake") == "connected")
    assert "subscriptions/listen" not in server.methods()


def test_a_server_without_the_stream_is_left_to_its_cache(services: Services, mcp_up: Callable[..., FakeMcp]) -> None:
    server = mcp_up("fake", list_changed=False)
    wait_for(lambda: any(r.method == "subscriptions/listen" for r in server.requests))
    time.sleep(0.5)
    assert [r.method for r in server.requests].count("subscriptions/listen") == 1 and state_of(services, "fake") == "connected"


@pytest.mark.anyio
async def test_the_model_asks_the_user_and_answers_with_mcp_reply(services: Services, fake_mcp: Make) -> None:
    server = fake_mcp()
    server.enable_mrtr()
    services.mcp.save("fake", {"url": server.url})
    services.mcp_manager.start()
    wait_for(lambda: state_of(services, "fake") == "connected")
    services.config.update({"TOOLSETS": ["mcp:fake"]})
    ctx = ToolContext(services, "cli:q", "cli")
    declared = [r for r in server.requests if r.method == "tools/list"][0].body["params"]["_meta"]["io.modelcontextprotocol/clientCapabilities"]
    assert declared == {"elicitation": {"form": {}}}                           # sampling stays off by default
    assert "mcp_reply" not in {t.name for t in services.tools.for_context(ctx)}   # nothing is waiting yet
    out = await services.tools.execute(ctx, "mcp__fake__ask", "{}")
    token = out.split("(request ")[1].split(")")[0]
    assert "Who are you?" in out and "mcp_reply" in {t.name for t in services.tools.for_context(ctx)}
    answer = {"request": token, "answers": {"who": {"action": "accept", "content": {"name": "Ada"}}}}
    assert await services.tools.execute(ctx, "mcp_reply", json.dumps(answer)) == 'hello {"name": "Ada"}'
    assert await services.tools.execute(ctx, "mcp_reply", json.dumps(answer)) == "Error: unknown tool 'mcp_reply'"   # answered once: nothing is waiting
    assert "mcp_reply" not in {t.name for t in services.tools.for_context(ctx)}
    assert "mcp_reply" not in {t.name for t in services.tools.for_context(ToolContext(services, "telegram:open:1", "telegram", True))}


def test_elicitation_can_be_switched_off(services: Services, fake_mcp: Make) -> None:
    services.config.update({"MCP_ELICITATION": False})
    server = fake_mcp()
    services.mcp.save("fake", {"url": server.url})
    services.mcp_manager.start()
    wait_for(lambda: state_of(services, "fake") == "connected")
    assert [r for r in server.requests if r.method == "tools/list"][0].body["params"]["_meta"]["io.modelcontextprotocol/clientCapabilities"] == {}


@pytest.mark.anyio
async def test_sampling_runs_the_default_model_for_the_server_only_when_allowed(services: Services, llm_ready: FakeLLM, fake_mcp: Make) -> None:
    server = fake_mcp()
    server.enable_mrtr()
    services.config.update({"MCP_SAMPLING": True, "MCP_SAMPLING_PER_HOUR": 1})
    services.mcp.save("fake", {"url": server.url})
    services.mcp_manager.start()
    wait_for(lambda: state_of(services, "fake") == "connected")
    services.config.update({"TOOLSETS": ["mcp:fake"]})
    ctx = ToolContext(services, "cli:s", "cli")
    assert [r for r in server.requests if r.method == "tools/list"][0].body["params"]["_meta"]["io.modelcontextprotocol/clientCapabilities"] == {"elicitation": {"form": {}}, "sampling": {}}
    llm_ready.queue(Text("Paris."))
    assert await services.tools.execute(ctx, "mcp__fake__think", "{}") == "model said: Paris."
    sent = llm_ready.requests[0].body["messages"]
    assert sent[0] == {"role": "system", "content": "Be brief."} and sent[1]["content"] == "Capital of France?"
    assert "was refused: at most 1 sampling requests an hour" in await services.tools.execute(ctx, "mcp__fake__think", "{}")
    usage = services.store._query("SELECT agent, source FROM llm_usage ORDER BY id DESC LIMIT 1")
    assert usage == [{"agent": "mcp:fake", "source": "mcp-sampling"}]


def test_sampling_refuses_anything_but_plain_text(services: Services) -> None:
    from aifred.mcp_client import McpError

    sample = services.mcp_sampler.sample
    ok = {"messages": [{"role": "user", "content": {"type": "text", "text": "hi"}}], "maxTokens": 10}
    with pytest.raises(McpError, match="turned off"):
        sample("s", ok)
    services.config.update({"MCP_SAMPLING": True})
    for bad, message in (({**ok, "tools": [{"name": "x"}]}, "with tools"), ({**ok, "messages": []}, "1 to 20 messages"), ({**ok, "messages": [{"role": "system", "content": {"type": "text", "text": "x"}}]}, "role user or assistant"),
                         ({**ok, "messages": [{"role": "user", "content": {"type": "image", "data": "x", "mimeType": "image/png"}}]}, "only text"),
                         ({**ok, "messages": [{"role": "user", "content": {"type": "text", "text": "x" * 30_000}}]}, "longer than")):
        with pytest.raises(McpError, match=message):
            sample("s", bad)
