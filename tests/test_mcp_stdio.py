"""Tests for MCP servers run as child processes (aifred.mcp_stdio and its guards in aifred.mcp)."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from aifred.app import Services
from aifred.mcp import McpConfigError, parse_command, validate_env
from aifred.mcp_client import McpError
from aifred.mcp_stdio import StdioClient

SERVER = str(Path(__file__).with_name("fake_mcp_stdio.py"))


def client(tmp_path: Path, mode: str = "", env: dict[str, str] | None = None, **kw: float) -> StdioClient:
    return StdioClient([sys.executable, SERVER, mode] if mode else [sys.executable, SERVER], env or {}, tmp_path / "work", name="fake", **kw)


def test_handshake_listing_and_calls(tmp_path: Path) -> None:
    c = client(tmp_path)
    c.connect()
    try:
        assert c.version == "2025-11-25" and c.server_name == "fake-stdio" and c.instructions == "Stdio test server."
        assert [t.name for t in c.list_tools().tools] == ["echo", "env", "cwd", "spawn", "ask"]
        assert c.call_tool("echo", {"text": "ciao"}) == "ciao"
        assert c.call_tool("echo", {"text": "again"}) == "again"
    finally:
        c.close()


def test_the_child_sees_a_minimal_environment_and_a_private_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIFRED_ADMIN_PASSWORD", "hunter2")
    monkeypatch.setenv("SOME_API_KEY", "secret")
    c = client(tmp_path, env={"MY_TOKEN": "abc"})
    c.connect()
    try:
        names = set(c.call_tool("env", {}).split(","))
        assert {"MY_TOKEN", "PATH", "HOME"} <= names                          # the minimal set plus what the admin gave this server
        assert "AIFRED_ADMIN_PASSWORD" not in names and "SOME_API_KEY" not in names
        assert Path(c.call_tool("cwd", {})).resolve() == (tmp_path / "work").resolve()
        assert oct((tmp_path / "work").stat().st_mode & 0o777) == "0o700"
    finally:
        c.close()


def test_a_crash_is_reported_and_close_is_safe(tmp_path: Path) -> None:
    c = client(tmp_path, "crash")
    c.connect()
    with pytest.raises(McpError, match="exited with code 3"):
        c.call_tool("echo", {"text": "x"})
    with pytest.raises(McpError):
        c.call_tool("echo", {"text": "x"})                                    # a dead process stays dead until reconnected
    c.close()
    c.close()


def test_a_server_that_does_not_answer_times_out_and_is_killed(tmp_path: Path) -> None:
    c = client(tmp_path, "hang", timeout=1.0)
    c.connect()
    began = time.monotonic()
    with pytest.raises(McpError, match="did not answer"):
        c.call_tool("echo", {"text": "x"})
    c.close()
    assert time.monotonic() - began < 8
    assert c._proc is None


def test_no_handshake_answer_fails_the_connection(tmp_path: Path) -> None:
    c = client(tmp_path, "noinit", connect_timeout=1.0)
    with pytest.raises(McpError, match="did not answer initialize"):
        c.connect()
    assert c._proc is None


def test_a_missing_program_is_an_error_not_a_crash(tmp_path: Path) -> None:
    c = StdioClient(["/nonexistent/program"], {}, tmp_path / "w", name="x")
    with pytest.raises(McpError, match="cannot start"):
        c.connect()


def alive(pid: int) -> bool:
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"      # a zombie is dead, just not reaped
    except OSError:
        return False


def test_close_kills_what_the_server_started_too(tmp_path: Path) -> None:
    c = client(tmp_path)
    c.connect()
    grandchild = int(c.call_tool("spawn", {}))
    assert alive(grandchild)
    c.close()
    deadline = time.monotonic() + 5
    while alive(grandchild) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not alive(grandchild)


def test_requests_from_the_server_are_refused_politely(tmp_path: Path) -> None:
    c = client(tmp_path)
    c.connect()
    try:
        assert c.call_tool("ask", {}) == "-32601"                                  # sampling, roots and elicitation are not offered
    finally:
        c.close()


def test_close_kills_the_whole_process_group(tmp_path: Path) -> None:
    c = client(tmp_path)
    c.connect()
    assert c._proc is not None
    pid = c._proc.pid
    c.close()
    time.sleep(0.2)
    with pytest.raises(ProcessLookupError):
        os.killpg(pid, 0)


# The guards in the definition ---------------------------------------------------------------------------


def test_parse_command_and_env_validation() -> None:
    assert parse_command("uvx some-server --flag 'a b'") == ["uvx", "some-server", "--flag", "a b"]
    assert parse_command(["python", "x.py"]) == ["python", "x.py"]
    for bad in ("", [], [""], ["a\nb"], "'unterminated", ["x"] * 30):
        with pytest.raises(McpConfigError):
            parse_command(bad)
    assert validate_env({"API_KEY": "v", "KEEP": None}) == {"API_KEY": "v", "KEEP": None}
    for bad in ({"PATH": "x"}, {"ld_preload": "x"}, {"LD_LIBRARY_PATH": "x"}, {"PYTHONPATH": "x"}, {"1BAD": "x"}, {"OK": ""}, {"OK": "a\x00b"}):
        with pytest.raises(McpConfigError):
            validate_env(bad)


def test_a_stdio_server_needs_its_program_on_the_allowlist(services: Services) -> None:
    mcp = services.mcp
    with pytest.raises(McpConfigError, match="MCP_STDIO_COMMANDS"):
        mcp.save("local", {"command": [sys.executable, SERVER]})
    services.config.update({"MCP_STDIO_COMMANDS": [os.path.basename(sys.executable)]})
    saved = mcp.save("local", {"command": [sys.executable, SERVER], "headers": {"MY_TOKEN": "abc"}})
    assert saved.stdio and saved.url == "stdio://local" and saved.header_names == ("MY_TOKEN",)
    assert mcp.headers("local") == {"MY_TOKEN": "abc"}
    assert mcp.export()["servers"]["local"] == {"command": [sys.executable, SERVER], "headers": {"MY_TOKEN": None}}
    with pytest.raises(McpConfigError, match="relative"):
        mcp.save("rel", {"command": ["./bin/python", "x"]})
    with pytest.raises(McpConfigError, match="set by AIFRED"):
        mcp.save("local", {"headers": {"LD_PRELOAD": "/x.so"}})
    with pytest.raises(McpConfigError, match="not allowed"):
        mcp.save("other", {"command": ["/bin/sh", "-c", "id"]})                     # not on the list, even with an absolute path


def test_removing_the_program_from_the_allowlist_blocks_edits(services: Services) -> None:
    name = os.path.basename(sys.executable)
    services.config.update({"MCP_STDIO_COMMANDS": [name]})
    services.mcp.save("local", {"command": [sys.executable, SERVER]})
    services.config.update({"MCP_STDIO_COMMANDS": []})
    with pytest.raises(McpConfigError, match="MCP_STDIO_COMMANDS"):
        services.mcp.save("local", {"enabled": True})                               # the existing command is checked again on every save


def test_switching_a_server_back_to_http(services: Services) -> None:
    services.config.update({"MCP_STDIO_COMMANDS": [os.path.basename(sys.executable)]})
    services.mcp.save("both", {"command": [sys.executable, SERVER]})
    saved = services.mcp.save("both", {"command": [], "url": "https://mcp.example.org/mcp", "headers": {}})
    assert not saved.stdio and saved.url == "https://mcp.example.org/mcp"
    with pytest.raises(McpConfigError, match="url is required"):
        services.mcp.save("again", {"url": "stdio://x"})


def test_the_manager_connects_a_stdio_server_and_its_tools_run(services: Services) -> None:
    services.config.update({"MCP_STDIO_COMMANDS": [os.path.basename(sys.executable)]})
    services.mcp.save("local", {"command": [sys.executable, SERVER], "headers": {"MY_TOKEN": "abc"}})
    services.mcp_manager.start()
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and not [t for t in services.tools.all() if t.name.startswith("mcp__local__")]:
            time.sleep(0.1)
        names = sorted(t.name for t in services.tools.all() if t.name.startswith("mcp__local__"))
        assert names == ["mcp__local__ask", "mcp__local__cwd", "mcp__local__echo", "mcp__local__env", "mcp__local__spawn"]
        [status] = [s for s in services.mcp_manager.status() if s["name"] == "local"]
        assert status["state"] == "connected" and status["protocol"] == "2025-11-25"
        result = services.mcp_manager.test(*services.mcp.preview("local", {}))
        assert result["ok"] and result["tools"] == ["echo", "env", "cwd", "spawn", "ask"]
    finally:
        services.mcp_manager.stop()
