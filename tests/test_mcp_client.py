"""Tests for the MCP client (aifred.mcp_client) against the fake server."""

from __future__ import annotations

import base64
from collections.abc import Callable
from typing import Any

import pytest

from aifred.mcp_client import MODERN, McpClient, McpError, format_result, header_value
from tests.fake_mcp import FakeMcp

Make = Callable[..., FakeMcp]


def client_for(server: FakeMcp, **kw: Any) -> McpClient:
    kw.setdefault("timeout", 5.0)
    kw.setdefault("connect_timeout", 5.0)
    return McpClient(server.url, **kw)


def connected(server: FakeMcp, **kw: Any) -> McpClient:
    client = client_for(server, **kw)
    client.connect()
    return client


# Helpers


def test_header_value_encoding() -> None:
    assert header_value("us-west1") == "us-west1"
    assert header_value("a b") == "a b"
    for odd in ("Hello, 世界", " padded ", "line1\nline2", "=?base64?literal?="):
        encoded = header_value(odd)
        assert encoded.startswith("=?base64?") and encoded.endswith("?=")
        assert base64.b64decode(encoded[9:-2]).decode() == odd
    assert header_value("Hello, 世界") == "=?base64?SGVsbG8sIOS4lueVjA==?="


def test_format_result() -> None:
    assert format_result({"content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}) == "a\nb"
    assert format_result({"content": [], "structuredContent": {"x": 1}}) == '{"x": 1}'
    assert format_result({"content": [{"type": "text", "text": "kept"}], "structuredContent": {"x": 1}}) == "kept"
    assert format_result({"content": []}) == "(no output)"
    assert format_result({"content": [{"type": "text", "text": "bad"}], "isError": True}) == "Error: bad"
    text = format_result({"content": [{"type": "image", "mimeType": "image/png", "data": "A" * 400}, {"type": "audio", "mimeType": "audio/wav", "data": ""},
                                      {"type": "resource", "resource": {"uri": "file:///a", "mimeType": "text/plain", "text": "hello"}},
                                      {"type": "resource_link", "uri": "file:///b"}, {"type": "weird"}]})
    assert "image/png, about 300 bytes" in text and "audio/wav" in text and "file:///a" in text and "5 characters" in text
    assert "hello" not in text and "file:///b" in text and "weird" in text


# Negotiation


def test_modern_server_is_detected_first(fake_mcp: Make) -> None:
    server = fake_mcp()
    client = connected(server)
    assert client.version == MODERN and client.server_name == "fake-mcp" and client.instructions == "Fake server"
    assert server.methods() == ["server/discover"]
    seen = server.requests[0]
    assert seen.headers["mcp-protocol-version"] == MODERN and seen.headers["mcp-method"] == "server/discover"
    assert "application/json" in seen.headers["accept"] and "text/event-stream" in seen.headers["accept"]
    meta = seen.body["params"]["_meta"]
    assert meta["io.modelcontextprotocol/protocolVersion"] == MODERN and meta["io.modelcontextprotocol/clientCapabilities"] == {}
    assert meta["io.modelcontextprotocol/clientInfo"]["name"] == "AIFRED"
    assert "initialize" not in server.methods() and not any("mcp-session-id" in r.headers for r in server.requests)


@pytest.mark.parametrize("style", ["empty400", "404", "405", "rpc200"])
def test_falls_back_to_the_handshake_when_the_answer_is_not_modern(fake_mcp: Make, style: str) -> None:
    server = fake_mcp(modern=False, reject_style=style)
    client = connected(server)
    assert client.version == "2025-11-25" and client.server_name == "fake-mcp-legacy"
    assert server.methods() == ["server/discover", "initialize", "notifications/initialized"]
    client.list_tools()
    follow = server.requests[-1]
    assert follow.headers["mcp-session-id"] == "session-1" and follow.headers["mcp-protocol-version"] == "2025-11-25"
    assert "mcp-method" not in follow.headers


def test_handshake_accepts_the_version_the_server_picks(fake_mcp: Make) -> None:
    server = fake_mcp(modern=False, legacy_versions=["2025-03-26"])
    assert connected(server).version == "2025-03-26"


def test_unsupported_version_error_leads_to_a_common_version(fake_mcp: Make) -> None:
    server = fake_mcp(modern_versions=["2027-01-01"])  # also speaks the legacy generations
    client = connected(server)
    assert client.version == "2025-11-25"
    assert server.methods() == ["server/discover", "initialize", "notifications/initialized"]


def test_no_common_version_is_reported(fake_mcp: Make) -> None:
    server = fake_mcp(modern_versions=["2027-01-01"], legacy=False)
    with pytest.raises(McpError, match="no common protocol version.*2027-01-01"):
        client_for(server).connect()


def test_a_modern_error_does_not_trigger_the_fallback(fake_mcp: Make) -> None:
    server = fake_mcp(modern_versions=["2027-01-01"], legacy=False)
    with pytest.raises(McpError):
        client_for(server).connect()
    assert "initialize" not in server.methods()


def test_forced_versions(fake_mcp: Make) -> None:
    server = fake_mcp()
    client = connected(server, protocol_version="2025-06-18")
    assert client.version == "2025-06-18" and server.methods() == ["initialize", "notifications/initialized"]
    legacy_only = fake_mcp(modern=False)
    with pytest.raises(McpError):
        client_for(legacy_only, protocol_version=MODERN).connect()
    with pytest.raises(McpError, match="unsupported protocol_version"):
        client_for(server, protocol_version="1999-01-01").connect()


def test_old_sse_transport_is_named(fake_mcp: Make) -> None:
    with pytest.raises(McpError, match="HTTP\\+SSE transport \\(2024-11-05\\)"):
        client_for(fake_mcp(old_sse_transport=True)).connect()


def test_credentials(fake_mcp: Make) -> None:
    server = fake_mcp(token="s3cret")
    with pytest.raises(McpError, match="refused the credentials \\(HTTP 401\\)"):
        client_for(server).connect()
    with pytest.raises(McpError, match="refused the credentials"):
        client_for(server, headers={"Authorization": "Bearer wrong"}).connect()
    assert connected(server, headers={"Authorization": "Bearer s3cret"}).version == MODERN


def test_unreachable_and_slow_servers(fake_mcp: Make) -> None:
    server = fake_mcp()
    server.stop()
    with pytest.raises(McpError, match="cannot connect"):
        client_for(server).connect()
    slow = fake_mcp(delay=1.5)
    with pytest.raises(McpError, match="timed out"):
        client_for(slow, connect_timeout=0.3, timeout=0.3).connect()


@pytest.mark.parametrize("modern", [True, False])
def test_sse_answers(fake_mcp: Make, modern: bool) -> None:
    server = fake_mcp(modern=modern, sse=True)
    client = connected(server)
    assert [t.name for t in client.list_tools().tools][:2] == ["echo", "add"]
    assert client.call_tool("echo", {"message": "hi"}) == "echo: hi"


def test_session_expiry_opens_a_new_session(fake_mcp: Make) -> None:
    server = fake_mcp(modern=False)
    client = connected(server)
    client.list_tools()
    server.expire_sessions()
    assert client.call_tool("echo", {"message": "again"}) == "echo: again"
    assert server.methods().count("initialize") == 2 and client._session == "session-2"


def test_close_ends_a_session_only_when_there_is_one(fake_mcp: Make) -> None:
    legacy = fake_mcp(modern=False)
    connected(legacy).close()
    assert [r.verb for r in legacy.requests].count("DELETE") == 1 and not legacy.sessions
    modern = fake_mcp()
    connected(modern).close()
    assert all(r.verb != "DELETE" for r in modern.requests)


# TLS


def test_tls_verification(fake_mcp: Make) -> None:
    server = fake_mcp(tls=True)
    with pytest.raises(McpError):
        client_for(server, verify=True).connect()  # self-signed
    assert connected(server, verify=False).version == MODERN
    assert connected(server, verify=str(server.cert)).version == MODERN  # custom CA


# Tools


def test_pagination_and_ttl(fake_mcp: Make) -> None:
    server = fake_mcp(page_size=2, ttl_ms=60_000)
    listing = connected(server).list_tools()
    assert [t.name for t in listing.tools] == ["echo", "add", "query", "picture", "broken", "ask"] and listing.ttl == 60
    assert server.methods().count("tools/list") == 3
    server.ttl_ms = 500
    assert connected(server).list_tools().ttl == 10  # minimum
    server.ttl_ms = None
    assert connected(server).list_tools().ttl == 300  # default
    assert connected(fake_mcp(modern=False, ttl_ms=1000)).list_tools().ttl == 300  # session revisions re-read every 300 s


def test_tool_view(fake_mcp: Make) -> None:
    server = fake_mcp()
    server.tools[0]["description"] = "x" * 3000
    tool = {t.name: t for t in connected(server).list_tools().tools}
    assert len(tool["echo"].description) == 1000
    assert tool["echo"].schema["required"] == ["message"]
    assert "x-mcp-header" not in str(tool["query"].schema) and tool["query"].schema["properties"]["region"]["type"] == "string"
    assert [(h.name, h.path) for h in tool["query"].headers] == [("Region", ("region",)), ("Count", ("count",)), ("Dry", ("dry",))]


def test_param_headers(fake_mcp: Make) -> None:
    server = fake_mcp()
    client = connected(server)
    client.list_tools()
    client.call_tool("query", {"region": "us-west1", "count": 3, "dry": False, "text": "free"})
    headers = server.requests[-1].headers
    assert headers["mcp-method"] == "tools/call" and headers["mcp-name"] == "query"
    assert (headers["mcp-param-region"], headers["mcp-param-count"], headers["mcp-param-dry"]) == ("us-west1", "3", "false")
    assert "mcp-param-text" not in headers
    client.call_tool("query", {"region": "Zürich 世界"})
    headers = server.requests[-1].headers
    assert headers["mcp-param-region"] == header_value("Zürich 世界") and "mcp-param-count" not in headers  # omitted when absent
    client.call_tool("query", {"region": None, "count": 1})
    assert "mcp-param-region" not in server.requests[-1].headers


def test_odd_tool_names_use_the_base64_form(fake_mcp: Make) -> None:
    server = fake_mcp()
    server.tools.append({"name": "tëst tool", "description": "", "inputSchema": {"type": "object"}})
    client = connected(server)
    client.list_tools()
    assert client.call_tool("tëst tool", {}) == "ran tëst tool"
    assert server.requests[-1].headers["mcp-name"] == header_value("tëst tool")


def test_invalid_annotations_drop_only_that_tool(fake_mcp: Make) -> None:
    server = fake_mcp()
    schemas = {
        "nested_array": {"type": "object", "properties": {"l": {"type": "array", "items": {"type": "string", "x-mcp-header": "Item"}}}},
        "number_type": {"type": "object", "properties": {"n": {"type": "number", "x-mcp-header": "N"}}},
        "bad_token": {"type": "object", "properties": {"s": {"type": "string", "x-mcp-header": "bad name"}}},
        "empty": {"type": "object", "properties": {"s": {"type": "string", "x-mcp-header": ""}}},
        "duplicate": {"type": "object", "properties": {"a": {"type": "string", "x-mcp-header": "Same"}, "b": {"type": "string", "x-mcp-header": "same"}}},
        "composed": {"type": "object", "properties": {"s": {"oneOf": [{"type": "string", "x-mcp-header": "S"}]}}},
        "root": {"type": "object", "x-mcp-header": "Root"},
    }
    server.tools = [*server.tools[:1], *({"name": n, "description": "", "inputSchema": s} for n, s in schemas.items()),
                    {"name": "nested_ok", "description": "", "inputSchema": {"type": "object", "properties": {"o": {"type": "object", "properties": {"r": {"type": "string", "x-mcp-header": "R"}}}}}}]
    listing = connected(server).list_tools()
    assert [t.name for t in listing.tools] == ["echo", "nested_ok"]
    assert {n for n, _ in listing.dropped} == set(schemas)
    assert [(h.name, h.path) for h in listing.tools[1].headers] == [("R", ("o", "r"))]


def test_legacy_servers_keep_schemas_untouched(fake_mcp: Make) -> None:
    listing = connected(fake_mcp(modern=False)).list_tools()
    query = next(t for t in listing.tools if t.name == "query")
    assert query.headers == () and query.schema["properties"]["region"]["x-mcp-header"] == "Region"


def test_header_mismatch_triggers_a_relist_and_one_retry(fake_mcp: Make) -> None:
    server = fake_mcp()
    client = connected(server)
    client.list_tools()
    server.tools[0]["inputSchema"]["properties"]["message"]["x-mcp-header"] = "Msg"  # the tool changed after the list
    assert client.call_tool("echo", {"message": "hi"}) == "echo: hi"
    assert server.methods()[-3:] == ["tools/call", "tools/list", "tools/call"]
    assert server.requests[-1].headers["mcp-param-msg"] == "hi"


def test_results(fake_mcp: Make) -> None:
    client = connected(fake_mcp())
    client.list_tools()
    assert client.call_tool("echo", {"message": "hi"}) == "echo: hi"
    assert client.call_tool("add", {"a": 2, "b": 3}) == '{"sum": 5}'
    assert "image/png" in client.call_tool("picture", {}) and client.call_tool("broken", {}) == "Error: it broke"
    assert client.call_tool("ask", {}).startswith("Error: the server asked for further input")
    with pytest.raises(McpError, match="Unknown tool"):
        client.call_tool("nope", {})
