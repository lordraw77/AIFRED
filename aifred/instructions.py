"""Instructions: what an MCP server, a toolset or a tool is *for* (spec 9.3).

A tool schema says how to call a tool, not why it exists. The admin writes a short note per MCP server, toolset or tool;
the system prompt gets a *Capabilities* section built from the notes of what is enabled for the gateway at hand, shortened
to ``INSTRUCTIONS_BUDGET`` characters, and ``tool_help`` returns the full text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from aifred.router import NoProvider, RouteError
from aifred.llm import LLMError
from aifred.tools.registry import Tool, ToolContext, ToolError

if TYPE_CHECKING:
    from aifred.app import Services

SCOPE_RE = re.compile(r"^(mcp:[a-z0-9_]{1,24}|toolset:[a-z0-9_]{1,24}|tool:[A-Za-z0-9_-]{1,64})$")
MAX_TEXT = 2000
DRAFT_TARGET = 600
SUGGEST_PROMPT = """You write the short note that tells an AI assistant what a group of tools is FOR, so it picks the right tool without being told.
Write plain text, at most 600 characters, in the language of the admin's hint (English if there is none), with these parts in this order: what it gives access to; "Use it when": the kinds of request it fits; "Do not use it for": near misses that belong to another tool; key names, address ranges or limits.
Base it ONLY on the facts given below. Do not invent capabilities. State how it differs from the other notes if they overlap. Reply with the note only, no preamble, no Markdown fences."""
MEMORY_CHARS = 1500
MODES = ("generate", "improve", "shorten")
MODE_PROMPTS = {
    "generate": "",
    "improve": "\nRewrite the current note: keep every statement that is correct, fix what is vague or wrong against the facts, and add what is missing.",
    "shorten": f"\nCut the current note to at most {DRAFT_TARGET} characters without losing when to use it and when not to.",
}
SHORT_CHARS = 160
INTERNAL_TOOLSETS = frozenset({"help", "review"})
"""Toolsets that are not the admin's to configure: ``help`` is always on, ``review`` only exists inside the background review."""
HEADER = "## Capabilities (what the tool groups are for; tool_help(name) gives the full text)"


class InstructionError(ValueError):
    """An invalid scope or text; the message is shown to the admin."""


def first_sentence(text: str, limit: int = SHORT_CHARS) -> str:
    """The first sentence of a text, cut to ``limit`` characters."""
    flat = " ".join(text.split())
    match = re.search(r"(?<=[.!?])\s", flat)
    sentence = flat[: match.start()] if match else flat
    return sentence if len(sentence) <= limit else sentence[: limit - 1].rstrip() + "…"


@dataclass
class Entry:
    """One line of the Capabilities section.

    Attributes:
        scope: ``mcp:<name>`` or ``toolset:<name>``.
        text: The instruction in force (the admin's, else what the server says about itself, else the tool names).
        source: ``custom``, ``server`` or ``tools``.
        tools: Names of the tools in the group that this gateway can use.
        notes: Tool-level instructions as ``(tool name, text)``.
    """

    scope: str
    text: str
    source: str
    tools: list[str]
    notes: list[tuple[str, str]] = field(default_factory=list)

    @property
    def undocumented(self) -> bool:
        """Whether the admin has not written anything for it."""
        return self.source != "custom"


