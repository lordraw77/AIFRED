"""Runs the parsers of ``rag_parse`` in a short-lived child process (spec 9.4, "Hostile files").

PDF, Office and image files are untrusted input to parsers, and ``pypdfium2`` is native code. The child gets a minimal
environment (none of AIFRED's credentials), an empty working directory, a wall-clock limit, a CPU limit, an address-space
limit and no core dumps; a crash, a hang or a memory bomb costs one document and nothing else. The data goes in through
stdin and the answer comes back as JSON on stdout.

The limit of this isolation is documented in ADR-004: it does not stop a parser exploit from reading files the service user can
read, so the container remains the real boundary.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from aifred.hardening import spawn_guard, track, untrack

BOOT = r"""
import json, os, resource, sys
sys.path.insert(0, sys.argv[1])
lim = json.loads(sys.argv[2])
def cap(which, value):
    try:
        resource.setrlimit(which, (value, value))
    except (ValueError, OSError):
        pass
cap(resource.RLIMIT_CORE, 0)
cap(resource.RLIMIT_CPU, int(lim["cpu"]))
cap(resource.RLIMIT_NOFILE, 32)
cap(resource.RLIMIT_AS, int(lim["memory"]) * 1024 * 1024)
raw = sys.stdin.buffer.read()
header, _, data = raw.partition(b"\n")
from aifred import rag_parse
try:
    out = rag_parse.run(json.loads(header), data)
except MemoryError:
    out = {"error": "the file needs more memory than the parser may use"}
except Exception as exc:
    out = {"error": "the parser failed (" + type(exc).__name__ + ")"}
sys.stdout.buffer.write(json.dumps(out).encode("ascii"))
sys.stdout.buffer.flush()
"""


class SandboxError(Exception):
    """The child could not produce an answer in time or within its limits; the message is shown to the admin."""


def run_parser(command: dict[str, Any], data: bytes, *, timeout: float, memory_mb: int, max_output: int) -> dict[str, Any]:
    """Run one parser command on ``data`` in a limited child process.

    Args:
        command: ``{"cmd": "parse" | "render", "limits": {...}, ...}`` for :func:`aifred.rag_parse.run`.
        data: The file.
        timeout: Wall-clock seconds; the CPU limit is the same plus a second.
        memory_mb: Address-space limit of the child.
        max_output: Largest answer accepted, in bytes.

    Returns:
        The child's answer (which has an ``error`` key when the file could not be read).

    Raises:
        SandboxError: On a timeout, a crash, an oversized or invalid answer.
    """
    package_root = str(Path(__file__).resolve().parent.parent)
    limits = json.dumps({"cpu": int(timeout) + 1, "memory": int(memory_mb)})
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONHASHSEED": "random"}
    with tempfile.TemporaryDirectory(prefix="aifred-parse-") as workdir:
        os.chmod(workdir, 0o700)
        with spawn_guard():  # as PID 1 the service reaps orphans; this child is waited for here
            proc = subprocess.Popen(  # noqa: S603 - fixed argument list, no shell
                [sys.executable, "-I", "-c", BOOT, package_root, limits], cwd=workdir, env=env, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True,
            )
            track(proc.pid)
        try:
            out, _ = proc.communicate(json.dumps(command).encode() + b"\n" + data, timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill(proc)
            proc.communicate()
            raise SandboxError(f"reading the file took longer than {timeout:g} s and was stopped") from None
        except BrokenPipeError:
            _kill(proc)
            proc.wait()
            raise SandboxError("the parser stopped before it read the file") from None
        finally:
            _kill(proc)
            untrack(proc.pid)
    if proc.returncode and proc.returncode < 0:
        name = signal.Signals(-proc.returncode).name if -proc.returncode in signal.Signals._value2member_map_ else str(-proc.returncode)
        reason = "used too much CPU time" if name == "SIGXCPU" else f"crashed ({name})"
        raise SandboxError(f"the parser {reason} on this file")
    if proc.returncode:
        raise SandboxError(f"the parser failed (exit code {proc.returncode}) on this file")
    if len(out) > max_output:
        raise SandboxError("the parser's answer is larger than allowed")
    try:
        result = json.loads(out)
    except ValueError:
        raise SandboxError("the parser gave no usable answer") from None
    if not isinstance(result, dict):
        raise SandboxError("the parser gave no usable answer")
    return result


def _kill(proc: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
