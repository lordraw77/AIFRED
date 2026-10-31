"""A scriptable fake MCP server on a real socket, speaking both protocol generations.

* Modern (2026-07-28): stateless, ``server/discover``, ``_meta`` on every request, ``Mcp-Method``/``Mcp-Name`` and
  ``Mcp-Param-*`` headers validated against the body.
* Legacy (2025-03-26 … 2025-11-25): ``initialize`` handshake and ``Mcp-Session-Id``.

Responses are JSON or SSE (with a progress notification first). Every request is recorded in ``requests``.
"""

from __future__ import annotations

import base64
import datetime as dt
import ipaddress
import asyncio
import json
import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

MODERN = "2026-07-28"
LEGACY = ["2025-11-25", "2025-06-18", "2025-03-26"]
META = "io.modelcontextprotocol/"

TOOLS: list[dict[str, Any]] = [
    {"name": "echo", "description": "Echo a message", "inputSchema": {"type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"]}},
    {"name": "add", "description": "Add two numbers", "inputSchema": {"type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}}}},
    {"name": "query", "description": "Query a region", "inputSchema": {"type": "object", "properties": {
        "region": {"type": "string", "x-mcp-header": "Region"}, "count": {"type": "integer", "x-mcp-header": "Count"},
        "dry": {"type": "boolean", "x-mcp-header": "Dry"}, "text": {"type": "string"}}}},
    {"name": "picture", "description": "Returns an image", "inputSchema": {"type": "object"}},
    {"name": "broken", "description": "Always fails", "inputSchema": {"type": "object"}},
    {"name": "ask", "description": "Needs more input", "inputSchema": {"type": "object"}},
]
MRTR_TOOLS: list[dict[str, Any]] = [
    {"name": "login", "description": "Asks for a password", "inputSchema": {"type": "object"}},
    {"name": "think", "description": "Needs the client's model", "inputSchema": {"type": "object"}},
    {"name": "loop", "description": "Never satisfied", "inputSchema": {"type": "object"}},
]
FORM = {"type": "object", "properties": {"name": {"type": "string"}, "age": {"type": "integer"}, "color": {"type": "string", "enum": ["red", "blue"]}}, "required": ["name"]}


def run_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    if name == "echo":
        return {"content": [{"type": "text", "text": f"echo: {args.get('message')}"}]}
    if name == "add":
        return {"content": [], "structuredContent": {"sum": args.get("a", 0) + args.get("b", 0)}}
    if name == "query":
        return {"content": [{"type": "text", "text": f"query {args}"}]}
    if name == "ask":
        return {"resultType": "input_required", "inputRequests": {"q": {"method": "elicitation/create"}}}
    if name == "picture":
        return {"content": [{"type": "image", "mimeType": "image/png", "data": base64.b64encode(b"x" * 300).decode()},
                            {"type": "resource", "resource": {"uri": "file:///a.txt", "mimeType": "text/plain", "text": "hello"}}]}
    if name == "broken":
        return {"content": [{"type": "text", "text": "it broke"}], "isError": True}
    return {"content": [{"type": "text", "text": f"ran {name}"}]}


@dataclass
class Seen:
    """One recorded request."""

    method: str
    headers: dict[str, str]
    body: dict[str, Any]
    verb: str = "POST"


def make_certificate(directory: Path) -> tuple[Path, Path]:
    """A self-signed certificate for 127.0.0.1 / localhost; returns ``(cert, key)``. The cert is also its own CA."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "fake-mcp")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=1)).not_valid_after(now + dt.timedelta(days=30))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1")), x509.DNSName("localhost")]), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = directory / "cert.pem", directory / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))
    return cert_path, key_path


@dataclass
class FakeMcp:
    """Configure a field, then ``start()``.

    Attributes:
        modern: Serve the stateless revision.
        legacy: Serve the handshake revisions.
        modern_versions: Versions accepted in modern requests.
        legacy_versions: Versions the handshake accepts.
        reject_style: How a non-modern server answers a modern request: ``empty400``, ``404``, ``405`` or ``rpc200``.
        sse: Answer with an SSE stream instead of JSON.
        token: Required bearer token (401 otherwise).
        token_ok: Called with the ``Authorization`` header value; falsy means 401 (OAuth tests, where tokens change).
        oauth_issuer: Advertise this authorization server in the protected-resource metadata and in the 401 answer.
        tools: Tool definitions served.
        page_size: Tools per ``tools/list`` page (0: one page).
        ttl_ms: ``ttlMs`` returned by modern ``tools/list``.
        old_sse_transport: Behave like a 2024-11-05 server (GET opens an SSE stream with an ``endpoint`` event).
        tls: Directory to place a self-signed certificate in; the server then speaks HTTPS.
        requests: Everything received.
    """

    modern: bool = True
    legacy: bool = True
    modern_versions: list[str] = field(default_factory=lambda: [MODERN])
    legacy_versions: list[str] = field(default_factory=lambda: list(LEGACY))
    reject_style: str = "empty400"
    sse: bool = False
    token: str | None = None
    token_ok: Any = None
    oauth_issuer: str | None = None
    tools: list[dict[str, Any]] = field(default_factory=lambda: [dict(t) for t in TOOLS])
    page_size: int = 0
    ttl_ms: int | None = None
    old_sse_transport: bool = False
    tls: Path | None = None
    delay: float = 0.0
    mrtr: bool = False
    list_changed: bool = True
    events: queue.Queue = field(default_factory=queue.Queue)
    requests: list[Seen] = field(default_factory=list)
    sessions: set[str] = field(default_factory=set)
    cert: Path | None = None
    port: int = 0
    _server: uvicorn.Server | None = None
    _thread: threading.Thread | None = None
    _counter: int = 0

    @property
    def url(self) -> str:
        return f"{'https' if self.tls else 'http'}://127.0.0.1:{self.port}/mcp"

    def methods(self) -> list[str]:
        return [r.method for r in self.requests if r.verb == "POST"]

    def expire_sessions(self) -> None:
        self.sessions.clear()

    # Application

    def _rpc_error(self, status: int, rid: Any, code: int, message: str, data: Any = None) -> JSONResponse:
        error: dict[str, Any] = {"code": code, "message": message}
        if data is not None:
            error["data"] = data
        return JSONResponse({"jsonrpc": "2.0", "id": rid, "error": error}, status_code=status)

    def _reply(self, rid: Any, result: dict[str, Any], headers: dict[str, str] | None = None) -> Response:
        message = {"jsonrpc": "2.0", "id": rid, "result": result}
        if not self.sse:
            return JSONResponse(message, headers=headers)

        def stream() -> Any:
            yield ": keep-alive\n\n"
            yield f"data: {json.dumps({'jsonrpc': '2.0', 'method': 'notifications/progress', 'params': {'progress': 1}})}\n\n"
            body = json.dumps(message)
            cut = body.find(", ", len(body) // 2) + 1  # data lines are joined with a newline, which JSON allows between tokens
            yield f"data: {body[:cut]}\ndata: {body[cut:]}\n\n"  # one event over two data lines

        return StreamingResponse(stream(), media_type="text/event-stream", headers=headers)

    @staticmethod
    def _decode_header(value: str) -> str:
        if value.startswith("=?base64?") and value.endswith("?="):
            return base64.b64decode(value[9:-2]).decode()
        return value

    def enable_mrtr(self) -> None:
        """Serve the multi round-trip tools (``login``, ``think``, ``loop``) and let ``ask`` use a form."""
        self.mrtr = True
        self.tools += [dict(t) for t in MRTR_TOOLS]

    def _tools_page(self, cursor: str | None) -> tuple[list[dict[str, Any]], str | None]:
        if not self.page_size:
            return self.tools, None
        start = int(cursor or 0)
        end = start + self.page_size
        return self.tools[start:end], (str(end) if end < len(self.tools) else None)

    def _build(self) -> FastAPI:
        app = FastAPI()

        @app.get("/.well-known/oauth-protected-resource")
        async def protected_resource() -> Response:
            if not self.oauth_issuer:
                return Response(status_code=404)
            return JSONResponse({"resource": self.url, "authorization_servers": [self.oauth_issuer], "scopes_supported": ["mcp"]})

        @app.get("/mcp")
        async def get(request: Request) -> Response:
            self.requests.append(Seen("GET", dict(request.headers), {}, "GET"))
            if self.old_sse_transport:
                return StreamingResponse(iter(["event: endpoint\ndata: /messages\n\n"]), media_type="text/event-stream")
            return Response(status_code=405)

        @app.delete("/mcp")
        async def delete(request: Request) -> Response:
            self.requests.append(Seen("DELETE", dict(request.headers), {}, "DELETE"))
            self.sessions.discard(request.headers.get("mcp-session-id", ""))
            return Response(status_code=200)

        @app.post("/mcp")
        async def post(request: Request) -> Response:
            if self.delay:
                time.sleep(self.delay)
            raw = await request.body()
            try:
                body = json.loads(raw)
            except ValueError:
                return Response(status_code=400)
            headers = {k.lower(): v for k, v in request.headers.items()}
            method, rid, params = body.get("method", ""), body.get("id"), body.get("params") or {}
            self.requests.append(Seen(method, headers, body))
            if (self.token and headers.get("authorization") != f"Bearer {self.token}") or (self.token_ok and not self.token_ok(headers.get("authorization", ""))):
                challenge = {"WWW-Authenticate": f'Bearer resource_metadata="http://127.0.0.1:{self.port}/.well-known/oauth-protected-resource"'} if self.oauth_issuer else {}
                return Response(status_code=401, content=b"unauthorized", headers=challenge)
            if self.old_sse_transport:
                return Response(status_code=405)
            meta = params.get("_meta") or {}
            is_modern = f"{META}protocolVersion" in meta
            if is_modern:
                return self._modern(method, rid, params, meta, headers)
            return self._legacy(method, rid, params, headers)

        return app

    def _not_modern(self) -> Response:
        if self.reject_style == "404":
            return Response(status_code=404)
        if self.reject_style == "405":
            return Response(status_code=405)
        if self.reject_style == "rpc200":
            return self._rpc_error(200, 1, -32601, "Method not found")
        return Response(status_code=400, content=b"bad request")

    def _modern(self, method: str, rid: Any, params: dict[str, Any], meta: dict[str, Any], headers: dict[str, str]) -> Response:
        if not self.modern:
            return self._not_modern()
        version = meta.get(f"{META}protocolVersion")
        if headers.get("mcp-protocol-version") != version:
            return self._rpc_error(400, rid, -32020, "Header mismatch: MCP-Protocol-Version")
        if version not in self.modern_versions:
            return self._rpc_error(400, rid, -32022, "Unsupported protocol version", {"supported": [*self.modern_versions, *(self.legacy_versions if self.legacy else [])], "requested": version})
        if headers.get("mcp-method") != method:
            return self._rpc_error(400, rid, -32020, "Header mismatch: Mcp-Method")
        if method == "server/discover":
            return self._reply(rid, {"resultType": "complete", "supportedVersions": self.modern_versions, "capabilities": {"tools": {}},
                                     "serverInfo": {"name": "fake-mcp", "version": "1"}, "instructions": "Fake server"})
        if method == "tools/list":
            tools, nxt = self._tools_page(params.get("cursor"))
            result: dict[str, Any] = {"resultType": "complete", "tools": tools}
            if nxt:
                result["nextCursor"] = nxt
            if self.ttl_ms is not None:
                result["ttlMs"] = self.ttl_ms
            return self._reply(rid, result)
        if method == "tools/call":
            name = params.get("name")
            sent = self._decode_header(headers.get("mcp-name", ""))
            if sent != name:
                return self._rpc_error(400, rid, -32020, "Header mismatch: Mcp-Name")
            tool = next((t for t in self.tools if t["name"] == name), None)
            if tool is None:
                return self._rpc_error(200, rid, -32602, f"Unknown tool: {name}")
            for prop, spec in (tool["inputSchema"].get("properties") or {}).items():
                header = spec.get("x-mcp-header")
                if header is None:
                    continue
                value = (params.get("arguments") or {}).get(prop)
                got = headers.get(f"mcp-param-{header}".lower())
                if value is None:
                    if got is not None:
                        return self._rpc_error(400, rid, -32020, f"Header mismatch: unexpected {header}")
                    continue
                expected = "true" if value is True else "false" if value is False else str(value)
                if got is None or self._decode_header(got) != expected:
                    return self._rpc_error(400, rid, -32020, f"Header mismatch: Mcp-Param-{header}")
            if self.mrtr and name in ("ask", "login", "think", "loop"):
                return self._mrtr(rid, name, params)
            return self._reply(rid, {"resultType": "complete", **run_tool(name, params.get("arguments") or {})})
        if method == "subscriptions/listen":
            return self._listen(rid)
        return self._rpc_error(404, rid, -32601, "Method not found")

    def _text(self, rid: Any, text: str) -> Response:
        return self._reply(rid, {"resultType": "complete", "content": [{"type": "text", "text": text}]})

    def _mrtr(self, rid: Any, name: str, params: dict[str, Any]) -> Response:
        """Multi round-trip tools: ``ask`` (a form), ``login`` (a form asking for a password), ``think`` (sampling), ``loop``."""
        responses, state = params.get("inputResponses"), params.get("requestState")

        def need(requests: dict[str, Any], token: str = "S1") -> Response:
            return self._reply(rid, {"resultType": "input_required", "inputRequests": requests, "requestState": token})

        if name == "loop":
            return self._reply(rid, {"resultType": "input_required", "requestState": f"again-{len(self.requests)}"})
        if name == "ask":
            if responses is None:
                return need({"who": {"method": "elicitation/create", "params": {"mode": "form", "message": "Who are you?", "requestedSchema": FORM}}})
            if state != "S1":
                return self._rpc_error(200, rid, -32602, "bad requestState")
            who = responses.get("who") or {}
            return self._text(rid, f"hello {json.dumps(who.get('content'), sort_keys=True)}" if who.get("action") == "accept" else f"declined: {who.get('action')}")
        if name == "login":
            if responses is None:
                schema = {"type": "object", "properties": {"password": {"type": "string"}}, "required": ["password"]}
                return need({"pw": {"method": "elicitation/create", "params": {"message": "Password?", "requestedSchema": schema}}})
            return self._text(rid, f"login: {json.dumps(responses, sort_keys=True)}")
        if responses is None:
            return need({"s": {"method": "sampling/createMessage", "params": {"messages": [{"role": "user", "content": {"type": "text", "text": "Capital of France?"}}],
                                                                              "systemPrompt": "Be brief.", "maxTokens": 50}}})
        answer = (responses.get("s") or {}).get("content") or {}
        return self._text(rid, f"model said: {answer.get('text')}")

    def _listen(self, rid: Any) -> Response:
        """A ``subscriptions/listen`` stream: the acknowledgment, then what ``events`` holds (``tools``, ``end`` or ``drop``)."""
        sub = {f"{META}subscriptionId": rid}

        def frame(message: dict[str, Any]) -> bytes:
            return b"data: " + json.dumps({"jsonrpc": "2.0", **message}).encode() + b"\n\n"

        async def stream() -> Any:
            yield frame({"method": "notifications/subscriptions/acknowledged", "params": {"_meta": sub, "notifications": {"toolsListChanged": True} if self.list_changed else {}}})
            while True:
                try:
                    event = self.events.get_nowait()
                except queue.Empty:
                    await asyncio.sleep(0.02)
                    continue
                if event == "tools":
                    yield frame({"method": "notifications/tools/list_changed", "params": {"_meta": sub}})
                elif event == "end":
                    yield frame({"id": rid, "result": {"resultType": "complete", "_meta": sub}})
                    return
                elif event == "drop":
                    return

        return StreamingResponse(stream(), media_type="text/event-stream")

    def _legacy(self, method: str, rid: Any, params: dict[str, Any], headers: dict[str, str]) -> Response:
        if not self.legacy:
            return self._rpc_error(400, rid, -32600, "initialize first")
        if method == "initialize":
            asked = params.get("protocolVersion")
            chosen = asked if asked in self.legacy_versions else self.legacy_versions[0]
            self._counter += 1
            sid = f"session-{self._counter}"
            self.sessions.add(sid)
            return self._reply(rid, {"protocolVersion": chosen, "capabilities": {"tools": {}}, "serverInfo": {"name": "fake-mcp-legacy", "version": "1"}},
                               {"Mcp-Session-Id": sid})
        sid = headers.get("mcp-session-id")
        if sid not in self.sessions:
            return Response(status_code=404, content=b"unknown session")
        if method == "notifications/initialized":
            return Response(status_code=202)
        if "mcp-protocol-version" not in headers:
            return self._rpc_error(400, rid, -32600, "missing MCP-Protocol-Version")
        if method == "tools/list":
            tools, nxt = self._tools_page(params.get("cursor"))
            result: dict[str, Any] = {"tools": tools}
            if nxt:
                result["nextCursor"] = nxt
            return self._reply(rid, result)
        if method == "tools/call":
            name = params.get("name")
            if not any(t["name"] == name for t in self.tools):
                return self._rpc_error(200, rid, -32602, f"Unknown tool: {name}")
            return self._reply(rid, run_tool(name, params.get("arguments") or {}))
        return self._rpc_error(200, rid, -32601, "Method not found")

    # Lifecycle

    def start(self) -> FakeMcp:
        options: dict[str, Any] = {}
        if self.tls:
            self.cert, key = make_certificate(self.tls)
            options = {"ssl_certfile": str(self.cert), "ssl_keyfile": str(key)}
        config = uvicorn.Config(self._build(), host="127.0.0.1", port=0, log_level="error", lifespan="off", **options)
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        deadline = time.time() + 10
        while not self._server.started:
            if time.time() > deadline:
                raise RuntimeError("fake MCP server did not start")
            time.sleep(0.01)
        self.port = self._server.servers[0].sockets[0].getsockname()[1]
        return self

    def stop(self) -> None:
        if self._server:
            self._server.should_exit = True
        if self._thread:
            self._thread.join(timeout=10)

