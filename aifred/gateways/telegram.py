"""Telegram gateway: long polling over ``httpx``, no SDK (spec 10.2).

* **Allowlist.** With ``TELEGRAM_ALLOWED_IDS`` set, anyone else is told their numeric id so the admin can add them.
  With an empty list the bot is open: those users run with the restricted capability set and a rate limit.
* **Private chats only.** Groups and channels are ignored; a group message could reach people the admin never meant to serve.
* **The token is never logged.** It is part of every request URL, so error text is scrubbed before it is written anywhere.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

import httpx

from aifred.agent import DoneEvent, ErrorEvent, NoticeEvent
from aifred.commands import Commands, Context
from aifred.tgformat import to_html
from aifred.vault import VaultUnavailable

if TYPE_CHECKING:
    from aifred.app import Services

logger = logging.getLogger(__name__)

MESSAGE_LIMIT = 4000
MAX_INPUT_CHARS = 8000
TYPING_EVERY = 4.0
OPEN_BOT_RATE = 20
RATE_WINDOW = 60.0
POLL_TIMEOUT = 50
BACKOFF_START, BACKOFF_MAX = 1.0, 30.0
CONFLICT_WAIT = 15.0
OFFSET_KEY = "telegram:offset"


class TelegramError(Exception):
    """A Telegram API call failed.

    Attributes:
        status: HTTP status (0 for a network failure).
        retry_after: Seconds Telegram asked us to wait.
    """

    def __init__(self, message: str, status: int = 0, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


def split_message(text: str, limit: int = MESSAGE_LIMIT) -> list[str]:
    """Split a reply into pieces of at most ``limit`` characters, preferring paragraph, line and word boundaries."""
    text = text.strip() or "(no answer)"
    parts: list[str] = []
    while len(text) > limit:
        cut = max(text.rfind("\n\n", 0, limit), text.rfind("\n", 0, limit), text.rfind(" ", 0, limit))
        cut = cut if cut > limit // 2 else limit
        parts.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    parts.append(text)
    return parts


def _formatted(chat_id: int, piece: str) -> dict[str, Any]:
    """The sendMessage body for a piece of Markdown: HTML when it fits Telegram's limit, else plain text."""
    rendered = to_html(piece)
    if len(rendered) > 4096:
        return {"chat_id": chat_id, "text": piece}
    return {"chat_id": chat_id, "text": rendered, "parse_mode": "HTML", "link_preview_options": {"is_disabled": True}}


async def notify(services: Services, chat_id: int, text: str) -> None:
    """Send a message on the admin's behalf (scheduled tasks), independent of the polling loop.

    Only chats on ``TELEGRAM_ALLOWED_IDS`` are served, so an open bot can never be used to message strangers.

    Raises:
        TelegramError: If the chat is not allowed, no token is set or Telegram refused the message.
    """
    cfg = services.config
    if chat_id not in cfg.get("TELEGRAM_ALLOWED_IDS"):
        raise TelegramError(f"chat {chat_id} is not in TELEGRAM_ALLOWED_IDS")
    try:
        token = await asyncio.to_thread(cfg.get, "TELEGRAM_TOKEN")
    except VaultUnavailable as exc:
        raise TelegramError(f"the secret store is unavailable ({exc})") from None
    if not token:
        raise TelegramError("no Telegram token is configured")
    base = str(cfg.get("TELEGRAM_API_BASE")).rstrip("/")
    async with httpx.AsyncClient(timeout=30) as client:
        for piece in split_message(text):
            try:
                response = await client.post(f"{base}/bot{token}/sendMessage", json=_formatted(chat_id, piece))
                if response.status_code == 400:   # Telegram could not parse the markup: send the text as it is
                    response = await client.post(f"{base}/bot{token}/sendMessage", json={"chat_id": chat_id, "text": piece})
            except httpx.HTTPError as exc:
                raise TelegramError(f"{type(exc).__name__}: {exc}".replace(token, "***")[:200]) from None
            if response.status_code != 200:
                raise TelegramError(f"Telegram answered HTTP {response.status_code}")


