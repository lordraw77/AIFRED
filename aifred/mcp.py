"""MCP servers: stored definitions, background connections and the tools they provide (spec 9.2).

:class:`McpService` validates and stores server definitions (header values encrypted in the vault).
:class:`McpManager` keeps a connection per enabled server, retrying in the background so that an unreachable
server never blocks the agent, and exposes the discovered tools to the registry as ``mcp__<server>__<tool>``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shlex
import tempfile
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import httpx

from aifred.mcp_client import McpClient, McpError, McpTool, SUPPORTED
from aifred.mcp_stdio import StdioClient
from aifred.providers import ProviderError, _verify_to_db
from aifred.tools.registry import RawArgs, Tool, ToolContext, ToolError

if TYPE_CHECKING:
    from aifred.config import RuntimeConfig
    from aifred.store import Store
    from aifred.vault import Vault

logger = logging.getLogger(__name__)

NAME_RE = re.compile(r"^[a-z0-9_]{1,24}$")
TOOL_NAME_MAX = 64
RETRY_START, RETRY_MAX = 5.0, 60.0
PROTOCOL_CHOICES = ("auto", *SUPPORTED)
_RESERVED_HEADERS = frozenset({"content-type", "accept", "host", "content-length", "mcp-protocol-version", "mcp-session-id", "mcp-method", "mcp-name"})
IMPORT_KEY = "mcp:imported"
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_FORBIDDEN_ENV = re.compile(r"^(PATH|HOME|LD_.*|DYLD_.*|PYTHONPATH|PYTHONHOME|PYTHONSTARTUP|NODE_OPTIONS|BASH_ENV|ENV|IFS)$", re.IGNORECASE)
MAX_COMMAND_ITEMS, MAX_COMMAND_ITEM = 24, 500


class McpConfigError(ValueError):
    """An invalid server definition; the message is shown to the admin."""


@dataclass(frozen=True)
class McpServer:
    """A stored definition (header values are fetched from the vault when connecting)."""

    name: str
    url: str
    enabled: bool = True
    header_names: tuple[str, ...] = ()
    verify_tls: bool | str = True
    timeout: float | None = None
    connect_timeout: float | None = None
    protocol_version: str = "auto"
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    max_tools: int | None = None
    updated: float = 0.0
    command: tuple[str, ...] = ()

    @property
    def stdio(self) -> bool:
        """Whether the server is a child process instead of an HTTP endpoint."""
        return bool(self.command)

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> McpServer:
        raw = row["verify_tls"]
        return cls(
            row["name"], row["url"], row["enabled"], tuple(row["header_names"]), {"true": True, "false": False}.get(raw, raw),
            row["timeout"], row["connect_timeout"], row["protocol_version"], tuple(row["include"]), tuple(row["exclude"]), row["max_tools"], row["updated"],
            tuple(row.get("command") or ()),
        )


def secret_name(server: str) -> str:
    """Vault name of a server's header values."""
    return f"mcp_headers:{server}"


def validate_headers(headers: Any) -> dict[str, str | None]:
    """Header names and values; a value of ``None`` means "keep the stored value"."""
    if not isinstance(headers, dict):
        raise McpConfigError("headers must be an object")
    out: dict[str, str | None] = {}
    for name, value in headers.items():
        if not re.fullmatch(r"[A-Za-z0-9-]{1,64}", str(name)):
            raise McpConfigError(f"invalid header name {name!r}")
        if str(name).lower() in _RESERVED_HEADERS or str(name).lower().startswith("mcp-"):
            raise McpConfigError(f"header {name!r} is managed by AIFRED")
        if value is None:
            out[str(name)] = None
        elif isinstance(value, str) and value and len(value) <= 2000 and not re.search(r"[\r\n]", value):
            out[str(name)] = value
        else:
            raise McpConfigError(f"invalid value for header {name!r}")
    return out


