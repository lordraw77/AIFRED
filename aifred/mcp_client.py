"""MCP client over Streamable HTTP, written on ``httpx`` (spec 9.2).

Two protocol generations are spoken:

* **2026-07-28** is stateless: no ``initialize``, no session; every request carries the protocol version and
  client capabilities in ``_meta`` and the ``MCP-Protocol-Version``, ``Mcp-Method`` and ``Mcp-Name`` headers.
* **2025-03-26 … 2025-11-25** open a session with an ``initialize`` handshake and keep ``Mcp-Session-Id``.

The generation is detected per server by trying the modern one first. The client is synchronous (tools run in
worker threads) and thread-safe: ``httpx.Client`` is, and the only mutable state, the session id, is guarded.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import ssl
import threading
from dataclasses import dataclass, field
from typing import Any

import httpx

from aifred import __version__

logger = logging.getLogger(__name__)

MODERN = "2026-07-28"
LEGACY = ("2025-11-25", "2025-06-18", "2025-03-26")
SUPPORTED = (MODERN, *LEGACY)
DEFAULT_TTL = 300.0
MIN_TTL = 10.0
MAX_PAGES = 20

ERR_HEADER_MISMATCH = -32020
ERR_MISSING_CAPABILITY = -32021
ERR_UNSUPPORTED_VERSION = -32022
ERR_METHOD_NOT_FOUND = -32601
_MODERN_ERRORS = {ERR_HEADER_MISMATCH, ERR_MISSING_CAPABILITY, ERR_UNSUPPORTED_VERSION}

_TOKEN_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_SENTINEL_RE = re.compile(r"^=\?base64\?.*\?=$", re.DOTALL)
_META = "io.modelcontextprotocol/"


class McpError(Exception):
    """A failure talking to a server; the message is readable by the admin and the model.

    Attributes:
        status: HTTP status, 0 when there was none.
        code: JSON-RPC error code, when the server sent one.
        data: JSON-RPC error data.
        modern: Whether the body was a recognised modern-revision JSON-RPC error.
    """

    def __init__(self, message: str, *, status: int = 0, code: int | None = None, data: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.data = data

    @property
    def modern(self) -> bool:
        """The server answered with an error only a 2026-07-28 server sends."""
        return self.code in _MODERN_ERRORS or (self.code == ERR_METHOD_NOT_FOUND and self.status == 404)


def header_value(value: str) -> str:
    """Encode a header value as the transport requires: plain ASCII, else the Base64 sentinel form."""
    plain = all(0x20 <= ord(ch) <= 0x7E for ch in value) and value == value.strip() and not _SENTINEL_RE.match(value)
    if plain:
        return value
    return "=?base64?" + base64.b64encode(value.encode()).decode() + "?="


@dataclass(frozen=True)
class ParamHeader:
    """A tool parameter mirrored into an ``Mcp-Param-*`` header.

    Attributes:
        name: The ``x-mcp-header`` name.
        path: Property path of the parameter in the arguments.
    """

    name: str
    path: tuple[str, ...]


@dataclass(frozen=True)
class McpTool:
    """A tool as discovered on a server.

    Attributes:
        name: Name on the server.
        description: Description, shortened for the model.
        schema: ``inputSchema`` without the ``x-mcp-header`` keys.
        headers: Parameters to mirror into headers (modern revision).
    """

    name: str
    description: str
    schema: dict[str, Any]
    headers: tuple[ParamHeader, ...] = ()


def _scan_headers(schema: Any) -> tuple[dict[str, Any], tuple[ParamHeader, ...]]:
    """Validate ``x-mcp-header`` annotations and return the schema without them.

    Raises:
        McpError: If an annotation breaks a rule (the tool is then dropped).
    """
    found: list[ParamHeader] = []
    seen: set[str] = set()

    def walk(node: Any, reachable: bool, path: tuple[str, ...]) -> Any:
        if isinstance(node, list):
            return [walk(item, False, path) for item in node]
        if not isinstance(node, dict):
            return node
        out: dict[str, Any] = {}
        for key, value in node.items():
            if key == "x-mcp-header":
                kind = node.get("type")
                if not reachable or not path:
                    raise McpError("x-mcp-header is only allowed on properties reachable through 'properties'")
                if not isinstance(value, str) or not _TOKEN_RE.match(value):
                    raise McpError(f"x-mcp-header {value!r} is not a valid header name")
                if kind not in ("integer", "string", "boolean"):
                    raise McpError(f"x-mcp-header on a parameter of type {kind!r}")
                if value.lower() in seen:
                    raise McpError(f"x-mcp-header {value!r} is used twice")
                seen.add(value.lower())
                found.append(ParamHeader(value, path))
            elif key == "properties" and reachable and isinstance(value, dict):
                out[key] = {name: walk(prop, True, (*path, name)) for name, prop in value.items()}
            else:
                out[key] = walk(value, False, path)
        return out

    return walk(schema, True, ()), tuple(found)


def _lookup(arguments: Any, path: tuple[str, ...]) -> Any:
    for part in path:
        if not isinstance(arguments, dict) or part not in arguments:
            return None
        arguments = arguments[part]
    return arguments


def format_result(result: dict[str, Any]) -> str:
    """Turn a ``tools/call`` result into text for the model (spec 9.2, "Tool result")."""
    parts: list[str] = []
    for item in result.get("content") or []:
        kind = item.get("type") if isinstance(item, dict) else None
        if kind == "text":
            parts.append(str(item.get("text", "")))
        elif kind in ("image", "audio"):
            size = len(str(item.get("data", ""))) * 3 // 4
            parts.append(f"[{kind}: {item.get('mimeType', 'unknown type')}, about {size:,} bytes, not shown]")
        elif kind == "resource":
            res = item.get("resource") or {}
            size = len(str(res.get("text", res.get("blob", ""))))
            parts.append(f"[resource: {res.get('uri', '?')}, {res.get('mimeType', 'unknown type')}, {size:,} characters, not shown]")
        elif kind == "resource_link":
            parts.append(f"[resource link: {item.get('uri', '?')}]")
        else:
            parts.append(f"[{kind or 'unknown'} content, not shown]")
    text = "\n".join(p for p in parts if p)
    if not text and result.get("structuredContent") is not None:
        text = json.dumps(result["structuredContent"], ensure_ascii=False)
    text = text or "(no output)"
    return f"Error: {text}" if result.get("isError") else text


@dataclass
class Listing:
    """Result of ``tools/list``.

    Attributes:
        tools: Valid tools in server order.
        ttl: Seconds the list stays fresh.
        dropped: ``(name, reason)`` for tools rejected by validation.
    """

    tools: list[McpTool]
    ttl: float
    dropped: list[tuple[str, str]] = field(default_factory=list)


class McpClient:
    """One server connection.

    Args:
        url: The MCP endpoint.
        headers: Extra request headers (for example ``Authorization``).
        verify: TLS verification: ``True``, ``False`` or the path of a PEM CA bundle.
        timeout: Seconds for a tool call.
        connect_timeout: Seconds for connecting and negotiating.
        protocol_version: ``auto`` or a revision to force.
        transport: httpx transport (tests).
    """

    def __init__(
        self, url: str, headers: dict[str, str] | None = None, *, verify: bool | str = True, timeout: float = 120.0,
        connect_timeout: float = 15.0, protocol_version: str = "auto", transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.url = url
        self._headers = dict(headers or {})
        self._timeout = timeout
        self._connect_timeout = connect_timeout
        self._forced = protocol_version
        context: bool | ssl.SSLContext = ssl.create_default_context(cafile=verify) if isinstance(verify, str) else verify
        self._http = httpx.Client(
            verify=context, follow_redirects=False, transport=transport,
            timeout=httpx.Timeout(timeout, connect=connect_timeout, pool=connect_timeout),
        )
        self._lock = threading.Lock()
        self._ids = 0
        self._session: str | None = None
        self.version = ""
        self.server_name = ""
        self.instructions = ""
        self._tools: dict[str, McpTool] = {}

    # Wire

    @property
    def modern(self) -> bool:
        """Whether the stateless revision is in use."""
        return self.version == MODERN

    def _next_id(self) -> int:
        with self._lock:
            self._ids += 1
            return self._ids

    def _meta(self) -> dict[str, Any]:
        return {
            f"{_META}protocolVersion": self.version,
            f"{_META}clientInfo": {"name": "AIFRED", "version": __version__},
            f"{_META}clientCapabilities": {},
        }

    def _request_headers(self, method: str, name: str | None, extra: dict[str, str] | None) -> dict[str, str]:
        headers = {**self._headers, "Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self.version:
            headers["MCP-Protocol-Version"] = self.version
        if self.modern:
            headers["Mcp-Method"] = method
            if name is not None:
                headers["Mcp-Name"] = header_value(name)
        elif self._session:
            headers["Mcp-Session-Id"] = self._session
        headers.update(extra or {})
        return headers

    @staticmethod
    def _error(status: int, body: Any, fallback: str) -> McpError:
        error = body.get("error") if isinstance(body, dict) else None
        if isinstance(error, dict):
            return McpError(str(error.get("message") or fallback), status=status, code=error.get("code"), data=error.get("data"))
        return McpError(fallback, status=status)

    def _read(self, response: httpx.Response, request_id: int | None) -> dict[str, Any]:
        """The JSON-RPC response of a request: from a JSON body or the end of an SSE stream."""
        kind = response.headers.get("content-type", "").split(";")[0].strip().lower()
        if kind == "text/event-stream":
            data: list[str] = []
            for line in response.iter_lines():
                if line.startswith(":"):
                    continue
                if line.startswith("data:"):
                    data.append(line[5:].removeprefix(" "))
                elif line == "" and data:
                    message = self._decode("\n".join(data))
                    data = []
                    if isinstance(message, dict) and message.get("id") == request_id and ("result" in message or "error" in message):
                        return message
                    if isinstance(message, dict) and message.get("method") == "notifications/progress":
                        logger.debug("MCP progress: %s", message.get("params"))
            if data:
                message = self._decode("\n".join(data))
                if isinstance(message, dict) and message.get("id") == request_id:
                    return message
            raise McpError("the server closed the stream without answering")
        response.read()
        message = self._decode(response.text)
        if not isinstance(message, dict):
            raise McpError("the server's answer is not a JSON-RPC message")
        return message

    @staticmethod
    def _decode(text: str) -> Any:
        try:
            return json.loads(text)
        except ValueError:
            raise McpError("the server's answer is not valid JSON") from None

    def _post(
        self, method: str, params: dict[str, Any] | None = None, *, name: str | None = None, extra: dict[str, str] | None = None,
        timeout: float | None = None, notification: bool = False,
    ) -> dict[str, Any]:
        """POST one message and return the ``result`` (``{}`` for a notification)."""
        params = dict(params or {})
        if self.modern:
            params["_meta"] = {**params.get("_meta", {}), **self._meta()}
        body: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params:
            body["params"] = params
        request_id = None
        if not notification:
            request_id = body["id"] = self._next_id()
        limits = httpx.Timeout(timeout or self._timeout, connect=self._connect_timeout, pool=self._connect_timeout)
        try:
            with self._http.stream(
                "POST", self.url, json=body, headers=self._request_headers(method, name, extra), timeout=limits
            ) as response:
                if response.status_code in (401, 403):
                    raise McpError(f"the server refused the credentials (HTTP {response.status_code}); check the headers", status=response.status_code)
                if notification:
                    if response.status_code >= 400:
                        raise McpError(f"HTTP {response.status_code}", status=response.status_code)
                    return {}
                self._remember_session(response)
                if response.status_code >= 400:
                    try:
                        parsed = self._read(response, request_id)
                    except McpError:
                        parsed = {}
                    raise self._error(response.status_code, parsed, f"HTTP {response.status_code}")
                message = self._read(response, request_id)
        except httpx.TimeoutException:
            raise McpError(f"timed out after {timeout or self._timeout:g} s") from None
        except httpx.ConnectError as exc:
            raise McpError(f"cannot connect to {self.url}: {_short(exc)}") from None
        except httpx.HTTPError as exc:
            raise McpError(f"{type(exc).__name__}: {_short(exc)}") from None
        if "error" in message:
            raise self._error(200, message, "the server returned an error")
        result = message.get("result")
        return result if isinstance(result, dict) else {}

    def _remember_session(self, response: httpx.Response) -> None:
        sid = response.headers.get("mcp-session-id")
        if sid and not self.modern:
            self._session = sid

    # Negotiation

    def connect(self) -> None:
        """Detect the protocol generation and open the connection.

        Raises:
            McpError: If no common version exists or the server is unreachable.
        """
        forced = self._forced
        if forced != "auto" and forced not in SUPPORTED:
            raise McpError(f"unsupported protocol_version {forced!r}")
        if forced in LEGACY:
            self._handshake(forced)
            return
        self.version = MODERN
        try:
            result = self._post("server/discover", timeout=self._connect_timeout)
        except McpError as exc:
            if exc.status in (401, 403) or (exc.status == 0 and exc.code is None):
                raise
            if exc.code == ERR_UNSUPPORTED_VERSION:
                self._after_unsupported(exc)
                return
            if exc.modern or forced == MODERN:
                raise
            if exc.status in (400, 404, 405) or exc.code is not None:
                self.version = ""
                logger.info("MCP server %s does not speak %s; using the handshake generation", self.url, MODERN)
                self._handshake(LEGACY[0])
                return
            raise
        self._accept_discover(result)

    def _after_unsupported(self, exc: McpError) -> None:
        supported = (exc.data or {}).get("supported") if isinstance(exc.data, dict) else None
        common = [v for v in SUPPORTED if isinstance(supported, list) and v in supported]
        if not common:
            theirs = ", ".join(map(str, supported)) if isinstance(supported, list) else "unknown"
            raise McpError(f"no common protocol version (server supports: {theirs}; AIFRED: {', '.join(SUPPORTED)})") from None
        if common[0] == MODERN:
            raise McpError("the server rejected 2026-07-28 although it lists it as supported") from None
        self.version = ""
        self._handshake(common[0])

    def _accept_discover(self, result: dict[str, Any]) -> None:
        versions = result.get("supportedVersions") or result.get("supported") or [MODERN]
        if isinstance(versions, list) and MODERN not in versions:
            common = [v for v in SUPPORTED if v in versions]
            if not common:
                raise McpError(f"no common protocol version (server supports: {', '.join(map(str, versions))})")
            self.version = ""
            self._handshake(common[0])
            return
        self.version = MODERN
        info = result.get("serverInfo") or {}
        self.server_name = str(info.get("name", "")) if isinstance(info, dict) else ""
        self.instructions = str(result.get("instructions") or "")

    def _handshake(self, version: str) -> None:
        self._session = None
        self.version = ""  # the first message of a session carries no version header
        try:
            result = self._post(
                "initialize",
                {"protocolVersion": version, "capabilities": {}, "clientInfo": {"name": "AIFRED", "version": __version__}},
                timeout=self._connect_timeout,
            )
        except McpError as exc:
            if exc.status in (400, 404, 405) and not exc.modern:
                self._reject_old_transport()
            raise
        chosen = result.get("protocolVersion")
        if chosen not in LEGACY:
            raise McpError(f"the server chose protocol version {chosen!r}, which AIFRED does not support")
        self.version = chosen
        info = result.get("serverInfo") or {}
        self.server_name = str(info.get("name", "")) if isinstance(info, dict) else ""
        self.instructions = str(result.get("instructions") or "")
        self._post("notifications/initialized", notification=True, timeout=self._connect_timeout)

    def _reject_old_transport(self) -> None:
        """Say so clearly when the URL is a 2024-11-05 HTTP+SSE endpoint."""
        try:
            with self._http.stream("GET", self.url, headers={**self._headers, "Accept": "text/event-stream"}, timeout=self._connect_timeout) as r:
                if r.status_code == 200 and r.headers.get("content-type", "").startswith("text/event-stream"):
                    raise McpError("this server uses the deprecated HTTP+SSE transport (2024-11-05), which AIFRED does not support")
        except httpx.HTTPError:
            return

    def close(self) -> None:
        """End the session (when there is one) and release the connection."""
        if self._session:
            try:
                self._http.delete(self.url, headers=self._request_headers("", None, None), timeout=self._connect_timeout)
            except httpx.HTTPError:
                pass
        self._http.close()

    # Requests with recovery

    def _call(self, method: str, params: dict[str, Any] | None = None, **kw: Any) -> dict[str, Any]:
        try:
            return self._post(method, params, **kw)
        except McpError as exc:
            if exc.status == 404 and self._session and not self.modern:
                logger.info("MCP session of %s expired; opening a new one", self.url)
                self._handshake(self.version or LEGACY[0])
                return self._post(method, params, **kw)
            raise

    # Tools

    def list_tools(self) -> Listing:
        """Read the tool list (all pages), validating it."""
        raw: list[dict[str, Any]] = []
        cursor: str | None = None
        ttl_ms: Any = None
        for _ in range(MAX_PAGES):
            result = self._call("tools/list", {"cursor": cursor} if cursor else {})
            raw.extend(t for t in result.get("tools") or [] if isinstance(t, dict))
            ttl_ms = result.get("ttlMs", ttl_ms)
            cursor = result.get("nextCursor")
            if not cursor:
                break
        tools: list[McpTool] = []
        dropped: list[tuple[str, str]] = []
        for item in raw:
            name = item.get("name")
            if not isinstance(name, str) or not name:
                dropped.append((str(name), "no name"))
                continue
            schema = item.get("inputSchema")
            try:
                clean, headers = _scan_headers(schema if isinstance(schema, dict) else {"type": "object"})
            except McpError as exc:
                logger.warning("MCP tool %r dropped: %s", name, exc)
                dropped.append((name, str(exc)))
                continue
            if not self.modern:  # earlier revisions have no such annotations; leave the schema as sent
                clean, headers = (schema if isinstance(schema, dict) else {"type": "object"}), ()
            clean.setdefault("type", "object")
            tools.append(McpTool(name, str(item.get("description") or "")[:1000], clean, headers))
        ttl = DEFAULT_TTL
        if self.modern and isinstance(ttl_ms, (int, float)) and not isinstance(ttl_ms, bool) and ttl_ms >= 0:
            ttl = max(MIN_TTL, ttl_ms / 1000)
        self._tools = {t.name: t for t in tools}
        return Listing(tools, ttl, dropped)

    def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """Call a tool and return its output as text.

        Raises:
            McpError: On transport failure or a JSON-RPC error.
        """
        tool = self._tools.get(name)
        for attempt in (0, 1):
            extra = {}
            if self.modern and tool:
                for header in tool.headers:
                    value = _lookup(arguments, header.path)
                    if isinstance(value, bool):
                        text = "true" if value else "false"
                    elif isinstance(value, (int, str)):
                        text = str(value)
                    else:
                        continue
                    extra[f"Mcp-Param-{header.name}"] = header_value(text)
            try:
                result = self._call("tools/call", {"name": name, "arguments": arguments}, name=name, extra=extra)
            except McpError as exc:
                if exc.code == ERR_HEADER_MISMATCH and attempt == 0:
                    self.list_tools()  # the tool's schema may have changed
                    tool = self._tools.get(name)
                    continue
                raise
            if result.get("resultType") == "input_required":
                return "Error: the server asked for further input (sampling, elicitation or roots), which AIFRED does not provide"
            return format_result(result)
        raise McpError("the server kept rejecting the request headers")  # pragma: no cover - loop always returns or raises


def _short(exc: Exception) -> str:
    text = str(exc) or type(exc).__name__
    return text if len(text) <= 200 else text[:199] + "…"
