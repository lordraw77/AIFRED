"""The last log lines of the process, kept in memory for the dashboard's Logs page (spec 10.5)."""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Any

CAPACITY = 500


class LogBuffer(logging.Handler):
    """A ring buffer of formatted log records with a rising sequence number.

    Args:
        capacity: How many lines to keep.
    """

    def __init__(self, capacity: int = CAPACITY) -> None:
        super().__init__(level=logging.DEBUG)
        self._lines: deque[dict[str, Any]] = deque(maxlen=capacity)
        self._seq = 0
        self._guard = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
            if record.exc_info and record.exc_info[1] is not None:
                message += f" [{type(record.exc_info[1]).__name__}: {record.exc_info[1]}]"
        except Exception:  # noqa: BLE001 - a broken log call must never break the caller
            return
        with self._guard:
            self._seq += 1
            self._lines.append({"seq": self._seq, "ts": record.created or time.time(), "level": record.levelname, "logger": record.name, "message": message})

    def lines(self, since: int = 0, level: str = "DEBUG", limit: int = CAPACITY) -> list[dict[str, Any]]:
        """Lines newer than ``since`` at or above ``level``, oldest first (the last ``limit`` of them)."""
        floor = logging.getLevelName(level.upper())
        floor = floor if isinstance(floor, int) else logging.DEBUG
        with self._guard:
            rows = [r for r in self._lines if r["seq"] > since and logging.getLevelName(r["level"]) >= floor]
        return rows[-limit:]


BUFFER = LogBuffer()


def install() -> LogBuffer:
    """Attach the shared buffer to the root logger (once)."""
    root = logging.getLogger()
    if BUFFER not in root.handlers:
        root.addHandler(BUFFER)
    return BUFFER