def validate_env(env: Any) -> dict[str, str | None]:
    """Environment variables for a stdio server; a value of ``None`` means "keep the stored value"."""
    if not isinstance(env, dict):
        raise McpConfigError("the environment must be an object")
    out: dict[str, str | None] = {}
    for name, value in env.items():
        if not _ENV_NAME.match(str(name)):
            raise McpConfigError(f"invalid variable name {name!r}")
        if _FORBIDDEN_ENV.match(str(name)):
            raise McpConfigError(f"{name!r} is set by AIFRED and cannot be changed")
        if value is None:
            out[str(name)] = None
        elif isinstance(value, str) and value and len(value) <= 2000 and "\x00" not in value:
            out[str(name)] = value
        else:
            raise McpConfigError(f"invalid value for {name!r}")
    return out


def parse_command(value: Any) -> list[str]:
    """A command given as a list, or as a string split like a shell would (no shell is ever run)."""
    if isinstance(value, str):
        try:
            value = shlex.split(value)
        except ValueError as exc:
            raise McpConfigError(f"the command cannot be read: {exc}") from None
    if not isinstance(value, list) or not value or len(value) > MAX_COMMAND_ITEMS or not all(isinstance(v, str) and 0 < len(v) <= MAX_COMMAND_ITEM and "\x00" not in v and "\n" not in v for v in value):
        raise McpConfigError(f"the command must be 1 to {MAX_COMMAND_ITEMS} words (the program and its arguments)")
    return value


def _tool_name(server: str, tool: str, taken: set[str]) -> str:
    base = f"mcp__{server}__" + re.sub(r"[^A-Za-z0-9_-]", "_", tool)
    name = base
    if len(name) > TOOL_NAME_MAX or name in taken:
        digest = hashlib.sha256(f"{server}\0{tool}".encode()).hexdigest()[:6]
        name = f"{base[: TOOL_NAME_MAX - 7]}_{digest}"
    taken.add(name)
    return name


