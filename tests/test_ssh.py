"""Tests for the remote terminal (aifred.ssh_remote and the ssh toolset); the live ones start a throw-away sshd on localhost."""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from aifred import ssh_remote
from aifred.app import Services
from aifred.tools.registry import ToolContext

pytestmark = pytest.mark.anyio

KEY = "AAAAC3NzaC1lZDI1NTE5AAAAIOMqqetwAFnGKAbBtvIDFVTPkUFxSkF9fmbVMWXqjEUR"
HOST_KEY = f"ssh-ed25519 {KEY}"


def hosts(**over: Any) -> dict[str, Any]:
    return {"pve1": {"user": "root", "host": "10.70.0.1", "host_key": HOST_KEY, "description": "Proxmox  node", **over}}


def test_hosts_are_validated_one_by_one() -> None:
    good, problems = ssh_remote.parse_hosts({**hosts(), "Bad Name": hosts()["pve1"], "nokey": {"user": "u", "host": "h.example"}, "dash": hosts(host="-oProxyCommand=x")["pve1"]})
    assert list(good) == ["pve1"] and good["pve1"].description == "Proxmox node" and good["pve1"].port == 22
    assert len(problems) == 3 and any("host_key is required" in p for p in problems) and any("invalid or missing host" in p for p in problems)
    assert ssh_remote.parse_hosts(hosts(port=0))[0] == {} and ssh_remote.parse_hosts(hosts(port="22"))[0] == {}
    assert ssh_remote.parse_hosts(hosts(user="a b"))[0] == {} and ssh_remote.parse_hosts(hosts(host_key="ssh-ed25519 short"))[0] == {}
    assert ssh_remote.parse_hosts(["x"])[1] and ssh_remote.parse_hosts({})[0] == {}


def test_known_hosts_line_names_the_port_when_it_is_not_22() -> None:
    assert ssh_remote.parse_hosts(hosts())[0]["pve1"].known_hosts_line == f"10.70.0.1 {HOST_KEY}\n"
    assert ssh_remote.parse_hosts(hosts(port=2222))[0]["pve1"].known_hosts_line == f"[10.70.0.1]:2222 {HOST_KEY}\n"


def test_a_key_pasted_on_one_line_is_rebuilt_and_a_passphrase_key_refused() -> None:
    body = "b3BlbnNzaC1rZXktdjEAAAAABG5vbmU" * 6
    one_line = f"-----BEGIN OPENSSH PRIVATE KEY----- {body} -----END OPENSSH PRIVATE KEY-----"
    fixed = ssh_remote.normalize_key(one_line)
    lines = fixed.splitlines()
    assert lines[0] == "-----BEGIN OPENSSH PRIVATE KEY-----" and lines[-1] == "-----END OPENSSH PRIVATE KEY-----" and "".join(lines[1:-1]) == body
    assert all(len(line) <= 70 for line in lines[1:-1]) and ssh_remote.normalize_key(fixed) == fixed
    for bad in ("ssh-ed25519 AAAA", "-----BEGIN CERTIFICATE----- abc -----END CERTIFICATE-----", "-----BEGIN ENCRYPTED PRIVATE KEY----- abc -----END ENCRYPTED PRIVATE KEY-----"):
        with pytest.raises(ssh_remote.SshError):
            ssh_remote.normalize_key(bad)


def test_the_command_is_one_argument_and_the_host_cannot_inject_options() -> None:
    host = ssh_remote.parse_hosts(hosts(port=2222))[0]["pve1"]
    args = ssh_remote._arguments(host, "/k", "ls; echo '$HOME' && id", "ssh")
    assert args[-3:] == ["--", "root@10.70.0.1", "ls; echo '$HOME' && id"] and "StrictHostKeyChecking=yes" in args and "BatchMode=yes" in args
    assert "HostKeyAlgorithms=ssh-ed25519" in args and "-n" in args and args[args.index("-p") + 1] == "2222"
    assert "ForwardAgent=no" in args and "ClearAllForwardings=yes" in args and "ProxyCommand=none" in args


# The toolset ----------------------------------------------------------------------------------------------


def ctx(services: Services, source: str = "cli", restricted: bool = False) -> ToolContext:
    return ToolContext(services, f"{source}:1", source, restricted)


def names(services: Services, **kw: Any) -> set[str]:
    return {t.name for t in services.tools.for_context(ctx(services, **kw))}


async def call(services: Services, name: str, args: dict[str, Any], **kw: Any) -> str:
    return await services.tools.execute(ctx(services, **kw), name, json.dumps(args))


def test_the_tools_appear_only_when_enabled_and_configured(services: Services) -> None:
    assert not {"ssh_run", "ssh_hosts"} & names(services)
    services.config.update({"TOOLSETS": "memory,ssh"})
    assert not {"ssh_run", "ssh_hosts"} & names(services)                      # no host, no key
    services.config.update({"SSH_HOSTS": hosts()})
    assert not {"ssh_run", "ssh_hosts"} & names(services)                      # still no key
    services.config.update({"SSH_PRIVATE_KEY": "-----BEGIN OPENSSH PRIVATE KEY----- abc -----END OPENSSH PRIVATE KEY-----"})
    assert {"ssh_run", "ssh_hosts"} <= names(services)
    assert not {"ssh_run", "ssh_hosts"} & names(services, source="telegram", restricted=True)   # never for an open bot


