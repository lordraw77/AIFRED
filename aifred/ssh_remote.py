"""Remote terminal: run one command on a configured host over SSH (the ``ssh`` toolset, spec 9.1).

Hosts come from the ``SSH_HOSTS`` setting, a JSON object ``{"name": {"user", "host", "port", "host_key", "description"}}``. The model
chooses a host by name only and can never reach an address that is not listed. Every host must carry its public host key (the line
printed by ``ssh-keyscan -t ed25519 host``): the connection is refused when the server presents another one, and nothing is learned on
first use. One private key (``SSH_PRIVATE_KEY``, a secret) is used for all hosts; it reaches ``ssh`` through an anonymous in-memory file
and never has a name on disk and is not in the environment: ssh reads it from its standard input, which ``-n`` then points at /dev/null. The command is passed to the remote shell as one argument.
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aifred.hardening import spawn_guard, track, untrack

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
USER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,31}$")
HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{0,252}$")
KEY_TYPES = ("ssh-ed25519", "ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384", "ecdsa-sha2-nistp521", "ssh-rsa", "rsa-sha2-256", "rsa-sha2-512")
HOST_KEY_RE = re.compile(r"^(" + "|".join(re.escape(t) for t in KEY_TYPES) + r") ([A-Za-z0-9+/]{20,}={0,2})$")
PEM_RE = re.compile(r"^(-----BEGIN [A-Z ]+-----)\s*(.*?)\s*(-----END [A-Z ]+-----)$", re.S)
OUTPUT_CAP = 1_000_000


class SshError(Exception):
    """The remote command could not be run; the message is meant for the admin and the model."""


@dataclass(frozen=True)
class SshHost:
    """One configured host."""

    name: str
    user: str
    host: str
    port: int
    host_key: str
    description: str

    @property
    def key_type(self) -> str:
        return self.host_key.split(" ", 1)[0]

    @property
    def known_hosts_line(self) -> str:
        return f"{self.host if self.port == 22 else f'[{self.host}]:{self.port}'} {self.host_key}\n"


def parse_hosts(raw: Any) -> tuple[dict[str, SshHost], list[str]]:
    """The valid hosts of the ``SSH_HOSTS`` setting and one message per invalid entry."""
    hosts: dict[str, SshHost] = {}
    problems: list[str] = []
    if not isinstance(raw, dict):
        return hosts, ["SSH_HOSTS must be a JSON object"]
    for name, entry in raw.items():
        if not isinstance(name, str) or not NAME_RE.match(name):
            problems.append(f"{name!r}: the name must be 1 to 32 lowercase letters, digits, - or _")
            continue
        if not isinstance(entry, dict):
            problems.append(f"{name}: expected an object with user, host and host_key")
            continue
        user, host, port = str(entry.get("user") or ""), str(entry.get("host") or ""), entry.get("port", 22)
        key = " ".join(str(entry.get("host_key") or "").split()[:2])
        if not USER_RE.match(user):
            problems.append(f"{name}: invalid or missing user")
        elif not HOST_RE.match(host):
            problems.append(f"{name}: invalid or missing host")
        elif isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            problems.append(f"{name}: port must be a number from 1 to 65535")
        elif not HOST_KEY_RE.match(key):
            problems.append(f"{name}: host_key is required, as 'ssh-ed25519 AAAA…' (run: ssh-keyscan -t ed25519 {host})")
        else:
            hosts[name] = SshHost(name, user, host, port, key, " ".join(str(entry.get("description") or "").split())[:300])
    return hosts, problems


def normalize_key(text: str) -> str:
    """The private key as a PEM/OpenSSH block with proper lines (a one-line paste from a password field loses its line breaks)."""
    text = text.strip().replace("\\n", "\n")
    match = PEM_RE.match(text)
    if not match:
        raise SshError("SSH_PRIVATE_KEY is not a PEM or OpenSSH private key")
    body = re.sub(r"\s+", "", match.group(2))
    if "PRIVATE KEY" not in match.group(1) or not re.fullmatch(r"[A-Za-z0-9+/=:,._-]+", body) or "ENCRYPTED" in match.group(1) or "Proc-Type" in text:
        raise SshError("SSH_PRIVATE_KEY must be an unencrypted private key (a passphrase cannot be typed here)")
    lines = [body[i:i + 70] for i in range(0, len(body), 70)]
    return "\n".join([match.group(1), *lines, match.group(3)]) + "\n"


def _anonymous_file(content: str) -> int:
    """An unnamed file holding ``content`` and opened at its start, as a file descriptor to use as the child's standard input."""
    handle = tempfile.TemporaryFile()  # noqa: SIM115 - the descriptor outlives the function and is closed by the caller
    fd = os.dup(handle.fileno())
    handle.close()
    os.write(fd, content.encode())
    os.fchmod(fd, 0o600)
    os.lseek(fd, 0, os.SEEK_SET)
    return fd


def run(host: SshHost, key: str, command: str, timeout: int, workdir: Path, ssh_binary: str = "ssh") -> tuple[str, int, bool]:
    """Run ``command`` on ``host``: ``(output, exit code, timed out)``.

    Raises:
        SshError: If ``ssh`` is not installed or the key cannot be used.
    """
    key_fd = _anonymous_file(normalize_key(key))
    try:
        with tempfile.TemporaryDirectory(prefix="aifred-ssh-") as private:
            known = Path(private) / "known_hosts"
            known.write_text(host.known_hosts_line)
            args = _arguments(host, str(known), command, ssh_binary)
            return _spawn(args, key_fd, timeout, workdir)
    finally:
        os.close(key_fd)


def _arguments(host: SshHost, known_hosts: str, command: str, ssh_binary: str) -> list[str]:
    return [
        ssh_binary, "-F", "/dev/null", "-T", "-n", "-o", "LogLevel=ERROR",
        "-i", "/dev/stdin", "-o", "IdentitiesOnly=yes", "-o", "IdentityAgent=none",
        "-o", f"UserKnownHostsFile={known_hosts}", "-o", "GlobalKnownHostsFile=/dev/null", "-o", "StrictHostKeyChecking=yes",
        "-o", f"HostKeyAlgorithms={host.key_type}", "-o", "BatchMode=yes", "-o", "PasswordAuthentication=no", "-o", "ConnectTimeout=10",
        "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3", "-o", "ForwardAgent=no", "-o", "ForwardX11=no",
        "-o", "ClearAllForwardings=yes", "-o", "PermitLocalCommand=no", "-o", "ProxyCommand=none", "-o", "ControlMaster=no",
        "-p", str(host.port), "--", f"{host.user}@{host.host}", command,
    ]


def _spawn(args: list[str], key_fd: int, timeout: int, workdir: Path) -> tuple[str, int, bool]:
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(workdir), "LANG": "C.UTF-8"}
    with tempfile.TemporaryFile() as out:
        try:
            with spawn_guard():
                proc = subprocess.Popen(  # noqa: S603 - fixed argument list; host, user and port were validated
                    args, cwd=workdir, env=env, stdin=key_fd, stdout=out, stderr=subprocess.STDOUT, start_new_session=True,
                )
                track(proc.pid)
        except FileNotFoundError:
            raise SshError("the ssh client is not installed in this environment") from None
        timed_out = False
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            code = proc.wait()
        finally:
            untrack(proc.pid)
        out.seek(0)
        raw = out.read(OUTPUT_CAP + 1)
    text = raw[:OUTPUT_CAP].decode("utf-8", errors="replace")
    if len(raw) > OUTPUT_CAP:
        text += "\n[output cut]"
    return text, code, timed_out