class McpService:
    """Definitions of MCP servers.

    Args:
        store: Database.
        vault: Encrypted secrets.
        config: Runtime settings (default timeouts and tool cap).
    """

    def __init__(self, store: Store, vault: Vault, config: RuntimeConfig, workdir: Path | None = None) -> None:
        self._store = store
        self._vault = vault
        self._config = config
        self._workdir = workdir or Path(tempfile.gettempdir()) / "aifred-mcp"

    def list(self) -> list[McpServer]:
        """Every defined server."""
        return [McpServer.from_row(r) for r in self._store.mcp_server_list()]

    def get(self, name: str) -> McpServer | None:
        """One server by name."""
        row = self._store.mcp_server_get(name)
        return McpServer.from_row(row) if row else None

    def headers(self, name: str) -> dict[str, str]:
        """Decrypted header values of a server.

        Raises:
            aifred.vault.VaultUnavailable: If the secrets backend cannot be reached.
        """
        raw = self._vault.get(secret_name(name))
        try:
            data = json.loads(raw) if raw else {}
        except ValueError:
            return {}
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}

    def workdir(self, name: str) -> Path:
        """The private working directory of a stdio server."""
        return self._workdir / name

    def defaults(self) -> tuple[float, float, int]:
        """Default call timeout, connect timeout and tool cap from the settings."""
        c = self._config
        return float(c.get("MCP_CALL_TIMEOUT")), float(c.get("MCP_CONNECT_TIMEOUT")), int(c.get("MCP_MAX_TOOLS_PER_SERVER"))

    def public(self, server: McpServer) -> dict[str, Any]:
        """Dashboard view: header names only, never values."""
        call, connect, cap = self.defaults()
        return {
            "name": server.name, "url": server.url, "enabled": server.enabled, "headers": list(server.header_names), "command": list(server.command),
            "verify_tls": server.verify_tls, "tls_unverified": server.verify_tls is False,
            "timeout": server.timeout or call, "connect_timeout": server.connect_timeout or connect,
            "protocol_version": server.protocol_version, "include": list(server.include), "exclude": list(server.exclude),
            "max_tools": server.max_tools or cap,
        }

    def normalise(self, name: str, data: dict[str, Any], current: McpServer | None) -> tuple[dict[str, Any], dict[str, str] | None]:
        """Validate fields; returns the row values and the new full header map (``None`` when headers are unchanged).

        Raises:
            McpConfigError: On invalid input.
        """
        if not NAME_RE.match(name):
            raise McpConfigError("the server name must be 1-24 characters of a-z, 0-9 and underscore")
        out: dict[str, Any] = {}
        command: list[str] | None = None
        if "command" in data:
            command = parse_command(data["command"]) if data["command"] not in (None, "", []) else []
        elif current and current.command:
            command = list(current.command)
        if command:
            allowed = {str(x) for x in self._config.get("MCP_STDIO_COMMANDS")}
            program = command[0]
            if "/" in program and not program.startswith("/"):
                raise McpConfigError("the program must be a name on the path or an absolute path, not a relative one")
            if os.path.basename(program) not in allowed:
                raise McpConfigError(f"{os.path.basename(program)!r} is not allowed: add it to MCP_STDIO_COMMANDS on the Config page first (a stdio server runs inside AIFRED with its privileges)")
            out["command"] = command
            out["url"] = f"stdio://{name}"
        else:
            if "command" in data:
                out["command"] = []
            url = data.get("url", current.url if current else None)
            if not isinstance(url, str) or not url.strip() or url.startswith("stdio://"):
                raise McpConfigError("url is required")
            parts = urlsplit(url.strip())
            if parts.scheme not in ("http", "https") or not parts.hostname:
                raise McpConfigError("url must be http:// or https:// with a host")
            if parts.username or parts.password:
                raise McpConfigError("put credentials in a header, not in the URL")
            out["url"] = url.strip()
        if "enabled" in data:
            if not isinstance(data["enabled"], bool):
                raise McpConfigError("enabled must be true or false")
            out["enabled"] = data["enabled"]
        if "verify_tls" in data or current is None:
            try:
                out["verify_tls"] = _verify_to_db(data.get("verify_tls", True))
            except ProviderError as exc:
                raise McpConfigError(str(exc)) from None
        for field_name, low, high in (("timeout", 1, 3600), ("connect_timeout", 1, 600)):
            if field_name in data:
                value = data[field_name]
                if value in (None, ""):
                    out[field_name] = None
                    continue
                try:
                    number = float(value)
                except (TypeError, ValueError):
                    raise McpConfigError(f"{field_name} must be a number of seconds") from None
                if isinstance(value, bool) or not low <= number <= high:
                    raise McpConfigError(f"{field_name} must be between {low} and {high} seconds")
                out[field_name] = number
        if "max_tools" in data:
            value = data["max_tools"]
            if value in (None, ""):
                out["max_tools"] = None
            elif isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 500:
                raise McpConfigError("max_tools must be a whole number between 1 and 500")
            else:
                out["max_tools"] = value
        if "protocol_version" in data:
            if data["protocol_version"] not in PROTOCOL_CHOICES:
                raise McpConfigError(f"protocol_version must be one of {', '.join(PROTOCOL_CHOICES)}")
            out["protocol_version"] = data["protocol_version"]
        for field_name in ("include", "exclude"):
            if field_name in data:
                value = data[field_name]
                if not isinstance(value, list) or not all(isinstance(v, str) and 0 < len(v) <= 200 for v in value):
                    raise McpConfigError(f"{field_name} must be a list of tool names")
                out[field_name] = list(dict.fromkeys(value))
        full: dict[str, str] | None = None
        if "headers" in data:
            requested = validate_env(data["headers"]) if command else validate_headers(data["headers"])
            stored = self.headers(name) if current else {}
            full = {}
            for header, value in requested.items():
                if value is None:
                    if header not in stored:
                        raise McpConfigError(f"header {header!r} has no stored value to keep")
                    value = stored[header]
                full[header] = value
            out["header_names"] = list(full)
        return out, full

    def preview(self, name: str, data: dict[str, Any]) -> tuple[McpServer, dict[str, str]]:
        """The definition and headers a save would produce, without saving (for the connection test)."""
        current = self.get(name)
        values, headers = self.normalise(name, data, current)
        merged = {**(self._store.mcp_server_get(name) or {"name": name, "enabled": True, "header_names": [], "include": [], "exclude": [],
                                                           "verify_tls": "true", "timeout": None, "connect_timeout": None,
                                                           "protocol_version": "auto", "max_tools": None, "updated": 0.0, "command": []}), **values, "name": name}
        return McpServer.from_row(merged), (headers if headers is not None else (self.headers(name) if current else {}))

    def save(self, name: str, data: dict[str, Any]) -> McpServer:
        """Create or update a server.

        Raises:
            McpConfigError: On invalid input.
        """
        current = self.get(name)
        values, headers = self.normalise(name, data, current)
        if headers is not None:
            if headers:
                self._vault.set(secret_name(name), json.dumps(headers))
            else:
                self._vault.delete(secret_name(name))
        created = self._store.mcp_server_save(name, values)
        saved = self.get(name)
        assert saved is not None
        summary = f"MCP server {name!r} {'added' if created else 'updated'}"
        if saved.verify_tls is False and (current is None or current.verify_tls is not False):
            summary += " (TLS verification disabled)"
            logger.warning("TLS verification is disabled for the MCP server %s", name)
        self._store.journal_add("settings", "mcp_server", summary)
        return saved

    def delete(self, name: str) -> None:
        """Remove a server and its header values.

        Raises:
            McpConfigError: If it does not exist.
        """
        if not self._store.mcp_server_delete(name):
            raise McpConfigError(f"no MCP server named {name!r}")
        self._vault.delete(secret_name(name))
        self._store.instruction_delete(f"mcp:{name}")
        self._store.instruction_delete_prefix(f"tool:mcp__{name}__")
        self._store.journal_add("settings", "mcp_server", f"MCP server {name!r} deleted")

    def export(self) -> dict[str, Any]:
        """The import format without header values."""
        servers: dict[str, Any] = {}
        for s in self.list():
            entry: dict[str, Any] = {"command": list(s.command)} if s.stdio else {"url": s.url}
            if not s.enabled:
                entry["enabled"] = False
            if s.header_names:
                entry["headers"] = {h: None for h in s.header_names}
            if s.verify_tls is not True:
                entry["verify_tls"] = s.verify_tls
            for key in ("timeout", "connect_timeout", "max_tools"):
                if getattr(s, key) is not None:
                    entry[key] = getattr(s, key)
            if s.protocol_version != "auto":
                entry["protocol_version"] = s.protocol_version
            if s.include:
                entry["include"] = list(s.include)
            if s.exclude:
                entry["exclude"] = list(s.exclude)
            servers[s.name] = entry
        return {"servers": servers}

    def import_file(self, path: Path) -> list[str]:
        """Import ``mcp.json`` once; header values are encrypted and the file is deleted afterwards.

        Returns:
            Names of the imported servers (empty when there was nothing to do).
        """
        if self._store.kv_get(IMPORT_KEY) or not path.is_file():
            return []
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
            servers = document["servers"]
            if not isinstance(servers, dict):
                raise TypeError
        except (ValueError, KeyError, TypeError, OSError) as exc:
            logger.error("Cannot import %s: %s", path, exc if isinstance(exc, OSError) else "it is not a valid MCP configuration")
            return []
        imported: list[str] = []
        for name, entry in servers.items():
            try:
                if not isinstance(entry, dict):
                    raise McpConfigError("the entry must be an object")
                if self.get(name):
                    continue
                headers = entry.get("headers")
                if isinstance(headers, dict):
                    entry = {**entry, "headers": {k: v for k, v in headers.items() if v}}
                self.save(name, entry)
                text = entry.get("instruction")
                if isinstance(text, str) and text.strip():
                    self._store.instruction_set(f"mcp:{name}", text.strip()[:2000])
                imported.append(name)
            except McpConfigError as exc:
                logger.error("Skipping MCP server %r from %s: %s", name, path, exc)
        self._store.kv_set(IMPORT_KEY, str(int(time.time())))
        try:
            path.unlink()
            logger.info("Imported %d MCP server(s) from %s and deleted the file", len(imported), path)
        except OSError:
            logger.warning("Imported %d MCP server(s); delete %s, it may hold header values in clear", len(imported), path)
        return imported


