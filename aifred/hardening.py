"""Process hardening for the service (Linux only; failures are logged, never fatal).

With the terminal toolset enabled, commands run as the same user as AIFRED. Marking the
process non-dumpable makes ``/proc/<pid>/environ`` and ``/proc/<pid>/mem`` unreadable to
its same-user children, so they cannot lift credentials (the OpenBao token) from the
service. The attribute is reset on ``exec``, so the commands themselves are unaffected.
"""

from __future__ import annotations

import ctypes
import logging
import os
import resource
import threading
from collections.abc import Iterator
from contextlib import contextmanager

logger = logging.getLogger(__name__)

PR_SET_DUMPABLE = 4


def harden_process() -> bool:
    """Make the current process non-dumpable and disable core dumps.

    Returns:
        Whether the dumpable flag was cleared.
    """
    try:
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except (ValueError, OSError):
        logger.warning("Could not disable core dumps")
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(PR_SET_DUMPABLE, 0, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), os.strerror(ctypes.get_errno()))
    except (OSError, AttributeError) as exc:
        logger.warning("Could not mark the process non-dumpable (%s)", exc)
        return False
    return True


# Orphan reaping ---------------------------------------------------------------------------------
#
# In a container the service runs as PID 1 (a separate init process would be dumpable and would hold
# the container's whole environment, credentials included, in /proc/1/environ). PID 1 must also reap
# orphaned children, such as background jobs left by terminal commands. The reaper below only collects
# children that nobody is waiting for: those a tool started and will wait() for itself are registered.

_lock = threading.Lock()
_tracked: set[int] = set()


@contextmanager
def spawn_guard() -> Iterator[None]:
    """Hold this while starting a child and registering it with :func:`track`.

    It keeps the reaper from collecting the child between its creation and its registration, which
    would make the owner's ``wait()`` report a wrong exit code.
    """
    with _lock:
        yield


def track(pid: int) -> None:
    """Register a child whose owner will wait for it (call inside :func:`spawn_guard`)."""
    _tracked.add(pid)


def untrack(pid: int) -> None:
    """Forget a child once its owner has waited for it."""
    with _lock:
        _tracked.discard(pid)


def reap_orphans() -> int:
    """Collect exited children that are not registered.

    Returns:
        How many were reaped.
    """
    reaped = 0
    with _lock:
        while True:
            try:
                info = os.waitid(os.P_ALL, 0, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            except ChildProcessError:
                break
            if info is None or not info.si_pid or info.si_pid in _tracked:
                break  # nothing exited, or the first one belongs to a waiting owner; look again later
            try:
                os.waitpid(info.si_pid, 0)
                reaped += 1
            except ChildProcessError:
                pass
    return reaped


def start_reaper(interval: float = 1.0) -> threading.Event:
    """Reap orphans in a background thread; set the returned event to stop it."""
    stop = threading.Event()

    def loop() -> None:
        while not stop.wait(interval):
            try:
                reap_orphans()
            except OSError:
                logger.exception("Orphan reaper failed")

    threading.Thread(target=loop, name="orphan-reaper", daemon=True).start()
    return stop
