"""Scheduled tasks: recurring prompts the agent runs on its own and delivers on Telegram (docs/specs.md section 14).

A task is a name, a schedule (cron, alias or ``every 30m``), a prompt and optionally a Telegram chat to deliver to.
Runs are unattended: they use the ``cron`` gateway, whose toolsets (``TOOLSETS_CRON``) are narrower than an interactive
chat's, the model cannot ask questions, and a run cannot create tasks itself. A task the model proposes in a chat waits
in the approval queue; it exists only once the admin approves it. Delivery goes only to chats on ``TELEGRAM_ALLOWED_IDS``.
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
import time
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

from aifred import cron
from aifred.agent import DoneEvent, ErrorEvent
from aifred.scanner import scan

if TYPE_CHECKING:
    from aifred.app import Services

logger = logging.getLogger(__name__)

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
PROMPT_LIMIT = 2000
TICK_SECONDS = 15.0
MISSED_GRACE = 15 * 60
"""A slot found overdue by more than this (the server was down) is recorded as missed instead of run late."""
KEEP_RUNS = 50
FRAME = (
    "[Scheduled task {name!r}, running unattended at {when}. Nobody is here to answer questions: do the task with your tools "
    "and write the message that will be delivered, nothing else.]\n\n{prompt}"
)
Deliver = Callable[[int, str], Awaitable[None]]


class SchedulerError(Exception):
    """A task could not be created, changed or run; the message is meant for the user."""


def chat_of_session(session_id: str) -> int | None:
    """The Telegram chat a ``tg:<chat>:…`` session belongs to."""
    parts = session_id.split(":")
    if len(parts) >= 2 and parts[0] == "tg" and parts[1].lstrip("-").isdigit():
        return int(parts[1])
    return None


class SchedulerService:
    """Task management and the worker that runs due tasks.

    Args:
        services: The process services.
        clock: Epoch clock, injectable for tests.
        deliver: Sends ``(chat_id, text)``; defaults to the Telegram bot.
        tick: Seconds between looks at the clock.
    """

    def __init__(self, services: Services, *, clock: Callable[[], float] = time.time, deliver: Deliver | None = None,
                 tick: float = TICK_SECONDS) -> None:
        self._s = services
        self._clock = clock
        self._deliver = deliver
        self._tick = tick
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._pool: ThreadPoolExecutor | None = None
        self._running: set[int] = set()
        self._guard = threading.Lock()
        services.config.subscribe(self._settings_changed)

    # Configuration

    def zone(self) -> Any:
        """The time zone of cron expressions (``None``: the server's); a bad setting is reported and ignored."""
        try:
            return cron.timezone(str(self._s.config.get("SCHEDULER_TIMEZONE")))
        except cron.CronError as exc:
            logger.warning("SCHEDULER_TIMEZONE ignored: %s", exc)
            return None

    def parse(self, expression: str) -> cron.Spec:
        """Parse an expression under the configured minimum gap.

        Raises:
            SchedulerError: If it is invalid.
        """
        try:
            return cron.parse(expression, int(self._s.config.get("SCHEDULER_MIN_INTERVAL")))
        except cron.CronError as exc:
            raise SchedulerError(str(exc)) from None

    def _next(self, spec: cron.Spec, after: float) -> float:
        try:
            return cron.next_run(spec, after, self.zone())
        except cron.CronError as exc:
            raise SchedulerError(str(exc)) from None

    def preview(self, expression: str, count: int = 3) -> list[str]:
        """The next run times of an expression, for confirmation."""
        spec = self.parse(expression)
        zone = self.zone()
        try:
            return [cron.describe(t, zone) for t in cron.upcoming(spec, count, self._clock(), zone)]
        except cron.CronError as exc:
            raise SchedulerError(str(exc)) from None

    def _settings_changed(self, changed: set[str]) -> None:
        if changed & {"SCHEDULER_TIMEZONE"}:
            self.reschedule_all()

    def reschedule_all(self) -> None:
        """Recompute every enabled task's next run (after a time zone change)."""
        now = self._clock()
        for row in self._s.store.schedule_list():
            if not row["enabled"]:
                continue
            try:
                self._s.store.schedule_update(row["name"], next_run=self._next(self.parse(row["expression"]), now))
            except SchedulerError as exc:
                logger.warning("Schedule %s cannot be rescheduled: %s", row["name"], exc)

    # Management

    def check_delivery(self, chat_id: int | None) -> None:
        """Only an allowlisted chat can receive scheduled messages.

        Raises:
            SchedulerError: Otherwise.
        """
        if chat_id is not None and chat_id not in self._s.config.get("TELEGRAM_ALLOWED_IDS"):
            raise SchedulerError(f"chat {chat_id} is not on TELEGRAM_ALLOWED_IDS, so messages cannot be delivered there")

    def validate(self, name: str, expression: str, prompt: str, deliver_chat: int | None) -> cron.Spec:
        """Check every field of a new task.

        Raises:
            SchedulerError: With what is wrong.
        """
        if not NAME_RE.match(name):
            raise SchedulerError("the name must be 1-40 characters: lowercase letters, digits, '-' and '_'")
        if not prompt.strip():
            raise SchedulerError("the prompt is empty")
        if len(prompt) > PROMPT_LIMIT:
            raise SchedulerError(f"the prompt is longer than {PROMPT_LIMIT} characters")
        self.check_delivery(deliver_chat)
        spec = self.parse(expression)
        self._next(spec, self._clock())
        return spec

    def screen(self, prompt: str) -> None:
        """Reject a prompt that trips the scanner (a task prompt is a standing instruction).

        Raises:
            SchedulerError: With the reasons.
        """
        if findings := scan(prompt):
            raise SchedulerError("rejected by the safety filter (" + "; ".join(str(f) for f in findings) + "). Rephrase the task as plain instructions.")

    def create(self, name: str, expression: str, prompt: str, deliver_chat: int | None = None, *, created_by: str = "user",
               enabled: bool = True) -> dict[str, Any]:
        """Create a task.

        Raises:
            SchedulerError: If a field is invalid, the name is taken or there are too many tasks.
        """
        name = name.strip().lower()
        prompt = prompt.strip()
        spec = self.validate(name, expression, prompt, deliver_chat)
        if self._s.store.schedule_count() >= int(self._s.config.get("SCHEDULER_MAX_TASKS")):
            raise SchedulerError(f"there are already {self._s.config.get('SCHEDULER_MAX_TASKS')} tasks (SCHEDULER_MAX_TASKS)")
        try:
            row = self._s.store.schedule_create(name, spec.text, prompt, deliver_chat, self._next(spec, self._clock()) if enabled else None,
                                                created_by, enabled)
        except ValueError as exc:
            raise SchedulerError(str(exc)) from None
        self._s.store.journal_add("schedule", "create", f"{name}: {spec.text}", None, "applied", {"name": name})
        return row

    def get(self, name: str) -> dict[str, Any]:
        """One task.

        Raises:
            SchedulerError: If it does not exist.
        """
        row = self._s.store.schedule_get(name)
        if row is None:
            raise SchedulerError(f"no task named {name!r}")
        return row

    def update(self, name: str, *, expression: str | None = None, prompt: str | None = None, deliver_chat: int | None | str = "keep") -> dict[str, Any]:
        """Change a task's schedule, prompt or delivery chat.

        Raises:
            SchedulerError: If the task does not exist or a value is invalid.
        """
        row = self.get(name)
        values: dict[str, Any] = {}
        if expression is not None:
            spec = self.parse(expression)
            values["expression"] = spec.text
            if row["enabled"]:
                values["next_run"] = self._next(spec, self._clock())
        if prompt is not None:
            prompt = prompt.strip()
            if not prompt or len(prompt) > PROMPT_LIMIT:
                raise SchedulerError(f"the prompt must have 1-{PROMPT_LIMIT} characters")
            values["prompt"] = prompt
        if deliver_chat != "keep":
            self.check_delivery(deliver_chat)  # type: ignore[arg-type]
            values["deliver_chat"] = deliver_chat
        self._s.store.schedule_update(name, **values)
        return self.get(name)

    def set_enabled(self, name: str, enabled: bool) -> dict[str, Any]:
        """Pause or resume a task (resuming picks the next slot from now)."""
        row = self.get(name)
        next_run = self._next(self.parse(row["expression"]), self._clock()) if enabled else None
        self._s.store.schedule_update(name, enabled=enabled, next_run=next_run)
        return self.get(name)

    def remove(self, name: str) -> None:
        """Delete a task and its history.

        Raises:
            SchedulerError: If it does not exist.
        """
        if not self._s.store.schedule_delete(name):
            raise SchedulerError(f"no task named {name!r}")
        self._s.store.journal_add("schedule", "delete", name, None, "applied", {"name": name})

    def describe(self, row: dict[str, Any]) -> dict[str, Any]:
        """A task with its next run in readable form, for the API and commands."""
        zone = self.zone()
        out = dict(row)
        out["next_run_text"] = cron.describe(row["next_run"], zone) if row["next_run"] else ""
        out["running"] = row["id"] in self._running
        return out

    # Running

    def run_now(self, name: str) -> None:
        """Run a task once, now, in the background (its regular schedule is not changed).

        Raises:
            SchedulerError: If it does not exist or is already running.
        """
        row = self.get(name)
        if not self._claim_running(row["id"]):
            raise SchedulerError(f"{name!r} is already running")
        self._submit(row)

    def _claim_running(self, schedule_id: int) -> bool:
        with self._guard:
            if schedule_id in self._running:
                return False
            self._running.add(schedule_id)
            return True

    def _submit(self, row: dict[str, Any]) -> None:
        if self._pool is None:
            with self._guard:
                if self._pool is None:
                    self._pool = ThreadPoolExecutor(max_workers=int(self._s.config.get("SCHEDULER_MAX_PARALLEL")), thread_name_prefix="schedule")
        self._pool.submit(self._job, row)

    def _job(self, row: dict[str, Any]) -> None:
        try:
            asyncio.run(self.execute(row))
        except Exception:  # noqa: BLE001 - a failing task must not kill the worker
            logger.exception("Scheduled task %s crashed", row["name"])
        finally:
            with self._guard:
                self._running.discard(row["id"])

    async def execute(self, row: dict[str, Any]) -> dict[str, Any]:
        """Run one task to completion, store the outcome and deliver it.

        Returns:
            The finished run row.
        """
        s = self._s
        started = self._clock()
        session_id = f"cron:{row['name']}:{int(started)}"
        run_id = await asyncio.to_thread(s.store.schedule_run_start, row["id"], session_id)
        status, output, error = "ok", "", ""
        when = cron.describe(started, self.zone())
        text = FRAME.format(name=row["name"], when=when, prompt=row["prompt"])
        try:
            output = await asyncio.wait_for(self._ask(session_id, text), timeout=float(s.config.get("SCHEDULER_RUN_TIMEOUT")))
        except TimeoutError:
            status, error = "error", f"it took longer than {s.config.get('SCHEDULER_RUN_TIMEOUT')} s (SCHEDULER_RUN_TIMEOUT)"
        except SchedulerError as exc:
            status, error = "error", str(exc)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Scheduled task %s failed", row["name"])
            status, error = "error", f"{type(exc).__name__}: {exc}"
        delivered = False
        if row["deliver_chat"] is not None:
            message = f"⏰ {row['name']}\n\n{output}" if status == "ok" else f"⚠️ {row['name']} failed: {error}"
            delivered = await self._send(row["deliver_chat"], message[:12000] if message.strip() else f"⏰ {row['name']}: (no output)")
        await asyncio.to_thread(s.store.schedule_run_finish, run_id, status, output, error, delivered, KEEP_RUNS)
        runs = await asyncio.to_thread(s.store.schedule_runs, row["name"], 1)
        return runs[0] if runs else {}

    async def _ask(self, session_id: str, text: str) -> str:
        final = ""
        async for event in self._s.agent.run(session_id, text, "cron"):
            if isinstance(event, DoneEvent):
                final = event.content
            elif isinstance(event, ErrorEvent):
                raise SchedulerError(event.message)
        if not final.strip():
            raise SchedulerError("the model produced no answer")
        return final

    async def _send(self, chat_id: int, text: str) -> bool:
        try:
            if self._deliver is not None:
                await self._deliver(chat_id, text)
            else:
                from aifred.gateways.telegram import notify

                await notify(self._s, chat_id, text)
        except Exception as exc:  # noqa: BLE001 - delivery problems are shown on the run, never raised
            logger.warning("Could not deliver a scheduled message: %s", exc)
            return False
        return True

    # Worker

    def start(self) -> None:
        """Start the clock thread (idempotent); runs interrupted by a restart are marked failed."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._s.store.schedule_interrupted()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the clock thread and wait for running tasks."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)
            self._pool = None

    @property
    def running(self) -> bool:
        """Whether the clock thread is alive."""
        return self._thread is not None and self._thread.is_alive()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:  # noqa: BLE001
                logger.exception("Scheduler tick failed")
            self._stop.wait(self._tick)

    def tick(self) -> int:
        """Start every task whose time has come; returns how many were started."""
        if not self._s.config.get("SCHEDULER_ENABLED"):
            return 0
        now = self._clock()
        started = 0
        for row in self._s.store.schedule_due(now):
            try:
                spec = self.parse(row["expression"])
                following = self._next(spec, now)
            except SchedulerError as exc:  # e.g. the minimum gap was raised since: pause instead of looping on it
                logger.warning("Schedule %s paused: %s", row["name"], exc)
                self._s.store.schedule_update(row["name"], enabled=False, next_run=None)
                continue
            if not self._s.store.schedule_claim(row["id"], row["next_run"], following):
                continue
            if now - row["next_run"] > MISSED_GRACE:
                self._record_missed(row)
                continue
            if not self._claim_running(row["id"]):
                logger.info("Schedule %s is still running; this slot is skipped", row["name"])
                continue
            self._submit(row)
            started += 1
        return started

    def _record_missed(self, row: dict[str, Any]) -> None:
        run_id = self._s.store.schedule_run_start(row["id"], "")
        self._s.store.schedule_run_finish(run_id, "missed", error="the server was not running at the scheduled time", keep=KEEP_RUNS)

    def wait(self, timeout: float = 30.0) -> None:
        """Wait for running tasks (tests)."""
        end = time.time() + timeout
        while self._running and time.time() < end:
            time.sleep(0.02)
