"""Slash commands shared by the CLI, Telegram and the web chat (spec 10.1).

A gateway passes every message through :meth:`Commands.run`; text that is not a command comes back as
``None`` and goes to the agent. Open-gateway users (``restricted``) get a small safe subset.
"""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Awaitable, Callable

from aifred.rag import RagError
from aifred.router import RouteError
from aifred.writes import WriteError, apply_pending, describe_pending, reject_pending

if TYPE_CHECKING:
    from aifred.app import Services

SESSION_LIST = 10
RESTRICTED_COMMANDS = frozenset({"start", "help", "new", "think"})
_COMMAND_RE = re.compile(r"^/([A-Za-z][A-Za-z0-9_]*)(?:@\w+)?(?:\s+(.*))?$", re.DOTALL)


@dataclass(frozen=True)
class Reply:
    """What a command answers.

    Attributes:
        text: Plain text for the user.
        session_id: A session to switch to (``/new``).
        quit: Whether the CLI should exit.
    """

    text: str
    session_id: str | None = None
    quit: bool = False


@dataclass(frozen=True)
class Context:
    """Who is asking and where.

    Attributes:
        session_id: The current session.
        source: Gateway name (``cli``, ``telegram``, ``api``, ``web``).
        restricted: Whether the caller is an open-gateway user.
    """

    session_id: str
    source: str
    restricted: bool = False


def new_session_id(source: str, current: str) -> str:
    """A fresh session id for a gateway (Telegram keeps its chat id)."""
    epoch = int(time.time())
    if source == "telegram":
        parts = current.split(":")
        return f"tg:{parts[1] if len(parts) > 1 else '0'}:{epoch}"
    if source == "web":
        return f"web:{uuid.uuid4()}"
    return f"cli:{epoch}"


def _age(seconds: float) -> str:
    s = max(0, int(seconds))
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min"
    if s < 86400:
        return f"{s // 3600} h"
    return f"{s // 86400} d"


