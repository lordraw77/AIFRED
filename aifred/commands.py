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
from aifred.instructions import InstructionError, first_sentence
from aifred.mcp import McpConfigError
from aifred.agents import AgentError
from aifred.scheduler import SchedulerError, chat_of_session
from aifred.tools.registry import ToolContext
from aifred.writes import undo_entry, WriteError, apply_pending, describe_pending, reject_pending

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
            "mcp": self._mcp, "refine": self._refine, "journey": self._journey, "instructions": self._instructions, "assist": self._assist, "cron": self._cron, "agent": self._agent, "agents": self._agents, "quit": self._quit,
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
            "mcp": "[reload] MCP servers and their state", "refine": "review this conversation now and save what is worth keeping",
            "journey": "[delete <id>] what I learned; undo an entry",
            "instructions": "[reload|<name>] what each tool group is for", "assist": "<mcp:name|toolset:name> [hint] draft or improve its instruction and score it (saves nothing)",
            "agent": "<name> <task> hand a task to a sub-agent", "agents": "the sub-agents you can use",
            "cron": "[add <name> | <schedule> | <prompt>|pause|resume|run|remove <name>] scheduled tasks", "pending": "writes waiting for your approval", "approve": "<id> apply a pending write", "reject": "<id> discard one",
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
                return f"{exc}. Routes: {', '.join(await self._chat_routes())}."
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
        routes = await self._chat_routes()
        return "\n".join([*lines, f"Routes: {', '.join(routes)}. Use /model <route>, /model <provider>/<model> or /model auto."])

    async def _chat_routes(self) -> list[str]:
        """Route names a conversation can use (the vision route is reserved for reading images)."""
        names = await asyncio.to_thread(self._s.store.route_names)
        return [n for n in names if n != self._s.config.get("RAG_VISION_ROUTE")]

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
        elif args.lower().startswith("add"):
            return await self._mcp_add(args[3:].strip())
        elif args:
            return "Usage: /mcp, /mcp reload or /mcp add <name> <url> <what it is for>"
        rows = manager.status()
        if not rows:
            return "No MCP servers. Add them on the MCP page of the dashboard."
        return "\n".join(
            f"- {r['name']}: {r['state']}" + (f", {r['tools']} tools, protocol {r['protocol']}" if r["state"] == "connected" else "")
            + (f" ({r['error']})" if r["error"] else "") + (" [TLS not verified]" if r["tls_unverified"] else "")
            for r in rows
        )

    async def _mcp_add(self, args: str) -> str:
        parts = args.split(None, 2)
        if len(parts) < 3:
            return "Usage: /mcp add <name> <url> <what it is for>. Say what the server is for and when to use it; it is required."
        name, url, text = parts[0], parts[1], parts[2].strip()
        s = self._s
        try:
            s.instructions.check(f"mcp:{name}", text)
            server, headers = s.mcp.preview(name, {"url": url})
            if s.mcp.get(name):
                return f"There is already a server called {name!r}; change it on the MCP page."
            result = await asyncio.to_thread(s.mcp_manager.test, server, headers)
            if not result["ok"]:
                return f"Error: connection test failed: {result['error']}"
            s.mcp.save(name, {"url": url})
            s.instructions.set(f"mcp:{name}", text)
        except (McpConfigError, InstructionError) as exc:
            return f"Error: {exc}"
        await asyncio.to_thread(s.mcp_manager.reload)
        s.agent.reset_all_prompts()
        return (f"Added {name}: protocol {result['protocol']}, {len(result['tools'])} tools. Enable it with mcp:{name} in TOOLSETS. "
                "Headers for authentication can only be set on the dashboard.")

    async def _instructions(self, args: str, ctx: Context) -> str:
        s = self._s
        if args.lower() == "reload":
            s.agent.reset_all_prompts()
            return "The instructions will be re-read at the next message."
        overview = await asyncio.to_thread(s.instructions.overview)
        groups = [*overview["servers"], *overview["toolsets"]]
        if args:
            wanted = args.strip().lower()
            for g in groups:
                if wanted in (f"{g['kind']}:{g['name']}".lower(), g["name"].lower()):
                    text = g["text"] or g.get("server_text") or "(nothing written)"
                    return f"{g['kind']}:{g['name']}{'' if g['enabled'] else ' [disabled]'}\n{text}\n" + "\n".join(f"- {t['name']}: {t['text'] or t['description'][:100]}" for t in g["tools"][:30])
            return f"No server or tool group called {args!r}. Try /instructions."
        lines = [f"{g['kind']}:{g['name']}: " + (first_sentence(g["text"]) if g["text"] else "(undocumented)" if g["kind"] == "mcp" else "(none)") for g in groups if g["text"] or g["kind"] == "mcp"]
        lines.append(f"Capabilities section: {overview['used']:,} of {overview['budget']:,} characters.")
        return "\n".join(lines) if len(lines) > 1 else "No instructions written yet. " + lines[0]

    async def _assist(self, args: str, ctx: Context) -> str:
        """/assist <mcp:name|toolset:name|tool:name> [hint]: draft or improve its instruction and score it. Saves nothing."""
        s = self._s
        scope, _, hint = args.partition(" ")
        if not scope:
            return "Usage: /assist <mcp:name | toolset:name | tool:name> [hint]. It drafts or improves the instruction, tests it on a few requests and shows the score; nothing is saved (use the Tools page to accept it)."
        try:
            stored = s.instructions.get(scope)
            mode = "improve" if stored and stored["text"].strip() else "generate"
            drafted = await s.instructions.suggest(scope, hint.strip(), mode=mode, current=stored["text"] if stored else "")
            result = await s.assist.evaluate(scope, drafted["draft"], hint=hint.strip())
        except InstructionError as exc:
            return f"Cannot do that: {exc}"
        failed = [c for c in result["cases"] if not c["passed"]]
        lines = [f"{'Improved' if mode == 'improve' else 'Draft'} for {scope} (written by {drafted['provider']} / {drafted['model']}):", result["text"], "",
                 f"Score {result['score'][0]}/{result['score'][1]}" + (f"; the saved text scores {result['baseline'][0]}/{result['baseline'][1]}" if result["baseline"] else "")]
        lines += [f"- failed: \"{c['request']}\" (expected {c['expect']}, got {', '.join(c['chosen']) or 'no tool'})" for c in failed[:5]]
        lines.append("Nothing was saved. Accept it on the Tools page (Test and Refine are there too).")
        return "\n".join(lines)

    async def _refine(self, args: str, ctx: Context) -> str:
        result = await self._s.review.run(ctx.session_id, force=True)
        if result.acted and result.details:
            return f"Review: {result.summary()}\n" + "\n".join(f"- {d}" for d in result.details)
        return f"Review: {result.summary()}"

    async def _journey(self, args: str, ctx: Context) -> str:
        sub, _, rest = args.partition(" ")
        if sub == "delete":
            if not rest.strip().lstrip("#").isdigit():
                return "Usage: /journey delete <id>"
            try:
                return await asyncio.to_thread(undo_entry, self._s, int(rest.strip().lstrip("#")))
            except WriteError as exc:
                return f"Error: {exc}"
        if args:
            return "Usage: /journey or /journey delete <id>"
        rows = await asyncio.to_thread(self._s.store.journal_list, 15)
        if not rows:
            return "Nothing learned yet."
        return "\n".join(f"#{r['id']} {time.strftime('%Y-%m-%d %H:%M', time.localtime(r['ts']))} {r['kind']}/{r['action']} [{r['status']}] {r['summary'][:100]}" for r in rows)

    async def _cron(self, args: str, ctx: Context) -> str:
        sched, store = self._s.scheduler, self._s.store
        verb, _, rest = args.partition(" ")
        verb, rest = verb.lower(), rest.strip()
        try:
            if not verb or verb == "list":
                rows = await asyncio.to_thread(store.schedule_list)
                if not rows:
                    return "No scheduled tasks. Add one with /cron add <name> | <schedule> | <prompt>, for example\n/cron add morning | every morning at 8 | Summarise the news about Python"
                return "\n".join(
                    f"{'on ' if r['enabled'] else 'off'} {r['name']}  [{r['expression']}]  next {sched.describe(r)['next_run_text'] or '-'}  last {r['last_status'] or '-'}"
                    for r in rows
                )
            if verb == "add":
                parts = [p.strip() for p in rest.split("|", 2)]
                if len(parts) != 3 or not all(parts):
                    return "Usage: /cron add <name> | <schedule> | <prompt>   (schedule: 'every morning at 8', 'ogni lunedì alle 9', '0 8 * * *', @daily or 'every 30m')"
                allowed = self._s.config.get("TELEGRAM_ALLOWED_IDS")
                chat = chat_of_session(ctx.session_id) if ctx.source == "telegram" else (allowed[0] if allowed else None)
                row = await asyncio.to_thread(sched.create, parts[0], parts[1], parts[2], chat if chat in allowed else None)
                where = "delivered on Telegram" if row["deliver_chat"] is not None else "results are kept on the Schedules page (no Telegram chat to deliver to)"
                return f"Task {row['name']!r} created, next run {sched.describe(row)['next_run_text']}; {where}."
            if not rest:
                return f"Usage: /cron {verb} <name>"
            name = rest.lower()
            if verb in ("pause", "resume"):
                await asyncio.to_thread(sched.set_enabled, name, verb == "resume")
                return f"Task {name!r} {'resumed' if verb == 'resume' else 'paused'}."
            if verb == "run":
                await asyncio.to_thread(sched.run_now, name)
                return f"Task {name!r} started; I will deliver the result when it finishes."
            if verb in ("remove", "delete"):
                await asyncio.to_thread(sched.remove, name)
                return f"Task {name!r} removed."
        except SchedulerError as exc:
            return f"Error: {exc}"
        return "Usage: /cron [list | add <name> | <schedule> | <prompt> | pause|resume|run|remove <name>]"

    async def _agents(self, args: str, ctx: Context) -> str:
        rows = await asyncio.to_thread(self._s.store.agent_list)
        if not rows:
            return "No sub-agents are defined. Add them on the Agents page of the dashboard."
        return "\n".join(f"{'on ' if r['enabled'] else 'off'} {r['name']}  [{r['route']}]  {r['description']}" for r in rows) + "\nUse /agent <name> <task>."

    async def _agent(self, args: str, ctx: Context) -> str:
        name, _, task = args.partition(" ")
        if not name or not task.strip():
            return "Usage: /agent <name> <task>   (see /agents)"
        try:
            result = await self._s.agents.run(name.lower(), task, "", ToolContext(self._s, ctx.session_id, ctx.source, ctx.restricted))
        except AgentError as exc:
            return f"Error: {exc}"
        return result["output"] if result["status"] == "ok" else f"Error: {result['error']}"

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