@dataclass
class ServerState:
    """Runtime state of one server (shown on the dashboard)."""

    server: McpServer
    status: str = "connecting"  # connecting | connected | error | disabled
    error: str = ""
    version: str = ""
    latency_ms: int | None = None
    client: McpClient | None = None
    tools: list[McpTool] = field(default_factory=list)
    dropped: list[tuple[str, str]] = field(default_factory=list)
    refresh_at: float = 0.0
    next_try: float = 0.0
    backoff: float = RETRY_START
    busy: bool = False
    stale: bool = False
    signature: dict[str, str] = field(default_factory=dict)
    server_text: str = ""


class McpManager:
    """Connections to all enabled servers and the registry source for their tools.

    Args:
        service: Server definitions.
        store: Database (journal).
        clock: Monotonic clock (tests).
        client_factory: Builds a client from a server and its headers (tests).
    """

    def __init__(
        self, service: McpService, store: Store, *, clock: Callable[[], float] = time.monotonic,
        client_factory: Callable[[McpServer, dict[str, str]], McpClient] | None = None,
    ) -> None:
        self._service = service
        self._store = store
        self._clock = clock
        self._factory = client_factory or self._default_factory
        self._lock = threading.RLock()
        self._states: dict[str, ServerState] = {}
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._pool: ThreadPoolExecutor | None = None

    def _default_factory(self, server: McpServer, headers: dict[str, str]) -> McpClient:
        call, connect, _ = self._service.defaults()
        if server.stdio:    # for a stdio server the stored "headers" are its environment variables
            return StdioClient(
                list(server.command), headers, self._service.workdir(server.name), timeout=server.timeout or call,
                connect_timeout=server.connect_timeout or connect, name=server.name,
            )
        return McpClient(
            server.url, headers, verify=server.verify_tls, timeout=server.timeout or call,
            connect_timeout=server.connect_timeout or connect, protocol_version=server.protocol_version,
        )

    # Lifecycle

    def start(self) -> None:
        """Start the background thread (idempotent) and load the definitions."""
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="mcp")
        self.reload()
        self._thread = threading.Thread(target=self._loop, name="mcp-manager", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the thread and close every connection."""
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        if self._pool:
            self._pool.shutdown(wait=False, cancel_futures=True)
            self._pool = None
        with self._lock:
            for state in self._states.values():
                self._close(state)
            self._states.clear()

    @staticmethod
    def _close(state: ServerState) -> None:
        client, state.client = state.client, None
        if client:
            try:
                client.close()
            except Exception:  # noqa: BLE001 - closing must never raise
                logger.debug("Closing an MCP client failed", exc_info=True)

    def reload(self) -> None:
        """Re-read the definitions: new servers connect, changed ones reconnect, removed ones close."""
        servers = {s.name: s for s in self._service.list()}
        with self._lock:
            for name in list(self._states):
                if name not in servers:
                    self._close(self._states.pop(name))
            for name, server in servers.items():
                state = self._states.get(name)
                if state is None or state.server != server:
                    if state:
                        self._close(state)
                    state = self._states[name] = ServerState(server)
                if not server.enabled:
                    self._close(state)
                    state.status, state.error, state.tools = "disabled", "", []
                elif state.status == "disabled":
                    state.status, state.next_try = "connecting", 0.0
        self._wake.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            now = self._clock()
            with self._lock:
                due = [
                    s for s in self._states.values()
                    if s.server.enabled and not s.busy and self._due(s, now)
                ]
                for state in due:
                    state.busy = True
            for state in due:
                if self._pool:
                    self._pool.submit(self._work, state)
            self._wake.wait(1.0)
            self._wake.clear()

    @staticmethod
    def _due(state: ServerState, now: float) -> bool:
        if state.client is None:
            return now >= state.next_try
        return state.stale or now >= state.refresh_at

    def _work(self, state: ServerState) -> None:
        try:
            if state.client is None:
                self._connect(state)
            else:
                self._refresh(state)
        except Exception:  # noqa: BLE001 - the background thread must survive anything
            logger.exception("Unexpected failure while handling MCP server %s", state.server.name)
        finally:
            state.busy = False

    def _connect(self, state: ServerState) -> None:
        server = state.server
        started = self._clock()
        client: McpClient | None = None
        try:
            headers = self._service.headers(server.name)
            if server.verify_tls is False:
                logger.warning("Connecting to MCP server %s without verifying its TLS certificate", server.name)
            if urlsplit(server.url).scheme == "http" and not _private_host(urlsplit(server.url).hostname or ""):
                logger.warning("MCP server %s is reached over plain HTTP on a public address; its headers travel in clear", server.name)
            client = self._factory(server, headers)
            client.connect()
            listing = client.list_tools()
        except Exception as exc:  # noqa: BLE001 - any failure is reported and retried
            if client:
                client.close()
            message = str(exc) if isinstance(exc, McpError) else f"{type(exc).__name__}: {exc}"
            with self._lock:
                state.status, state.error = "error", message
                state.next_try = self._clock() + state.backoff
                logger.warning("MCP server %s unavailable (retry in %.0f s): %s", server.name, state.backoff, message)
                state.backoff = min(state.backoff * 2, RETRY_MAX)
            return
        with self._lock:
            if self._states.get(server.name) is not state:  # removed or redefined while connecting
                client.close()
                return
            state.client, state.status, state.error, state.backoff = client, "connected", "", RETRY_START
            state.version, state.latency_ms = client.version, int((self._clock() - started) * 1000)
            state.server_text = client.instructions
            self._apply(state, listing.tools, listing.dropped, listing.ttl)
        logger.info("MCP server %s connected (protocol %s, %d tool(s))", server.name, client.version, len(state.tools))

    def _refresh(self, state: ServerState) -> None:
        client = state.client
        if client is None:
            return
        try:
            listing = client.list_tools()
        except Exception as exc:  # noqa: BLE001
            message = str(exc) if isinstance(exc, McpError) else f"{type(exc).__name__}: {exc}"
            with self._lock:
                self._close(state)
                state.status, state.error, state.next_try, state.stale = "error", message, self._clock(), False
            logger.warning("MCP server %s lost: %s", state.server.name, message)
            return
        with self._lock:
            self._apply(state, listing.tools, listing.dropped, listing.ttl)

    def _apply(self, state: ServerState, tools: list[McpTool], dropped: list[tuple[str, str]], ttl: float) -> None:
        """Record a fresh tool list; a changed definition is journaled (untrusted servers can alter tools silently)."""
        signature = {t.name: hashlib.sha256(json.dumps([t.description, t.schema], sort_keys=True).encode()).hexdigest() for t in tools}
        if state.signature:
            changed = sorted(n for n in signature if n in state.signature and signature[n] != state.signature[n])
            added = sorted(set(signature) - set(state.signature))
            removed = sorted(set(state.signature) - set(signature))
            if changed or added or removed:
                parts = [f"{label}: {', '.join(names)}" for label, names in (("changed", changed), ("added", added), ("removed", removed)) if names]
                self._store.journal_add("mcp", "tools_changed", f"MCP server {state.server.name!r} tools {'; '.join(parts)}"[:500])
        state.signature = signature
        state.tools, state.dropped = tools, dropped
        state.refresh_at = self._clock() + ttl
        state.stale = False

    # Queries

    def wait_ready(self, timeout: float) -> None:
        """Block until every enabled server has connected or failed once, or the timeout passes (the CLI uses it)."""
        deadline = self._clock() + timeout
        while self._clock() < deadline:
            with self._lock:
                pending = [s for s in self._states.values() if s.server.enabled and s.status == "connecting"]
            if not pending:
                return
            time.sleep(0.05)

    def status(self) -> list[dict[str, Any]]:
        """State of every server for the dashboard and ``/mcp``."""
        with self._lock:
            out = []
            for name in sorted(self._states):
                s = self._states[name]
                out.append({
                    **self._service.public(s.server), "state": s.status, "error": s.error, "protocol": s.version,
                    "tools": len(self._exposed(s)), "latency_ms": s.latency_ms, "dropped": [{"tool": n, "reason": r} for n, r in s.dropped],
                })
            return out

    def describe(self, name: str) -> tuple[str, list[McpTool]]:
        """The server's own ``instructions`` text and the tools it exposes (empty when unknown)."""
        with self._lock:
            state = self._states.get(name)
            return (state.server_text, self._exposed(state)) if state else ("", [])

    def _exposed(self, state: ServerState) -> list[McpTool]:
        """Tools after the include/exclude filters and the cap."""
        server = state.server
        tools = [t for t in state.tools if (not server.include or t.name in server.include) and t.name not in server.exclude]
        cap = server.max_tools or self._service.defaults()[2]
        if len(tools) > cap:
            logger.warning("MCP server %s offers %d tools; only the first %d are exposed", server.name, len(tools), cap)
        return tools[:cap]

    def tools(self) -> list[Tool]:
        """Registry source: the tools of every server that has connected at least once."""
        out: list[Tool] = []
        with self._lock:
            for name in sorted(self._states):
                state = self._states[name]
                if not state.server.enabled:
                    continue
                taken: set[str] = set()
                for tool in self._exposed(state):
                    out.append(self._make_tool(name, tool, _tool_name(name, tool.name, taken)))
        return out

    def _make_tool(self, server: str, tool: McpTool, exposed_name: str) -> Tool:
        def run(ctx: ToolContext, args: RawArgs) -> str:
            return self.call(server, tool.name, args.model_dump())

        description = tool.description or f"Tool {tool.name} of the MCP server {server}"
        return Tool(exposed_name, f"mcp:{server}", description, RawArgs, run, restricted_ok=False, raw_schema=tool.schema)

    def call(self, server: str, tool: str, arguments: dict[str, Any]) -> str:
        """Call a tool.

        Raises:
            ToolError: If the server is not connected or the call fails.
        """
        with self._lock:
            state = self._states.get(server)
            client = state.client if state else None
        if state is None or client is None:
            reason = state.error if state and state.error else "still connecting"
            raise ToolError(f"the MCP server {server!r} is not connected ({reason})")
        try:
            return client.call_tool(tool, arguments)
        except McpError as exc:
            with self._lock:
                state.stale = True  # re-read the tool list after any error
            self._wake.set()
            raise ToolError(f"MCP server {server!r}: {exc}") from None

    # Connection test

    def test(self, server: McpServer, headers: dict[str, str]) -> dict[str, Any]:
        """Connect once with a throw-away client (used before saving and by the dashboard test button).

        Returns:
            ``{"ok": bool, "error": str, "protocol": str, "tools": [names], "latency_ms": int, "dropped": [...]}``.
        """
        started = time.monotonic()
        client = self._factory(server, headers)
        try:
            client.connect()
            listing = client.list_tools()
        except (McpError, httpx.HTTPError) as exc:
            return {"ok": False, "error": str(exc), "protocol": "", "tools": [], "latency_ms": int((time.monotonic() - started) * 1000), "dropped": []}
        finally:
            client.close()
        return {
            "ok": True, "error": "", "protocol": client.version, "tools": [t.name for t in listing.tools], "server": client.server_name,
            "instructions": client.instructions, "tool_info": [tool_info(t) for t in listing.tools],
            "latency_ms": int((time.monotonic() - started) * 1000), "dropped": [{"tool": n, "reason": r} for n, r in listing.dropped],
        }


def tool_info(tool: McpTool) -> dict[str, Any]:
    """A tool's name, description and parameter names: what an instruction draft may be based on (never outputs)."""
    props = tool.schema.get("properties") if isinstance(tool.schema.get("properties"), dict) else {}
    return {"name": tool.name, "description": tool.description, "params": list(props)[:20]}


def _private_host(host: str) -> bool:
    """Loopback, private-range and single-label hosts, for which plain HTTP is not worth a warning."""
    import ipaddress

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return "." not in host or host.endswith((".local", ".lan", ".internal", ".home.arpa"))
    return address.is_private or address.is_loopback or address.is_link_local