async def test_unknown_hosts_and_blocked_commands_are_refused(services: Services) -> None:
    services.config.update({"TOOLSETS": "ssh", "SSH_HOSTS": hosts(), "SSH_PRIVATE_KEY": "-----BEGIN OPENSSH PRIVATE KEY----- abc -----END OPENSSH PRIVATE KEY-----"})
    assert "pve1: root@10.70.0.1:22 - Proxmox node" in await call(services, "ssh_hosts", {})
    assert "unknown host 'nas'" in await call(services, "ssh_run", {"host": "nas", "command": "ls"})
    assert "blocked: shutting down or rebooting" in await call(services, "ssh_run", {"host": "pve1", "command": "sudo reboot"})


async def test_a_missing_ssh_client_is_reported(services: Services, monkeypatch: pytest.MonkeyPatch) -> None:
    services.config.update({"TOOLSETS": "ssh", "SSH_HOSTS": hosts(), "SSH_PRIVATE_KEY": "-----BEGIN OPENSSH PRIVATE KEY----- abc -----END OPENSSH PRIVATE KEY-----"})
    monkeypatch.setattr("aifred.ssh_remote._arguments", lambda *a, **k: ["/nonexistent/ssh"])
    assert "ssh client is not installed" in await call(services, "ssh_run", {"host": "pve1", "command": "ls"})


# A real server --------------------------------------------------------------------------------------------

SSHD = shutil.which("sshd") or "/usr/sbin/sshd"
LIVE = bool(shutil.which("ssh") and shutil.which("ssh-keygen") and Path(SSHD).exists())


@pytest.fixture
def server(tmp_path: Path):
    if not LIVE:
        pytest.skip("needs ssh, ssh-keygen and sshd")
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    for name in ("hostkey", "client"):
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(tmp_path / name)], check=True)
    (tmp_path / "authorized_keys").write_text((tmp_path / "client.pub").read_text())
    (tmp_path / "sshd_config").write_text(
        f"Port {port}\nListenAddress 127.0.0.1\nHostKey {tmp_path}/hostkey\nPidFile {tmp_path}/sshd.pid\nAuthorizedKeysFile {tmp_path}/authorized_keys\n"
        "PasswordAuthentication no\nPermitRootLogin yes\nStrictModes no\nUsePAM no\n"
    )
    proc = subprocess.Popen([SSHD, "-D", "-f", str(tmp_path / "sshd_config"), "-E", str(tmp_path / "log")])
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
            break
        except OSError:
            time.sleep(0.1)
    try:
        import getpass

        yield {
            "port": port, "user": getpass.getuser(), "key": (tmp_path / "client").read_text(),
            "host_key": " ".join((tmp_path / "hostkey.pub").read_text().split()[:2]),
        }
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def configure(services: Services, server: dict[str, Any], **over: Any) -> None:
    entry = {"user": server["user"], "host": "127.0.0.1", "port": server["port"], "host_key": server["host_key"], **over}
    services.config.update({"TOOLSETS": "ssh", "SSH_HOSTS": {"box": entry}, "SSH_PRIVATE_KEY": server["key"].replace("\n", " ").strip(), "SSH_TIMEOUT": 20})


async def test_a_command_runs_on_the_server_with_its_exit_code(services: Services, server: dict[str, Any]) -> None:
    configure(services, server)
    out = await call(services, "ssh_run", {"host": "box", "command": "echo hello; echo oops >&2; exit 3"})
    assert out.splitlines() == ["hello", "oops", "[exit code: 3]"]
    assert (await call(services, "ssh_run", {"host": "box", "command": "cat"})).endswith("[exit code: 0]")   # stdin is empty, never the key


async def test_a_server_with_another_host_key_is_refused(services: Services, server: dict[str, Any]) -> None:
    configure(services, server, host_key="ssh-ed25519 " + KEY)
    out = await call(services, "ssh_run", {"host": "box", "command": "echo hello"})
    assert "hello" not in out and "[exit code: 255]" in out and "Host key verification failed" in out


async def test_a_slow_command_is_killed(services: Services, server: dict[str, Any]) -> None:
    configure(services, server)
    start = time.time()
    out = await call(services, "ssh_run", {"host": "box", "command": "sleep 30", "timeout": 1})
    assert "timed out after 1 s" in out and time.time() - start < 10


async def test_the_key_never_reaches_the_remote_command_or_a_file(services: Services, server: dict[str, Any], tmp_path: Path) -> None:
    configure(services, server)
    out = await call(services, "ssh_run", {"host": "box", "command": "echo $SSH_PRIVATE_KEY; env | grep -c PRIVATE"})
    assert "BEGIN" not in out
    import tempfile

    assert not list(Path(tempfile.gettempdir()).glob("aifred-ssh-*"))   # the private directory is gone after the call
