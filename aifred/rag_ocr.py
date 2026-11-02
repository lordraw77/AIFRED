"""Reading text from images and scanned pages with a local OCR engine (spec 9.4, "Local OCR").

The vision model (``rag_vision``) understands pictures but sends them to a provider and counts against a daily limit.
``tesseract`` reads printed text on the machine itself, for free and without a limit; it knows nothing about what a
picture shows. ``RAG_OCR_ENGINE`` picks one, or ``auto`` (tesseract first, the vision model when tesseract is missing or
finds no text). The binary is native code reading untrusted pixels, so it runs like the document parsers do: alone, in an
empty directory with an empty environment, with CPU, memory and wall-clock limits, and the image arrives on stdin.
"""

from __future__ import annotations

import os
import re
import resource
import shutil
import signal
import subprocess
import tempfile

from aifred.hardening import spawn_guard, track, untrack

BINARY = "tesseract"
LANGS_RE = re.compile(r"^[a-z]{3}(_[A-Za-z]{2,8})?(\+[a-z]{3}(_[A-Za-z]{2,8})?){0,5}$")
MAX_TEXT = 200_000


class OcrUnavailable(Exception):
    """The OCR engine is not installed."""


class OcrError(Exception):
    """The OCR engine failed on this image."""


def executable() -> str | None:
    """Path of the ``tesseract`` binary, or ``None`` when it is not installed."""
    return shutil.which(BINARY)


def languages() -> list[str]:
    """Language packs installed with tesseract (empty when it is missing)."""
    path = executable()
    if not path:
        return []
    try:
        out = subprocess.run([path, "--list-langs"], capture_output=True, text=True, timeout=15, check=False, env={"PATH": "/usr/bin:/bin"})  # noqa: S603
    except (OSError, subprocess.SubprocessError):
        return []
    return [line.strip() for line in out.stdout.splitlines()[1:] if line.strip()]


def _limits(cpu: int, memory_mb: int) -> None:  # pragma: no cover - runs in the child between fork and exec
    for which, value in ((resource.RLIMIT_CORE, 0), (resource.RLIMIT_CPU, cpu), (resource.RLIMIT_NOFILE, 64), (resource.RLIMIT_AS, memory_mb * 1024 * 1024)):
        try:
            resource.setrlimit(which, (value, value))
        except (ValueError, OSError):
            pass


def read(image: bytes, langs: str, *, timeout: float, memory_mb: int) -> str:
    """The text in an image (PNG, JPEG or WebP), as tesseract reads it.

    Args:
        image: Image bytes (already stripped of metadata).
        langs: Language packs joined by ``+`` (``eng+ita``).
        timeout: Wall-clock seconds; the CPU limit is the same plus a second.
        memory_mb: Address-space limit of the child.

    Raises:
        OcrUnavailable: If tesseract is not installed.
        OcrError: On a bad language string, a timeout, a crash or a failure.
    """
    path = executable()
    if path is None:
        raise OcrUnavailable("tesseract is not installed (the image of AIFRED includes it; a plain install needs the tesseract-ocr package)")
    if not LANGS_RE.match(langs):
        raise OcrError(f"invalid RAG_OCR_LANGS {langs!r}; use language codes joined by + (for example eng+ita)")
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8", "OMP_THREAD_LIMIT": "1"}
    if prefix := os.environ.get("TESSDATA_PREFIX"):
        env["TESSDATA_PREFIX"] = prefix
    with tempfile.TemporaryDirectory(prefix="aifred-ocr-") as workdir:
        os.chmod(workdir, 0o700)
        with spawn_guard():
            proc = subprocess.Popen(  # noqa: S603 - fixed argument list, no shell
                [path, "stdin", "stdout", "-l", langs, "--psm", "3"], cwd=workdir, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, start_new_session=True, preexec_fn=lambda: _limits(int(timeout) + 1, memory_mb),  # noqa: PLW1509
            )
            track(proc.pid)
        try:
            out, err = proc.communicate(image, timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill(proc)
            proc.communicate()
            raise OcrError(f"the OCR engine took longer than {timeout:g} s and was stopped") from None
        except BrokenPipeError:
            _kill(proc)
            proc.wait()
            raise OcrError("the OCR engine stopped before it read the image") from None
        finally:
            _kill(proc)
            untrack(proc.pid)
    if proc.returncode:
        detail = err.decode(errors="replace").strip().splitlines()[-1:] or [f"exit code {proc.returncode}"]
        raise OcrError(f"the OCR engine failed: {detail[0][:200]}")
    return re.sub(r"\n{3,}", "\n\n", out.decode("utf-8", errors="replace")).strip()[:MAX_TEXT]


def _kill(proc: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
