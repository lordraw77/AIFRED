"""MCP over standard input and output: AIFRED starts the server as a child process (spec 9.2).

Messages are JSON-RPC, one per line, on the child's stdin and stdout; its stderr goes to the log. The revision
negotiated is 2025-11-25 (the stateless 2026-07-28 revision is HTTP only). Starting a process is the most powerful
thing a server definition can do, so it is guarded three ways: nothing starts unless ``MCP_STDIO_COMMANDS`` lists the
executable, the process gets a minimal environment (none of AIFRED's own variables or secrets, only the ones the
admin set for that server), and it runs in its own session in a private working directory so it can be killed
together with anything it spawned.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import threading
from pathlib import Path
from typing import Any

from aifred import __version__
from aifred.mcp_client import ERR_METHOD_NOT_FOUND, LEGACY, McpClient, McpError

logger = logging.getLogger(__name__)

BASE_ENV = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8", "PYTHONUNBUFFERED": "1"}
MAX_LINE = 4 * 1024 * 1024


class StdioClient(McpClient):
    """One server run as a child process. Same interface as :class:`McpClient`.

    Args:
        command: Executable and arguments (no shell is involved).
        env: Variables added to the minimal environment.
        cwd: Private working directory (created when missing).
        timeout: Seconds for a tool call.
        connect_timeout: Seconds for starting the process and the handshake.
        name: Server name, for logs and the label shown as its address.
    """

    def __init__(self, command: list[str], env: dict[str, str], cwd: Path, *, timeout: float = 120.0, connect_timeout: float = 15.0, name: str = "stdio") -> None:
        self.url = f"stdio://{name}"
        self._command = list(command)
        self._env = {**BASE_ENV, "HOME": str(cwd), **env}
        self._cwd = cwd
        self._timeout = timeout
        self._connect_timeout = connect_timeout
        self._name = name
        self._lock = threading.Lock()
        self._ids = 0
        self.version = ""
        self.server_name = ""
        self.instructions = ""
        self._tools = {}
        self._proc: subprocess.Popen[bytes] | None = None
        self._waiting: dict[int, tuple[threading.Event, list[dict[str, Any]]]] = {}
        self._write_lock = threading.Lock()
        self._dead = ""

    # Process

    def connect(self) -> None:
        """Start the process and negotiate the protocol.

        Raises:
            McpError: If the process cannot start, exits, or does not answer the handshake in time.
        """
        self._cwd.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            self._proc = subprocess.Popen(  # noqa: S603 - argv list, no shell; the executable is on the admin's allowlist
                self._command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=self._cwd, env=self._env,
                start_new_session=True, close_fds=True,
            )
        except (OSError, ValueError) as exc:
            raise McpError(f"cannot start {self._command[0]!r}: {exc}") from None
        threading.Thread(target=self._read_stdout, name=f"mcp-{self._name}-out", daemon=True).start()
        threading.Thread(target=self._read_stderr, name=f"mcp-{self._name}-err", daemon=True).start()
        try:
            result = self._request("initialize", {
                "protocolVersion": LEGACY[0], "capabilities": {}, "clientInfo": {"name": "AIFRED", "version": __version__},
            }, self._connect_timeout)
            version = result.get("protocolVersion")
            if version not in LEGACY:
                raise McpError(f"the server speaks protocol {version!r}, which AIFRED does not support over stdio")
            self.version = version
            info = result.get("serverInfo") if isinstance(result.get("serverInfo"), dict) else {}
            self.server_name = str(info.get("name") or "")[:100]
            self.instructions = str(result.get("instructions") or "")[:4000]
            self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        except McpError:
            self.close()
            raise

    def close(self) -> None:
        """Stop the process and everything it started."""
        proc, self._proc = self._proc, None
        if proc is None:
            return
        for pipe in (proc.stdin,):
            try:
                if pipe:
                    pipe.close()
            except OSError:
                pass
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=2)
        except (ProcessLookupError, PermissionError):
            pass
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=2)
            except (ProcessLookupError, PermissionError, subprocess.TimeoutExpired):
                pass
        self._fail_all("the connection was closed")

    # Wire

    def _send(self, message: dict[str, Any]) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None or proc.poll() is not None:
            raise McpError(self._dead or "the server process is not running")
        data = (json.dumps(message, separators=(",", ":")) + "\n").encode()
        try:
            with self._write_lock:
                proc.stdin.write(data)
                proc.stdin.flush()
        except (OSError, ValueError):
            raise McpError(self._dead or "the server process closed its input") from None

    def _request(self, method: str, params: dict[str, Any] | None, timeout: float) -> dict[str, Any]:
        request_id = self._next_id()
        event: threading.Event = threading.Event()
        box: list[dict[str, Any]] = []
        with self._lock:
            self._waiting[request_id] = (event, box)
        try:
            self._send({"jsonrpc": "2.0", "id": request_id, "method": method, **({"params": params} if params is not None else {})})
            if not event.wait(timeout):
                raise McpError(f"the server did not answer {method} within {timeout:g} s")
        finally:
            with self._lock:
                self._waiting.pop(request_id, None)
        message = box[0]
        if "error" in message:
            error = message["error"] if isinstance(message["error"], dict) else {}
            raise McpError(str(error.get("message") or "the server returned an error")[:300], code=error.get("code") if isinstance(error.get("code"), int) else None)
        result = message.get("result")
        return result if isinstance(result, dict) else {}

    def _call(self, method: str, params: dict[str, Any] | None = None, **kw: Any) -> dict[str, Any]:
        return self._request(method, params, self._timeout)

    def _fail_all(self, reason: str) -> None:
        self._dead = self._dead or reason
        with self._lock:
            waiting = list(self._waiting.values())
        for event, box in waiting:
            if not box:
                box.append({"error": {"message": reason}})
            event.set()

    def _read_stdout(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stdout is not None
        try:
            for raw in proc.stdout:
                if len(raw) > MAX_LINE:
                    self._fail_all("the server sent a message that is too large")
                    break
                try:
                    message = json.loads(raw)
                except ValueError:
                    logger.debug("MCP server %s printed a line that is not JSON", self._name)
                    continue
                if not isinstance(message, dict):
                    continue
                if "id" in message and ("result" in message or "error" in message):
                    with self._lock:
                        slot = self._waiting.get(message["id"]) if isinstance(message["id"], int) else None
                    if slot:
                        slot[1].append(message)
                        slot[0].set()
                elif "id" in message and "method" in message:   # a request from the server: sampling, roots, elicitation are not offered
                    try:
                        self._send({"jsonrpc": "2.0", "id": message["id"], "error": {"code": ERR_METHOD_NOT_FOUND, "message": "not supported by AIFRED"}})
                    except McpError:
                        break
        except (OSError, ValueError):
            pass
        code = proc.poll()
        self._fail_all("the server process exited" + (f" with code {code}" if code is not None else ""))

    def _read_stderr(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stderr is not None
        try:
            for raw in proc.stderr:
                logger.info("MCP server %s: %s", self._name, raw.decode("utf-8", "replace").rstrip()[:300])
        except (OSError, ValueError):
            pass
