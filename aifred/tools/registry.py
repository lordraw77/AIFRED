"""Tool registry: schemas from Pydantic models, toolset selection, safe execution.

A tool is a function ``(ctx, args) -> str`` plus a Pydantic model for its arguments.
The model gives both the JSON schema sent to the LLM and the argument validation.
Exceptions and invalid arguments become ``Error: …`` strings returned to the
model; output is truncated to ``TOOL_OUTPUT_LIMIT``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, ValidationError

if TYPE_CHECKING:
    from aifred.app import Services

logger = logging.getLogger(__name__)

SOURCE_TO_GATEWAY = {"cli": "CLI", "telegram": "TELEGRAM", "api": "API", "web": "WEB", "cron": "CRON", "library": "WEB"}
RESTRICTED_TOOLSETS = frozenset({"search", "skills", "web"})
"""Toolsets an open (unauthenticated) gateway may use, further limited by each tool's ``restricted_ok``."""


class RawArgs(BaseModel):
    """Arguments of a tool whose schema comes from elsewhere (MCP): any object is accepted and the server validates."""

    model_config = ConfigDict(extra="allow")


class ToolError(Exception):
    """An expected failure; the message is returned to the model as ``Error: …``."""


@dataclass
class ToolContext:
    """What a tool may touch during one call.

    Attributes:
        services: The process services.
        session_id: Calling session.
        source: Gateway name (``cli``, ``telegram``, ``api``, ``web``).
        restricted: Whether the caller is an open gateway user (spec 10.2).
        toolsets: Exactly these toolsets are on, whatever the gateway's settings say (a sub-agent's own set, 14.1).
    """

    services: Services
    session_id: str
    source: str
    restricted: bool = False
    toolsets: frozenset[str] | None = None

    @property
    def search_prefix(self) -> str | None:
        """Session-id prefix a restricted caller may search (its own chat), else ``None``."""
        if not self.restricted:
            return None
        parts = self.session_id.split(":")
        if self.source == "telegram" and len(parts) >= 3:
            return ":".join(parts[:2]) + ":"
        return self.session_id  # anything else: only the session itself


@dataclass(frozen=True)
class Tool:
    """A registered tool.

    Attributes:
        name: Function name shown to the model.
        toolset: Toolset that enables it.
        description: What it does, for the model.
        args: Pydantic model of the arguments.
        fn: Implementation, run in a worker thread.
        restricted_ok: Whether open gateways may use it.
        available: Extra condition (for example a configured URL).
        raw_schema: JSON schema sent as is, for tools defined by an MCP server (``args`` is then :class:`RawArgs`).
    """

    name: str
    toolset: str
    description: str
    args: type[BaseModel]
    fn: Callable[[ToolContext, Any], str]
    restricted_ok: bool = False
    available: Callable[[ToolContext], bool] = field(default=lambda ctx: True)
    raw_schema: dict[str, Any] | None = None

    def schema(self) -> dict[str, Any]:
        """OpenAI function-tool definition."""
        if self.raw_schema is not None:
            return {"type": "function", "function": {"name": self.name, "description": self.description, "parameters": self.raw_schema}}
        parameters = _clean_schema(self.args.model_json_schema())
        parameters.setdefault("type", "object")
        parameters["additionalProperties"] = False
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": parameters},
        }


def _clean_schema(node: Any) -> Any:
    """Drop Pydantic's ``title`` noise so the schema stays small."""
    if isinstance(node, dict):
        return {
            k: _clean_schema(v) for k, v in node.items() if not (k == "title" and isinstance(v, str))
        }
    if isinstance(node, list):
        return [_clean_schema(v) for v in node]
    return node