class TelegramGateway:
    """Polls Telegram and answers messages through the agent.

    Args:
        services: The process services.
        poll_timeout: Long-poll timeout in seconds (shortened in tests).
        clock: Epoch clock (replaceable in tests).
        sleep: Async sleep for backoff (replaceable in tests).
    """

    def __init__(
        self, services: Services, *, poll_timeout: int = POLL_TIMEOUT, clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._s = services
        self._poll_timeout = poll_timeout
        self._clock = clock
        self._sleep = sleep
        self._commands = Commands(services)
        self._task: asyncio.Task[None] | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._token = ""
        self._base = ""
        self._client: httpx.AsyncClient | None = None
        self._recent: dict[int, deque[float]] = defaultdict(deque)
        self._ignored_chats: set[int] = set()
        self.state = "stopped"
        self.bot = ""
        self.last_error = ""
        self.last_update = 0.0

    # Lifecycle

    @property
    def running(self) -> bool:
        """Whether the polling task is alive."""
        return self._task is not None and not self._task.done()

    def status(self) -> dict[str, Any]:
        """State for the dashboard (never contains the token)."""
        allowed = self._s.config.get("TELEGRAM_ALLOWED_IDS")
        return {"state": self.state, "bot": self.bot, "open": not allowed, "last_error": self.last_error, "last_update": self.last_update}

    async def start(self) -> None:
        """Start polling if a token is configured (does nothing if already running)."""
        if self.running:
            return
        self.state, self.last_error = "starting", ""
        self._task = asyncio.create_task(self._run(), name="telegram")

    async def stop(self) -> None:
        """Stop polling and cancel the messages in flight."""
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self.state = "stopped"

    async def restart(self) -> None:
        """Apply new settings (token, allowlist, API base) without restarting the process."""
        await self.stop()
        await self.start()

    # Telegram API

    def _scrub(self, text: str) -> str:
        return text.replace(self._token, "***") if self._token else text

    async def _call(self, method: str, payload: dict[str, Any] | None = None, *, timeout: float | None = None) -> Any:
        assert self._client is not None
        try:
            response = await self._client.post(f"{self._base}/bot{self._token}/{method}", json=payload or {}, timeout=timeout)
        except httpx.HTTPError as exc:
            raise TelegramError(self._scrub(f"{type(exc).__name__}: {exc}")[:200]) from None
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.status_code != 200 or not body.get("ok"):
            retry = (body.get("parameters") or {}).get("retry_after")
            raise TelegramError(self._scrub(str(body.get("description") or f"HTTP {response.status_code}"))[:200], response.status_code, retry)
        return body.get("result")

    async def _send(self, chat_id: int, text: str) -> None:
        for piece in split_message(text):
            try:
                try:
                    await self._call("sendMessage", _formatted(chat_id, piece))
                except TelegramError as exc:
                    if exc.status != 400:
                        raise
                    await self._call("sendMessage", {"chat_id": chat_id, "text": piece})   # the markup was refused: plain text
            except TelegramError as exc:
                logger.warning("Could not send a Telegram message: %s", exc)
                return

    # Polling

    async def _run(self) -> None:
        cfg = self._s.config
        wait = BACKOFF_START
        while True:
            try:
                token = await asyncio.to_thread(cfg.get, "TELEGRAM_TOKEN")
            except VaultUnavailable as exc:
                self.state, self.last_error = "error", f"the secret store is unavailable ({exc})"
                await self._sleep(30)
                continue
            if not token:
                self.state, self.last_error = "stopped", "no token configured"
                return
            self._token, self._base = token, str(cfg.get("TELEGRAM_API_BASE")).rstrip("/")
            break
        if not cfg.get("TELEGRAM_ALLOWED_IDS"):
            logger.warning("The Telegram bot is open to everyone (TELEGRAM_ALLOWED_IDS is empty): users get a restricted set of capabilities and a rate limit")
        async with httpx.AsyncClient(timeout=self._poll_timeout + 15) as client:
            self._client = client
            try:
                me = await self._call("getMe")
            except TelegramError as exc:
                self.state, self.last_error = "error", f"cannot reach Telegram or the token is wrong: {exc}"
                logger.error("Telegram: %s", self.last_error)
                if exc.status in (401, 404):
                    return
            else:
                self.bot = str(me.get("username", ""))
                self.state = "running"  # the token works: do not wait for the first long poll (up to 50 s) to say so
                logger.info("Telegram bot @%s is polling", self.bot)
            offset = int(await asyncio.to_thread(self._s.store.kv_get, OFFSET_KEY) or 0)
            while True:
                try:
                    updates = await self._call("getUpdates", {"offset": offset, "timeout": self._poll_timeout, "allowed_updates": ["message"]})
                except TelegramError as exc:
                    self.state, self.last_error = "error", str(exc)
                    if exc.status in (401, 404):
                        logger.error("Telegram rejected the token; polling stops until it is changed")
                        return
                    delay = CONFLICT_WAIT if exc.status == 409 else (exc.retry_after or wait)
                    if exc.status == 409:
                        logger.warning("Telegram reports another poller for this bot; waiting")
                    wait = min(wait * 2, BACKOFF_MAX)
                    await self._sleep(delay)
                    continue
                wait = BACKOFF_START
                self.state, self.last_error = "running", ""
                for update in updates:
                    offset = max(offset, int(update["update_id"]) + 1)
                    task = asyncio.create_task(self._safely(update))
                    self._tasks.add(task)
                    task.add_done_callback(self._tasks.discard)
                if updates:
                    self.last_update = self._clock()
                    await asyncio.to_thread(self._s.store.kv_set, OFFSET_KEY, str(offset))

    async def _safely(self, update: dict[str, Any]) -> None:
        try:
            await self.handle_update(update)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - one bad message must not stop the bot
            logger.exception("Error while handling a Telegram update")

    # Messages

    def _limited(self, user_id: int) -> bool:
        """Open-bot rate limit: ``OPEN_BOT_RATE`` messages per minute and user."""
        now, window = self._clock(), self._recent[user_id]
        while window and window[0] <= now - RATE_WINDOW:
            window.popleft()
        if len(window) >= OPEN_BOT_RATE:
            return True
        window.append(now)
        return False

    async def _session_for(self, chat_id: int) -> str:
        key = f"tg:chat:{chat_id}"
        existing = await asyncio.to_thread(self._s.store.kv_get, key)
        if existing:
            return existing
        session = f"tg:{chat_id}:{int(self._clock())}"
        await asyncio.to_thread(self._s.store.kv_set, key, session)
        return session

    async def handle_update(self, update: dict[str, Any]) -> None:
        """Handle one update (public so tests and a future webhook can feed it)."""
        message = update.get("message")
        if not message:
            return
        chat, sender = message.get("chat") or {}, message.get("from") or {}
        chat_id, user_id = chat.get("id"), sender.get("id")
        if chat_id is None or user_id is None:
            return
        if chat.get("type") != "private":
            if chat_id not in self._ignored_chats:
                self._ignored_chats.add(chat_id)
                logger.info("Ignoring Telegram chat %s: only private chats are served", chat_id)
            return
        await asyncio.to_thread(self._s.config.reload)
        allowed = self._s.config.get("TELEGRAM_ALLOWED_IDS")
        if allowed and user_id not in allowed:
            await self._send(chat_id, f"You are not authorised to use this bot. Your Telegram id is {user_id}; ask the admin to add it.")
            return
        restricted = not allowed
        if restricted and self._limited(user_id):
            await self._send(chat_id, "You are sending messages too quickly. Please wait a minute.")
            return
        text = message.get("text")
        if not isinstance(text, str):
            await self._send(chat_id, "I only understand text messages for now.")
            return
        if len(text) > MAX_INPUT_CHARS:
            await self._send(chat_id, f"That message is too long (more than {MAX_INPUT_CHARS:,} characters).")
            return

        session = await self._session_for(chat_id)
        reply = await self._commands.run(text, Context(session, "telegram", restricted))
        if reply is not None:
            if reply.session_id:
                await asyncio.to_thread(self._s.store.kv_set, f"tg:chat:{chat_id}", reply.session_id)
            await self._send(chat_id, reply.text)
            return
        await self._answer(chat_id, session, text, restricted)

    async def _typing(self, chat_id: int) -> None:
        while True:
            try:
                await self._call("sendChatAction", {"chat_id": chat_id, "action": "typing"})
            except TelegramError:
                pass
            await asyncio.sleep(TYPING_EVERY)

    async def _answer(self, chat_id: int, session: str, text: str, restricted: bool) -> None:
        typing = asyncio.create_task(self._typing(chat_id))
        notices: list[str] = []
        final = ""
        try:
            async for event in self._s.agent.run(session, text, "telegram", restricted=restricted):
                if isinstance(event, DoneEvent):
                    final = event.content
                elif isinstance(event, ErrorEvent):
                    final = f"⚠ {event.message}"
                elif isinstance(event, NoticeEvent):
                    notices.append(event.text)
        finally:
            typing.cancel()
        await self._send(chat_id, final)
        for notice in notices:
            await self._send(chat_id, f"ℹ {notice}")