class Commands:
    """Parses and runs slash commands.

    Args:
        services: The process services.
    """

    def __init__(self, services: Services) -> None:
        self._s = services
        self._table: dict[str, Callable[[str, Context], Awaitable[str | Reply]]] = {
            "start": self._start, "help": self._help, "new": self._new, "sessions": self._sessions, "think": self._think,
            "model": self._model, "kb": self._kb, "pending": self._pending, "approve": self._approve, "reject": self._reject,
            "mcp": self._mcp, "quit": self._quit,
        }

    @staticmethod
    def is_command(text: str) -> bool:
        """Whether a message starts with a slash command."""
        return _COMMAND_RE.match(text.strip()) is not None

    def available(self, ctx: Context) -> list[str]:
        """Command names this caller may use."""
        names = [n for n in self._table if not ctx.restricted or n in RESTRICTED_COMMANDS]
        return [n for n in names if n != "quit" or ctx.source == "cli"]

    async def run(self, text: str, ctx: Context) -> Reply | None:
        """Run a command, or return ``None`` if the text is not one."""
        match = _COMMAND_RE.match(text.strip())
        if not match:
            return None
        name, args = match.group(1).lower(), (match.group(2) or "").strip()
        if name not in self._table:
            return Reply(f"Unknown command /{name}. Type /help for the list.")
        if name not in self.available(ctx):
            return Reply(f"/{name} is not available here.")
        result = await self._table[name](args, ctx)
        return result if isinstance(result, Reply) else Reply(result)

    # Commands

    async def _start(self, args: str, ctx: Context) -> str:
        return "Hello, I'm AIFRED. Send me a message, or type /help to see what I can do."

    async def _help(self, args: str, ctx: Context) -> str:
        info = {
            "start": "greeting", "help": "this list", "new": "start a new conversation",
            "sessions": "recent conversations", "think": "[on|off|auto] reasoning for this conversation",
            "model": "[route|provider/model|auto] show or choose the model route", "kb": "[scan|search <query>] the knowledge base",
            "mcp": "[reload] MCP servers and their state", "pending": "writes waiting for your approval", "approve": "<id> apply a pending write", "reject": "<id> discard one",
            "quit": "leave the CLI",
        }
        return "Commands:\n" + "\n".join(f"/{n} - {info[n]}" for n in self.available(ctx))

    async def _new(self, args: str, ctx: Context) -> Reply:
        self._s.agent.reset_prompt(ctx.session_id)
        return Reply("Started a new conversation.", session_id=new_session_id(ctx.source, ctx.session_id))

    async def _quit(self, args: str, ctx: Context) -> Reply:
        return Reply("Bye.", quit=True)

    async def _sessions(self, args: str, ctx: Context) -> str:
        rows = await asyncio.to_thread(self._s.store.list_sessions, None, SESSION_LIST)
        if not rows:
            return "No conversations yet."
        now = time.time()
        return "\n".join(
            f"{'*' if r['id'] == ctx.session_id else ' '} {r['id']}  {r['title'] or '(untitled)'}  "
            f"[{r['message_count']} messages, {_age(now - r['updated'])} ago]"
            for r in rows
        )

    async def _think(self, args: str, ctx: Context) -> str:
        agent, default = self._s.agent, self._s.config.get("LLM_THINKING") == "on"
        arg = args.lower()
        if arg in ("on", "off"):
            agent.set_thinking(ctx.session_id, arg == "on")
            return f"Reasoning is {arg} for this conversation."
        if arg == "auto":
            agent.set_thinking(ctx.session_id, None)
            return f"Reasoning follows the default ({'on' if default else 'off'})."
        if arg:
            return "Usage: /think on, /think off or /think auto."
        current = agent.thinking(ctx.session_id)
        if current is None:
            return f"Reasoning follows the default ({'on' if default else 'off'}). Use /think on or /think off."
        return f"Reasoning is {'on' if current else 'off'} for this conversation (default {'on' if default else 'off'})."

    async def _model(self, args: str, ctx: Context) -> str:
        s, agent = self._s, self._s.agent
        if args.lower() == "auto":
            agent.set_route(ctx.session_id, None)
            args = ""
        elif args:
            try:
                await asyncio.to_thread(s.llm.resolve, args, "default")
            except RouteError as exc:
                return f"{exc}. Routes: {', '.join(await asyncio.to_thread(s.store.route_names))}."
            agent.set_route(ctx.session_id, args)
            return f"This conversation now uses {args}" + (" (no fallback)." if "/" in args else ".")
        spec = agent.route_spec(ctx.session_id, ctx.source)
        pinned = s.store.kv_get(f"route:{ctx.session_id}") is not None
        try:
            entries = await asyncio.to_thread(s.llm.resolve, spec, "default")
        except RouteError as exc:
            return str(exc)
        lines = [f"Route: {spec} ({'chosen for this conversation' if pinned else 'default for ' + ctx.source})"]
        lines += [f" {i}. {e.label}" for i, e in enumerate(entries, start=1)]
        routes = await asyncio.to_thread(s.store.route_names)
        return "\n".join([*lines, f"Routes: {', '.join(routes)}. Use /model <route>, /model <provider>/<model> or /model auto."])

    async def _kb(self, args: str, ctx: Context) -> str:
        rag = self._s.rag
        if not rag.enabled:
            return "The knowledge base is off. Turn on RAG_ENABLED on the Config page."
        sub, _, rest = args.partition(" ")
        try:
            if sub == "scan":
                summary = await asyncio.to_thread(rag.scan)
                skipped = f", {len(summary['skipped'])} skipped" if summary["skipped"] else ""
                return f"Scan: {summary['queued']} queued, {summary['unchanged']} unchanged, {summary['removed']} removed{skipped}."
            if sub == "search":
                if not rest.strip():
                    return "Usage: /kb search <query>"
                return (await asyncio.to_thread(rag.search, rest.strip())).render()
        except RagError as exc:
            return f"Error: {exc}"
        if sub:
            return "Usage: /kb, /kb scan or /kb search <query>"
        rows = await asyncio.to_thread(rag.collections)
        if not rows:
            return "No collections yet. Put files in data/workspace/knowledge/<collection>/ and run /kb scan."
        return "\n".join(
            f"- {r['name']}: {r['documents']} documents, {r['chunk_count']} passages"
            + (f", {r['pending']} being indexed" if r["pending"] else "") + (f", {r['errors']} failed" if r["errors"] else "")
            + (", re-index needed" if r["stale"] else "")
            for r in rows
        )

    async def _mcp(self, args: str, ctx: Context) -> str:
        manager = self._s.mcp_manager
        if args.lower() == "reload":
            await asyncio.to_thread(manager.reload)
            await asyncio.to_thread(manager.wait_ready, 5.0)
        elif args:
            return "Usage: /mcp or /mcp reload"
        rows = manager.status()
        if not rows:
            return "No MCP servers. Add them on the MCP page of the dashboard."
        return "\n".join(
            f"- {r['name']}: {r['state']}" + (f", {r['tools']} tools, protocol {r['protocol']}" if r["state"] == "connected" else "")
            + (f" ({r['error']})" if r["error"] else "") + (" [TLS not verified]" if r["tls_unverified"] else "")
            for r in rows
        )

    async def _pending(self, args: str, ctx: Context) -> str:
        rows = await asyncio.to_thread(self._s.store.pending_list)
        if not rows:
            return "Nothing is waiting for approval."
        return "Waiting for approval:\n" + "\n".join(describe_pending(r) for r in rows) + "\nUse /approve <id> or /reject <id>."

    async def _decide(self, args: str, approve: bool) -> str:
        if not args.strip().lstrip("#").isdigit():
            return f"Usage: /{'approve' if approve else 'reject'} <id>"
        write_id = int(args.strip().lstrip("#"))
        try:
            if approve:
                return await asyncio.to_thread(apply_pending, self._s, write_id)
            await asyncio.to_thread(reject_pending, self._s, write_id)
            return f"Discarded #{write_id}."
        except WriteError as exc:
            return f"Error: {exc}"

    async def _approve(self, args: str, ctx: Context) -> str:
        return await self._decide(args, True)

    async def _reject(self, args: str, ctx: Context) -> str:
        return await self._decide(args, False)
