"""Command-line chat (spec 10.1).

A synchronous REPL on ``input()``: nothing runs while it waits for you, and Ctrl-C only ever interrupts the
answer being written. Settings are re-read from the database at the start of every turn, so changes made on
the dashboard apply to a running session. It is a separate process from ``serve`` sharing the same database.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from collections.abc import Callable
from typing import TextIO

from aifred.agent import DoneEvent, ErrorEvent, NoticeEvent, ResetEvent, TokenEvent, ToolCallEvent
from aifred.app import Services
from aifred.commands import Commands, Context
from aifred.config import Settings

PROMPT = "> "
TOOL_ARGS_WIDTH = 100


class Terminal:
    """Output helper: dim text only when writing to a terminal."""

    def __init__(self, out: TextIO) -> None:
        self.out = out
        self._tty = out.isatty()

    def write(self, text: str = "", end: str = "\n") -> None:
        self.out.write(text + end)
        self.out.flush()

    def dim(self, text: str, end: str = "\n") -> None:
        self.write(f"\x1b[2m{text}\x1b[0m" if self._tty else text, end)


def _arguments(raw: str) -> str:
    try:
        text = ", ".join(f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in json.loads(raw).items())
    except (ValueError, AttributeError):
        text = raw
    return text if len(text) <= TOOL_ARGS_WIDTH else text[: TOOL_ARGS_WIDTH - 1] + "…"


async def _turn(services: Services, term: Terminal, session: str, text: str) -> None:
    """Run one turn, printing the answer as it arrives."""
    streamed = False
    async for event in services.agent.run(session, text, "cli"):
        if isinstance(event, TokenEvent):
            term.write(event.text, end="")
            streamed = True
        elif isinstance(event, ToolCallEvent):
            term.dim(f"{chr(10) if streamed else ''}· {event.name}({_arguments(event.arguments)})")
            streamed = False
        elif isinstance(event, ResetEvent):
            term.dim("\n(the model changed; starting the answer again)")
            streamed = False
        elif isinstance(event, NoticeEvent):
            term.dim(f"{chr(10) if streamed else ''}[{event.text}]")
            streamed = False
        elif isinstance(event, ErrorEvent):
            term.write(f"{chr(10) if streamed else ''}Error: {event.message}")
            return
        elif isinstance(event, DoneEvent):
            term.write("" if streamed else event.content)  # after streamed tokens, just end the line


def run_cli(
    settings: Settings, *, session: str | None = None, messages: list[str] | None = None,
    input_fn: Callable[[str], str] = input, out: TextIO | None = None,
) -> int:
    """Run the chat until ``/quit``, end of input, or after the given messages.

    Args:
        settings: Bootstrap settings.
        session: Session to resume; a new one is started when omitted.
        messages: Messages to send and then exit (non-interactive use); the REPL runs when omitted.
        input_fn: Replaceable in tests.
        out: Where to write (defaults to stdout).

    Returns:
        The process exit code.
    """
    term = Terminal(out or sys.stdout)
    try:
        services = Services.open(settings)
    except ValueError as exc:
        term.write(f"Error: {exc}")
        return 2
    from aifred.hardening import harden_process

    harden_process()
    logging.getLogger().setLevel(logging.WARNING)  # keep the conversation readable
    if any(server.enabled for server in services.mcp.list()):
        services.mcp_manager.start()  # its own connections: the CLI is a separate process from the server
        services.mcp_manager.wait_ready(float(services.config.get("MCP_CONNECT_TIMEOUT")))
    commands = Commands(services)
    current = session or f"cli:{int(time.time())}"
    interrupted = False
    try:
        with asyncio.Runner() as runner:
            queue = list(messages) if messages is not None else None
            while True:
                if queue is not None:
                    if not queue:
                        return 0
                    text = queue.pop(0)
                    term.dim(f"{PROMPT}{text}")
                else:
                    try:
                        text = input_fn(PROMPT)
                        interrupted = False
                    except EOFError:
                        term.write()
                        return 0
                    except KeyboardInterrupt:
                        if interrupted:
                            term.write()
                            return 0
                        interrupted = True
                        term.write("\n(press Ctrl-C again, or type /quit, to leave)")
                        continue
                if not text.strip():
                    continue
                services.config.reload()  # dashboard changes apply to a running session
                try:
                    reply = runner.run(commands.run(text, Context(current, "cli", False)))
                    if reply is not None:
                        term.write(reply.text)
                        current = reply.session_id or current
                        if reply.quit:
                            return 0
                        continue
                    runner.run(_turn(services, term, current, text))
                except KeyboardInterrupt:
                    term.write("\n(interrupted)")
    finally:
        services.close()