class InstructionService:
    """Reads and writes instructions and builds what the agent sees.

    Args:
        services: The process services.
    """

    def __init__(self, services: Services) -> None:
        self._s = services

    # Storage

    def get(self, scope: str) -> dict[str, Any] | None:
        """One stored instruction."""
        return self._s.store.instruction_get(scope)

    @staticmethod
    def check(scope: str, text: str | None = None) -> None:
        """Validate a scope and, when given, a text.

        Raises:
            InstructionError: If either is invalid.
        """
        if not SCOPE_RE.match(scope):
            raise InstructionError("the scope must look like mcp:<name>, toolset:<name> or tool:<name>")
        if text is not None:
            if not text.strip():
                raise InstructionError("the instruction is empty")
            if len(text) > MAX_TEXT:
                raise InstructionError(f"the instruction is too long ({len(text):,} > {MAX_TEXT:,} characters)")

    def set(self, scope: str, text: str, *, enabled: bool = True, session_id: str | None = None, score: str = "") -> None:
        """Create or replace an instruction (journaled with the previous text so it can be undone).

        Raises:
            InstructionError: On an invalid scope or text.
        """
        self.check(scope, text)
        previous = self.get(scope)
        text = text.strip()
        self._s.store.instruction_set(scope, text, enabled)
        self._s.store.journal_add(
            "instruction", "update" if previous else "create", f"{scope}: {first_sentence(text, 100)}" + (f" [test {score}]" if score else ""), session_id, "applied",
            {"scope": scope, "previous": previous["text"] if previous else None, "previous_enabled": previous["enabled"] if previous else None, "score": score or None},
        )

    def delete(self, scope: str) -> bool:
        """Remove an instruction; returns whether it existed."""
        self.check(scope)
        previous = self.get(scope)
        if previous is None:
            return False
        self._s.store.instruction_delete(scope)
        self._s.store.journal_add("instruction", "delete", f"{scope}: deleted", None, "applied",
                                  {"scope": scope, "previous": previous["text"], "previous_enabled": previous["enabled"]})
        return True

    def restore(self, scope: str, previous: str | None, enabled: bool | None) -> None:
        """Put an instruction back as it was (undo)."""
        if previous is None:
            self._s.store.instruction_delete(scope)
        else:
            self._s.store.instruction_set(scope, previous, True if enabled is None else enabled)

    # What a gateway can see

    def visible(self, ctx: ToolContext, override: dict[str, str] | None = None) -> list[Entry]:
        """The documented (or undocumented MCP) groups available to this context, in a stable order.

        ``override`` replaces the stored text of some scopes for this call only (the assistant tests a draft this way).
        """
        s = self._s
        stored = {r["scope"]: r for r in s.store.instruction_list() if r["enabled"]}
        for scope, text in (override or {}).items():
            stored[scope] = {"scope": scope, "text": text, "enabled": True}
        groups: dict[str, list[Tool]] = {}
        for tool in s.tools.for_context(ctx, skip_help=True):
            groups.setdefault(tool.toolset, []).append(tool)
        entries: list[Entry] = []
        for toolset, tools in sorted(groups.items()):
            names = sorted(t.name for t in tools)
            notes = [(t.name, stored[f"tool:{t.name}"]["text"]) for t in sorted(tools, key=lambda t: t.name) if f"tool:{t.name}" in stored]
            if toolset.startswith("mcp:"):
                scope = f"mcp:{toolset[4:]}"
                text, source = (stored[scope]["text"], "custom") if scope in stored else ("", "tools")
                if not text:
                    own, _ = s.mcp_manager.describe(toolset[4:])
                    if own.strip():
                        text, source = own.strip(), "server"
                if not text:
                    text = "tools: " + ", ".join(self._short_name(n, toolset[4:]) for n in names[:12]) + ("…" if len(names) > 12 else "")
                entries.append(Entry(scope, text, source, names, notes))
            else:
                scope = f"toolset:{toolset}"
                if scope in stored or notes:
                    entries.append(Entry(scope, stored[scope]["text"] if scope in stored else "", "custom", names, notes))
        return entries

    @staticmethod
    def _short_name(tool: str, server: str) -> str:
        return tool.removeprefix(f"mcp__{server}__")

    def has_content(self, ctx: ToolContext) -> bool:
        """Whether there is anything to explain (``tool_help`` is only offered then)."""
        return bool(self.visible(ctx))

    def capabilities(self, ctx: ToolContext, override: dict[str, str] | None = None) -> str:
        """The Capabilities section for the system prompt (empty when there is nothing to say)."""
        entries = self.visible(ctx, override)
        if not entries:
            return ""
        budget = int(self._s.config.get("INSTRUCTIONS_BUDGET"))

        def line(e: Entry, short: bool) -> list[str]:
            label = f"{e.scope} ({len(e.tools)} tool{'s' if len(e.tools) != 1 else ''})"
            text = first_sentence(e.text) if short else " ".join(e.text.split())
            out = [f"- {label}: {text}" if text else f"- {label}"]
            if not short:
                out += [f"    - {name}: {' '.join(note.split())}" for name, note in e.notes]
            return out

        full = "\n".join([HEADER, *(ln for e in entries for ln in line(e, False))])
        if len(full) <= budget:
            return full
        shortened = "\n".join([HEADER, *(ln for e in entries for ln in line(e, True))])
        if len(shortened) <= budget:
            return shortened
        kept, rest = [HEADER], []
        for e in entries:
            candidate = line(e, True)
            if len("\n".join([*kept, *candidate])) + 80 <= budget:
                kept += candidate
            else:
                rest.append(e.scope)
        if rest:
            kept.append(f"- more (use tool_help): {', '.join(rest)}")
        return "\n".join(kept)

    def help_text(self, ctx: ToolContext, name: str) -> str:
        """The full note and tool list for a server, toolset or tool the context can see.

        Raises:
            ToolError: If nothing visible has that name.
        """
        s = self._s
        wanted = name.strip()
        tools = {t.name: t for t in s.tools.for_context(ctx, skip_help=True)}
        stored = {r["scope"]: r for r in s.store.instruction_list() if r["enabled"]}
        if wanted in tools:
            t = tools[wanted]
            note = stored.get(f"tool:{t.name}", {}).get("text")
            params = list((t.raw_schema or t.args.model_json_schema()).get("properties", {}))
            return "\n".join([f"{t.name} ({t.toolset})", f"Instruction: {note or '(none written)'}", f"Description: {t.description}", f"Parameters: {', '.join(params) or 'none'}"])
        for entry in self.visible(ctx):
            if wanted in (entry.scope, entry.scope.split(":", 1)[1]):
                label = {"custom": "Instruction", "server": "The server describes itself as", "tools": "No instruction written; its tools are"}[entry.source]
                lines = [f"{entry.scope}", f"{label}: {entry.text}"]
                lines += [f"Note for {n}: {t}" for n, t in entry.notes]
                lines.append("Tools:")
                lines += [f"- {n}: {tools[n].description[:200]}" for n in entry.tools[:40] if n in tools]
                return "\n".join(lines)
        known = ", ".join(e.scope for e in self.visible(ctx)) or "nothing documented"
        raise ToolError(f"there is nothing called {wanted!r} to explain here (known: {known})")

    # Drafting

    def _facts(self, scope: str, tools: list[dict[str, Any]] | None) -> tuple[str, list[dict[str, Any]]]:
        """The name of the target and the tools a draft may be based on (names, descriptions, parameter names; never outputs)."""
        s = self._s
        kind, _, name = scope.partition(":")
        if tools is not None:
            return name, tools
        if kind == "mcp":
            from aifred.mcp import tool_info

            _, discovered = s.mcp_manager.describe(name)
            return name, [{**tool_info(t), "name": f"mcp__{name}__{t.name}"} for t in discovered]
        found = [t for t in s.tools.all() if (t.toolset == name if kind == "toolset" else t.name == name)]
        return name, [{"name": t.name, "description": t.description[:300], "params": list((t.raw_schema or t.args.model_json_schema()).get("properties", {}))[:20]} for t in found]

    async def suggest(
        self, scope: str, hint: str = "", tools: list[dict[str, Any]] | None = None, *, mode: str = "generate", current: str = "",
        use_memory: bool = False,
    ) -> dict[str, Any]:
        """Ask the model for a draft instruction; nothing is saved.

        Args:
            scope: What the draft is for.
            hint: Free text from the admin ("my local Linux servers").
            tools: Tool descriptions to use instead of the discovered ones (a server that is not saved yet).
            mode: ``generate`` (from scratch), ``improve`` (rewrite ``current``, keeping what is right) or ``shorten``
                (cut ``current`` to the target length without losing when to use it).
            current: The text being improved or shortened.
            use_memory: Also give the model the owner's profile and curated notes (names, address ranges). Off by default
                because that text leaves the machine when the route is external.

        Raises:
            InstructionError: If there is nothing to base a draft on, or no model is available.
        """
        import asyncio

        self.check(scope)
        if mode not in MODES:
            raise InstructionError(f"mode must be one of {', '.join(MODES)}")
        if mode != "generate" and not current.strip():
            raise InstructionError(f"there is no text to {mode}: write one or use Generate")
        s = self._s
        name, facts = await asyncio.to_thread(self._facts, scope, tools)
        if not facts:
            raise InstructionError("there are no tools to describe yet: connect the server first, or write the note by hand")
        others = [f"- {r['scope']}: {first_sentence(r['text'], 200)}" for r in s.store.instruction_list() if r["enabled"] and r["scope"] != scope]
        listing = "\n".join(f"- {t['name']}: {t.get('description', '')[:300]} (parameters: {', '.join(t.get('params', [])) or 'none'})" for t in facts[:60])
        user = (f"Target: {scope}\nTools:\n{listing}\n\nAdmin's hint: {hint.strip()[:500] or '(none)'}\n\n"
                f"Other notes already written:\n{chr(10).join(others) or '(none)'}")
        if mode != "generate":
            user += f"\n\nCurrent note to {mode}:\n{current.strip()[:MAX_TEXT]}"
        if use_memory:
            facts_text = "\n".join(f"{t.upper()}: {e}" for t in ("user", "memory") for e in s.memory.snapshot(t).entries)[:MEMORY_CHARS]
            if facts_text:
                user += f"\n\nWhat the assistant knows about its owner (use it for names and ranges; do not repeat private details):\n{facts_text}"
        try:
            try:
                entries = s.llm.resolve("assist", "default")
            except RouteError:  # no route called assist: the default one drafts
                entries = s.llm.resolve("default", "default")
            result, entry, _ = await s.llm.complete(
                entries, [{"role": "system", "content": SUGGEST_PROMPT + MODE_PROMPTS[mode]}, {"role": "user", "content": user}], None, route="assist", source="assist", agent="assist",
            )
        except (NoProvider, RouteError, LLMError) as exc:
            raise InstructionError(f"no draft could be made: {exc}") from None
        draft = result.content.strip().strip("`").strip()
        warnings = []
        if len(draft) > DRAFT_TARGET:
            warnings.append(f"the draft is {len(draft)} characters; {DRAFT_TARGET} is the target")
        if len(draft) < 40:
            warnings.append("the draft is very short; it may be too vague to guide tool choice")
        budget = int(s.config.get("INSTRUCTIONS_BUDGET"))
        if len(self.capabilities(ToolContext(s, "dashboard", "web"))) + len(draft) > budget:
            warnings.append("with this note the Capabilities section would pass INSTRUCTIONS_BUDGET and be shortened")
        return {"draft": draft[:MAX_TEXT], "provider": entry.provider.slug, "model": entry.model, "warnings": warnings, "tools": len(facts), "target": name}

    # Dashboard view

    def overview(self) -> dict[str, Any]:
        """Every MCP server and toolset with its tools, its stored text and what the agent would see."""
        s = self._s
        stored = {r["scope"]: r for r in s.store.instruction_list()}

        def stored_view(scope: str) -> dict[str, Any]:
            row = stored.get(scope)
            return {"scope": scope, "text": row["text"] if row else "", "enabled": row["enabled"] if row else True, "stored": row is not None}

        servers = []
        for server in s.mcp.list():
            own, tools = s.mcp_manager.describe(server.name)
            exposed = [{"name": f"mcp__{server.name}__{t.name}", "description": t.description[:300]} for t in tools]
            base = stored_view(f"mcp:{server.name}")
            servers.append({
                **base, "kind": "mcp", "name": server.name, "server_text": own, "undocumented": not base["stored"],
                "tools": [{**t, **stored_view(f"tool:{t['name']}")} for t in exposed],
            })
        groups: dict[str, list[Tool]] = {}
        for tool in s.tools.all():
            if not tool.toolset.startswith("mcp:") and tool.toolset not in INTERNAL_TOOLSETS:
                groups.setdefault(tool.toolset, []).append(tool)
        toolsets = [{
            **stored_view(f"toolset:{name}"), "kind": "toolset", "name": name,
            "tools": [{"name": t.name, "description": t.description[:300], **stored_view(f"tool:{t.name}")} for t in sorted(tools, key=lambda t: t.name)],
        } for name, tools in sorted(groups.items())]
        ctx = ToolContext(s, "dashboard", "web")
        text = self.capabilities(ctx)
        return {"servers": servers, "toolsets": toolsets, "budget": int(s.config.get("INSTRUCTIONS_BUDGET")), "used": len(text), "preview": text}
