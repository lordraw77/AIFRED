"""Sampling for MCP servers (revision 2026-07-28): a server asks the client to run its model on a prompt (spec 9.2).

Sampling lets a remote server spend the owner's tokens and read what the model writes, so it is off until ``MCP_SAMPLING`` is set and
is narrow when on: text only (no images, no tools), a bounded prompt and answer, a few requests an hour, the ``default`` route and
no tool is ever run for the server. Each call is counted in ``llm_usage`` under the agent ``mcp:<server>``.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections import deque
from typing import TYPE_CHECKING, Any

from aifred.llm import LLMError
from aifred.mcp_client import McpError
from aifred.router import NoProvider, RouteError

if TYPE_CHECKING:
    from aifred.app import Services

MAX_MESSAGES = 20
MAX_PROMPT_CHARS = 20_000
MAX_ANSWER_TOKENS = 1_500
CHARS_PER_TOKEN = 4


class Sampler:
    """Answers ``sampling/createMessage`` for every server, with one shared hourly allowance."""

    def __init__(self, services: Services) -> None:
        self._s = services
        self._runs: deque[float] = deque()
        self._guard = threading.Lock()

    def enabled(self) -> bool:
        return bool(self._s.config.get("MCP_SAMPLING"))

    def _take_slot(self) -> None:
        limit = int(self._s.config.get("MCP_SAMPLING_PER_HOUR"))
        now = time.monotonic()
        with self._guard:
            while self._runs and now - self._runs[0] > 3600:
                self._runs.popleft()
            if len(self._runs) >= limit:
                raise McpError(f"at most {limit} sampling requests an hour (MCP_SAMPLING_PER_HOUR)")
            self._runs.append(now)

    @staticmethod
    def _text(content: Any) -> str:
        blocks = content if isinstance(content, list) else [content]
        parts = []
        for block in blocks:
            if not isinstance(block, dict) or block.get("type") != "text" or not isinstance(block.get("text"), str):
                raise McpError("only text content is supported")
            parts.append(block["text"])
        return "\n".join(parts)

    def sample(self, server: str, params: dict[str, Any]) -> dict[str, Any]:
        """Run one request in a worker thread and return a ``CreateMessageResult``.

        Raises:
            McpError: If sampling is off, the request uses anything beyond text, the allowance is spent or no model answers.
        """
        if not self.enabled():
            raise McpError("sampling is turned off (MCP_SAMPLING)")
        if params.get("tools") or params.get("toolChoice"):
            raise McpError("sampling with tools is not supported")
        raw = params.get("messages")
        if not isinstance(raw, list) or not raw or len(raw) > MAX_MESSAGES:
            raise McpError(f"a sampling request needs 1 to {MAX_MESSAGES} messages")
        messages: list[dict[str, Any]] = []
        system = params.get("systemPrompt")
        if isinstance(system, str) and system.strip():
            messages.append({"role": "system", "content": system.strip()})
        for item in raw:
            if not isinstance(item, dict) or item.get("role") not in ("user", "assistant"):
                raise McpError("a sampling message needs the role user or assistant")
            messages.append({"role": item["role"], "content": self._text(item.get("content"))})
        if sum(len(m["content"]) for m in messages) > MAX_PROMPT_CHARS:
            raise McpError(f"the prompt is longer than {MAX_PROMPT_CHARS:,} characters")
        wanted = params.get("maxTokens")
        limit = max(1, min(int(wanted) if isinstance(wanted, int) and not isinstance(wanted, bool) else MAX_ANSWER_TOKENS, MAX_ANSWER_TOKENS))
        self._take_slot()
        llm = self._s.llm
        try:
            entries = llm.resolve("default", "default")
            result, entry, _ = asyncio.run(llm.complete(entries, messages, None, route="default", source="mcp-sampling", agent=f"mcp:{server}"))
        except (NoProvider, RouteError, LLMError) as exc:
            raise McpError(f"the model failed: {exc}") from None
        text = result.content or ""
        cut = len(text) > limit * CHARS_PER_TOKEN
        return {"role": "assistant", "content": {"type": "text", "text": text[:limit * CHARS_PER_TOKEN]}, "model": entry.model,
                "stopReason": "maxTokens" if cut else "endTurn"}
