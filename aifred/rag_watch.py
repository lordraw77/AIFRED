"""A folder watcher for the knowledge folder (spec 9.4, "Folder scan"), built on Linux ``inotify`` through ``ctypes``.

The periodic scan stays as the fallback (``RAG_SCAN_MINUTES``). With the watcher on, a file written, moved in, moved
out or deleted under ``data/workspace/knowledge/<collection>/`` queues a scan of that collection a moment later, so
a burst of writes (a copy in progress) causes one scan, not hundreds. The scan itself decides what to read, so the
watcher adds no path of its own to the knowledge base: it only says *when* to look.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging
import os
import select
import struct
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)

IN_MODIFY = 0x002
IN_MOVED_FROM = 0x040
IN_MOVED_TO = 0x080
IN_CREATE = 0x100
IN_DELETE = 0x200
IN_DELETE_SELF = 0x400
IN_MOVE_SELF = 0x800
IN_CLOSE_WRITE = 0x008
IN_ISDIR = 0x40000000
IN_Q_OVERFLOW = 0x4000
IN_NONBLOCK = 0o4000
IN_CLOEXEC = 0o2000000
MASK = IN_CLOSE_WRITE | IN_MOVED_FROM | IN_MOVED_TO | IN_CREATE | IN_DELETE | IN_DELETE_SELF | IN_MOVE_SELF
EVENT = struct.Struct("iIII")
MAX_WATCHES = 4096
FULL = ""        # the key of "scan everything" in the pending set


def available() -> bool:
    """Whether inotify can be used here (Linux with a C library that has it)."""
    return sys.platform.startswith("linux") and ctypes.util.find_library("c") is not None


class FolderWatcher:
    """Watches a folder tree and calls ``on_change(collection)`` once it has been quiet for ``settle`` seconds.

    Args:
        folder: The knowledge folder.
        on_change: Called with a collection name, or ``None`` to rescan everything.
        settle: Seconds without events before the callback runs.
    """

    def __init__(self, folder: Path, on_change: Callable[[str | None], None], settle: float = 2.0) -> None:
        self._folder = folder
        self._on_change = on_change
        self._settle = settle
        self._fd = -1
        self._libc: ctypes.CDLL | None = None
        self._paths: dict[int, Path] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._pending: dict[str, float] = {}

    @property
    def running(self) -> bool:
        """Whether the watcher thread is alive."""
        return self._thread is not None and self._thread.is_alive()

    @property
    def watches(self) -> int:
        """Folders being watched."""
        return len(self._paths)

    def start(self) -> bool:
        """Open inotify and start the thread; returns False (and logs why) when the watcher cannot run."""
        if self.running:
            return True
        if not available():
            logger.info("Knowledge folder watcher: inotify is not available here; use RAG_SCAN_MINUTES")
            return False
        try:
            libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
            fd = libc.inotify_init1(IN_NONBLOCK | IN_CLOEXEC)
        except (OSError, AttributeError) as exc:
            logger.warning("Knowledge folder watcher: inotify failed (%s)", exc)
            return False
        if fd < 0:
            logger.warning("Knowledge folder watcher: inotify_init1 failed (%s)", os.strerror(ctypes.get_errno()))
            return False
        self._libc, self._fd = libc, fd
        self._stop.clear()
        self._add_tree(self._folder)
        self._thread = threading.Thread(target=self._run, name="rag-watch", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        """Stop the thread and close inotify."""
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        if self._fd >= 0:
            os.close(self._fd)
        self._fd, self._paths = -1, {}

    def _add(self, path: Path) -> bool:
        if self._libc is None or len(self._paths) >= MAX_WATCHES:
            return False
        wd = self._libc.inotify_add_watch(self._fd, os.fsencode(path), MASK)
        if wd < 0:
            return False
        self._paths[wd] = path
        return True

    def _add_tree(self, root: Path) -> None:
        """Watch ``root`` and every real (non-symlink, non-hidden) sub-folder; a missing root is retried later."""
        if not root.is_dir():
            return
        self._add(root)
        for current, dirs, _ in os.walk(root):
            dirs[:] = [d for d in dirs if not d.startswith(".") and not (Path(current) / d).is_symlink()]
            for d in dirs:
                if not self._add(Path(current) / d):
                    logger.warning("Knowledge folder watcher: too many folders or no watches left (fs.inotify.max_user_watches)")
                    return

    def _collection(self, wd: int, name: str) -> str:
        """The collection an event belongs to, or ``FULL`` when the whole tree must be scanned again."""
        base = self._paths.get(wd)
        if base is None:
            return FULL
        try:
            relative = (base / name).relative_to(self._folder)
        except ValueError:
            return FULL
        if base == self._folder:
            return relative.parts[0] if relative.parts else FULL
        return relative.parts[0]

    def _handle(self, data: bytes) -> None:
        offset = 0
        while offset + EVENT.size <= len(data):
            wd, mask, _cookie, length = EVENT.unpack_from(data, offset)
            name = data[offset + EVENT.size : offset + EVENT.size + length].split(b"\0", 1)[0].decode(errors="replace")
            offset += EVENT.size + length
            if mask & IN_Q_OVERFLOW:
                self._pending[FULL] = time.monotonic()
                continue
            if name.startswith("."):
                continue
            is_dir = bool(mask & IN_ISDIR)
            if is_dir and mask & (IN_CREATE | IN_MOVED_TO) and wd in self._paths:
                self._add_tree(self._paths[wd] / name)
            if mask & (IN_DELETE_SELF | IN_MOVE_SELF):
                self._paths.pop(wd, None)
                self._pending[FULL] = time.monotonic()
                continue
            top_level_dir = is_dir and self._paths.get(wd) == self._folder
            self._pending[FULL if top_level_dir and mask & (IN_DELETE | IN_MOVED_FROM) else self._collection(wd, name)] = time.monotonic()

    def _run(self) -> None:
        while not self._stop.is_set():
            ready, _, _ = select.select([self._fd], [], [], 0.5)
            if ready:
                try:
                    self._handle(os.read(self._fd, 65536))
                except BlockingIOError:
                    pass
                except OSError:
                    logger.exception("Knowledge folder watcher stopped")
                    return
            self._flush()

    def _flush(self) -> None:
        now = time.monotonic()
        due = [key for key, at in self._pending.items() if now - at >= self._settle]
        if not due:
            return
        if FULL in due:
            due = [FULL]
        for key in due:
            self._pending.pop(key, None)
        if FULL in due:
            self._pending.clear()
        if not self._folder.is_dir():
            return
        if self._fd >= 0 and not self._paths:
            self._add_tree(self._folder)
        for key in due:
            try:
                self._on_change(None if key == FULL else key)
            except Exception:  # noqa: BLE001
                logger.exception("Knowledge folder watcher: scan failed")
