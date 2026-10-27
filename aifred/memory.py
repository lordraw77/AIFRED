"""Curated, size-limited memory in two files (spec section 7).

``MEMORY.md`` holds the agent's notes and ``USER.md`` the user profile. Entries are
separated by ``\\n§\\n``. Every change is a read-modify-write under an exclusive
``flock`` on ``data/.lock`` so ``serve`` and ``cli`` never overwrite each other.
"""

from __future__ import annotations

import fcntl
import logging
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

SEPARATOR = "\n§\n"
Target = Literal["memory", "user"]
FILES: dict[str, str] = {"memory": "MEMORY.md", "user": "USER.md"}
LIMITS: dict[str, int] = {"memory": 2200, "user": 1375}


class MemoryError_(Exception):  # noqa: N801 - avoids shadowing the builtin MemoryError
    """A memory operation was refused; the message is meant for the model."""


@dataclass(frozen=True)
class Snapshot:
    """Contents of one memory file.

    Attributes:
        target: ``memory`` or ``user``.
        entries: Entries in order.
        used: Characters used.
        limit: Character limit.
    """

    target: str
    entries: list[str]
    used: int
    limit: int

    @property
    def counter(self) -> str:
        """``1,840 / 2,200`` style counter."""
        return f"{self.used:,} / {self.limit:,}"

    def render(self) -> str:
        """Entries as bullet lines for the system prompt."""
        return "\n".join(f"- {e}" for e in self.entries) if self.entries else "(empty)"


class Memory:
    """The two memory files.

    Args:
        data_dir: Data directory (holds ``memory/`` and ``.lock``).
    """

    def __init__(self, data_dir: Path) -> None:
        self._dir = data_dir / "memory"
        self._lock_path = data_dir / ".lock"
        self._thread_lock = threading.Lock()
        self._dir.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def locked(self) -> Iterator[None]:
        """Exclusive cross-process lock (also shared with the skill store)."""
        with self._thread_lock:
            fd = os.open(self._lock_path, os.O_RDWR | os.O_CREAT, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                yield
            finally:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                finally:
                    os.close(fd)

    def _path(self, target: str) -> Path:
        if target not in FILES:
            raise MemoryError_(f"unknown target {target!r}; use 'memory' or 'user'")
        return self._dir / FILES[target]

    def _read(self, target: str) -> list[str]:
        path = self._path(target)
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        return [e.strip() for e in text.split(SEPARATOR) if e.strip()]

    def _write(self, target: str, entries: list[str]) -> None:
        path = self._path(target)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(SEPARATOR.join(entries), encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)

    @staticmethod
    def _size(entries: list[str]) -> int:
        return len(SEPARATOR.join(entries))

    def snapshot(self, target: str) -> Snapshot:
        """Read one file."""
        entries = self._read(target)
        return Snapshot(target, entries, self._size(entries), LIMITS[target])

    @staticmethod
    def _clean(content: str | None) -> str:
        if content is None or not content.strip():
            raise MemoryError_("content is required")
        return content.replace(SEPARATOR, "\n").replace("§", "-").strip()

    def _fit(self, target: str, entries: list[str], needed_for: str) -> None:
        used, limit = self._size(entries), LIMITS[target]
        if used > limit:
            raise MemoryError_(
                f"{target} memory is full: this change needs {used:,} of {limit:,} characters "
                f"({needed_for}). Condense or remove entries first (replace or remove), then try again."
            )

    @staticmethod
    def _find(entries: list[str], match: str | None) -> int:
        if match is None or not match.strip():
            raise MemoryError_("'match' is required: a unique part of the entry")
        hits = [i for i, e in enumerate(entries) if match.strip().lower() in e.lower()]
        if not hits:
            raise MemoryError_(f"no entry contains {match.strip()!r}")
        if len(hits) > 1:
            raise MemoryError_(
                f"{len(hits)} entries contain {match.strip()!r}; use a longer, unique part of the entry"
            )
        return hits[0]

    def add(self, target: str, content: str | None) -> str:
        """Append an entry.

        Returns:
            The stored entry.

        Raises:
            MemoryError_: If the content is empty or the file would exceed its limit.
        """
        entry = self._clean(content)
        with self.locked():
            entries = self._read(target)
            if entry in entries:
                return entry
            entries.append(entry)
            self._fit(target, entries, "add")
            self._write(target, entries)
        return entry

    def replace(self, target: str, match: str | None, content: str | None) -> tuple[str, str]:
        """Replace the single entry containing ``match``.

        Returns:
            ``(old entry, new entry)``.
        """
        entry = self._clean(content)
        with self.locked():
            entries = self._read(target)
            index = self._find(entries, match)
            old = entries[index]
            entries[index] = entry
            self._fit(target, entries, "replace")
            self._write(target, entries)
        return old, entry

    def remove(self, target: str, match: str | None) -> str:
        """Remove the single entry containing ``match``; returns it."""
        with self.locked():
            entries = self._read(target)
            index = self._find(entries, match)
            old = entries.pop(index)
            self._write(target, entries)
        return old

    def remove_exact(self, target: str, entry: str) -> bool:
        """Remove an entry by exact text (used to undo a journal entry)."""
        with self.locked():
            entries = self._read(target)
            if entry not in entries:
                return False
            entries.remove(entry)
            self._write(target, entries)
        return True