class ToolRegistry:
    """All tools, with toolset selection and execution."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._sources: list[Callable[[], list[Tool]]] = []
        self.calls: dict[str, int] = {}
        """Calls per tool since the process started (shown on the Tools page)."""

    def fork(self) -> ToolRegistry:
        """A registry with the same static tools and no dynamic sources (one per process, so sources never leak between them)."""
        other = ToolRegistry()
        other._tools = dict(self._tools)
        return other

    def add_source(self, source: Callable[[], list[Tool]]) -> None:
        """Add a provider of dynamic tools (the MCP manager); it is asked on every request, so it must be cheap."""
        self._sources.append(source)

    def register(self, tool: Tool) -> Tool:
        """Add a tool (names are unique)."""
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool {tool.name}")
        self._tools[tool.name] = tool
        return tool

    def tool(
        self, name: str, toolset: str, description: str, args: type[BaseModel], *, restricted_ok: bool = False,
        available: Callable[[ToolContext], bool] | None = None,
    ) -> Callable[[Callable[[ToolContext, Any], str]], Callable[[ToolContext, Any], str]]:
        """Decorator registering a function as a tool."""

        def decorator(fn: Callable[[ToolContext, Any], str]) -> Callable[[ToolContext, Any], str]:
            self.register(Tool(name, toolset, description, args, fn, restricted_ok, available or (lambda ctx: True)))
            return fn

        return decorator

    def all(self) -> list[Tool]:
        """Every registered tool, dynamic ones included."""
        return [*self._tools.values(), *(t for source in self._sources for t in source())]

    @staticmethod
    def toolset_enabled(toolset: str, enabled: set[str]) -> bool:
        """Whether a toolset is on; ``mcp`` switches on every ``mcp:<server>``."""
        return toolset in enabled or (toolset.startswith("mcp:") and "mcp" in enabled)

    @staticmethod
    def enabled_toolsets(services: Services, source: str) -> set[str]:
        """Toolsets enabled for a gateway: its own list, else ``TOOLSETS`` (+ ``terminal`` by shortcut)."""
        cfg = services.config
        gateway = SOURCE_TO_GATEWAY.get(source)
        own = cfg.get(f"TOOLSETS_{gateway}") if gateway else None
        if own is not None:
            return {*own, "help"}
        base = set(cfg.get("TOOLSETS"))
        if cfg.get("ENABLE_TERMINAL"):
            base.add("terminal")
        return {*base, "help"}  # tool_help is always on: it is how a shortened Capabilities section is read in full

    def for_context(self, ctx: ToolContext, *, skip_help: bool = False) -> list[Tool]:
        """Tools exposed to the model in this context; a disabled toolset is never exposed.

        ``skip_help`` leaves out the ``help`` toolset, whose availability itself depends on this list.
        """
        enabled = set(ctx.toolsets) if ctx.toolsets is not None else self.enabled_toolsets(ctx.services, ctx.source)
        out = []
        for tool in self.all():
            if skip_help and tool.toolset == "help":
                continue
            if not self.toolset_enabled(tool.toolset, enabled):
                continue
            if ctx.restricted and not (tool.restricted_ok and tool.toolset in RESTRICTED_TOOLSETS):
                continue
            if not tool.available(ctx):
                continue
            out.append(tool)
        return out

    def schemas(self, ctx: ToolContext) -> list[dict[str, Any]]:
        """Function definitions for the request body (empty list when no tool is enabled)."""
        return [t.schema() for t in self.for_context(ctx)]

    async def execute(self, ctx: ToolContext, name: str, raw_arguments: str, *, allow: frozenset[str] | None = None) -> str:
        """Run a tool call and return its output (never raises).

        Args:
            ctx: Call context.
            name: Tool name requested by the model.
            raw_arguments: JSON text of the arguments.
            allow: Exactly these tools may run, whatever toolsets are enabled (the background review).
        """
        limit = int(ctx.services.config.get("TOOL_OUTPUT_LIMIT"))
        if allow is not None:
            tools = {t.name: t for t in self.all() if t.name in allow and t.available(ctx)}
        else:
            tools = {t.name: t for t in self.for_context(ctx)}
        tool = tools.get(name)
        if tool is None:
            return f"Error: unknown tool {name!r}"
        self.calls[name] = self.calls.get(name, 0) + 1
        try:
            data = json.loads(raw_arguments) if raw_arguments.strip() else {}
        except json.JSONDecodeError as exc:
            return f"Error: the arguments are not valid JSON ({exc.msg})"
        try:
            args = tool.args.model_validate(data)
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(p) for p in e['loc']) or 'arguments'}: {e['msg']}" for e in exc.errors()
            )
            return f"Error: invalid arguments ({problems})"
        try:
            result = await asyncio.to_thread(tool.fn, ctx, args)
        except ToolError as exc:
            result = f"Error: {exc}"
        except Exception as exc:  # noqa: BLE001 - tool failures must reach the model, not crash the turn
            logger.exception("Tool %s failed", name)
            result = f"Error: {type(exc).__name__}: {exc}"
        return truncate(result, limit)


def truncate(text: str, limit: int) -> str:
    """Cut text to ``limit`` characters, noting how much was dropped."""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n… [truncated {len(text) - limit:,} characters]"
