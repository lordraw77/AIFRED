"""Tests for the command-line chat (aifred.gateways.cli)."""

from __future__ import annotations

import io
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from aifred.app import Services
from aifred.config import Settings
from aifred.gateways import cli
from aifred.gateways.cli import run_cli
from tests.fake_llm import FakeLLM, Status, Text, ToolCall


def typed(*lines: str | type[BaseException]) -> Callable[[str], str]:
    """An ``input`` that returns the lines, then raises EOFError (Ctrl-D)."""
    source: Iterator[str | type[BaseException]] = iter(lines)

    def read(prompt: str) -> str:
        try:
            item = next(source)
        except StopIteration:
            raise EOFError from None
        if isinstance(item, type):
            raise item
        return item

    return read


def session(chat: Callable[..., int], *lines: str | type[BaseException], **kw: Any) -> tuple[int, str]:
    out = io.StringIO()
    code = chat(input_fn=typed(*lines), out=out, **kw)
    return code, out.getvalue()


@pytest.fixture
def chat(settings: Settings, services: Services, llm_ready: FakeLLM) -> Callable[..., int]:
    """`run_cli` bound to the test data directory (the provider was set up through the `services` fixture)."""
    def run(**kw: Any) -> int:
        return run_cli(settings, **kw)

    run.fake = llm_ready  # type: ignore[attr-defined]
    run.services = services  # type: ignore[attr-defined]
    return run


def test_a_conversation(chat: Callable[..., int]) -> None:
    chat.fake.queue(Text("Hello! How can I help?", chunks=3))  # type: ignore[attr-defined]
    code, text = session(chat, "hi there", "/quit", session="cli:fixed")
    assert code == 0 and text == "> " * 0 + "Hello! How can I help?\nBye.\n"
    messages = chat.services.store.messages("cli:fixed")  # type: ignore[attr-defined]
    assert [(m["role"], m["content"]) for m in messages] == [("user", "hi there"), ("assistant", "Hello! How can I help?")]


def test_tools_are_shown_as_lines(chat: Callable[..., int]) -> None:
    chat.fake.queue(ToolCall("session_search", {"query": "pizza night"}), Text("Nothing found."))  # type: ignore[attr-defined]
    _, text = session(chat, "search my history", session="cli:t")
    assert text == '· session_search(query="pizza night")\nNothing found.\n\n'  # the last line is Ctrl-D ending the input'


def test_errors_are_printed_and_the_session_goes_on(chat: Callable[..., int]) -> None:
    chat.fake.always = Status(402)  # type: ignore[attr-defined]
    code, text = session(chat, "hi", "/think on", session="cli:e")
    assert code == 0 and text.startswith("Error: ") and "quota exhausted" in text and text.endswith("Reasoning is on for this conversation.\n\n")


def test_commands_new_session_and_blank_lines(chat: Callable[..., int]) -> None:
    chat.fake.queue(Text("one"), Text("two"))  # type: ignore[attr-defined]
    _, text = session(chat, "first", "", "   ", "/new", "second", session="cli:a")
    assert text == "one\nStarted a new conversation.\ntwo\n\n"
    ids = {r["id"] for r in chat.services.store.list_sessions()}  # type: ignore[attr-defined]
    assert "cli:a" in ids and len(ids) == 2 and chat.services.store.messages("cli:a")[0]["content"] == "first"  # type: ignore[attr-defined]


def test_resuming_a_session_keeps_the_history(chat: Callable[..., int]) -> None:
    fake = chat.fake  # type: ignore[attr-defined]
    fake.queue(Text("first answer"), Text("second answer"))
    session(chat, "question one", session="cli:keep")
    session(chat, "question two", session="cli:keep")
    sent = [m["content"] for m in fake.requests[1].body["messages"] if m["role"] != "system"]
    assert sent == ["question one", "first answer", "question two"]


def test_one_shot_messages(chat: Callable[..., int]) -> None:
    chat.fake.queue(Text("answer one"), Text("answer two"))  # type: ignore[attr-defined]
    code, text = session(chat, messages=["first", "second"], session="cli:o")
    assert code == 0 and text == "> first\nanswer one\n> second\nanswer two\n"  # no interactive prompt; exits after the last


def test_end_of_input_and_ctrl_c_at_the_prompt(chat: Callable[..., int]) -> None:
    assert session(chat) == (0, "\n")  # Ctrl-D leaves
    code, text = session(chat, KeyboardInterrupt)
    assert code == 0  # EOF after one Ctrl-C
    code, text = session(chat, KeyboardInterrupt, KeyboardInterrupt)
    assert code == 0 and "press Ctrl-C again" in text
    chat.fake.queue(Text("still here"))  # type: ignore[attr-defined]
    _, text = session(chat, KeyboardInterrupt, "hello", KeyboardInterrupt, "/quit", session="cli:c")
    assert "still here" in text  # typing something resets the counter


def test_ctrl_c_during_an_answer_returns_to_the_prompt(chat: Callable[..., int], monkeypatch: pytest.MonkeyPatch) -> None:
    async def interrupted(*args: Any, **kw: Any) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "_turn", interrupted)
    code, text = session(chat, "long question", "/think on", session="cli:i")
    assert code == 0 and "(interrupted)" in text and "Reasoning is on" in text


def test_dashboard_changes_apply_to_a_running_session(chat: Callable[..., int], services: Services) -> None:
    fake = chat.fake  # type: ignore[attr-defined]
    seen: list[Any] = []

    def read(prompt: str) -> str:
        if not seen:
            seen.append(1)
            return "first"
        if len(seen) == 1:
            seen.append(2)
            services.config.update({"LLM_TEMPERATURE": 0.9})  # another process (the dashboard) changes a setting
            return "second"
        raise EOFError

    run_cli_out = io.StringIO()
    chat(input_fn=read, out=run_cli_out, session="cli:r")
    assert [r.body["temperature"] for r in fake.requests] == [0.6, 0.9]


def test_no_provider_prints_the_pointer(settings: Settings, services: Services) -> None:
    out = io.StringIO()
    run_cli(settings, input_fn=typed("hello"), out=out, session="cli:n")
    assert "No LLM provider is configured" in out.getvalue() and "dashboard" in out.getvalue()


def test_startup_problem_is_reported(tmp_path: Any) -> None:
    out = io.StringIO()
    bad = Settings(_env_file=None, data_dir=tmp_path / "d", secrets_backend="openbao")  # type: ignore[call-arg]
    assert run_cli(bad, input_fn=typed(), out=out) == 2 and "AIFRED_OPENBAO_ADDR" in out.getvalue()


def test_argument_rendering() -> None:
    assert cli._arguments('{"a": 1, "b": "x"}') == 'a=1, b="x"'
    assert cli._arguments("not json") == "not json"
    assert len(cli._arguments(json_long := '{"k": "' + "x" * 300 + '"}')) == cli.TOOL_ARGS_WIDTH and json_long


def test_the_cli_entry_point(settings: Settings, services: Services, llm_ready: FakeLLM, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from aifred.__main__ import main

    llm_ready.queue(Text("from main"))
    monkeypatch.chdir(settings.data_dir.parent)  # never pick up a developer's .env
    monkeypatch.setenv("AIFRED_DATA_DIR", str(settings.data_dir))
    assert main(["cli", "--session", "cli:main", "-m", "say something"]) == 0
    assert "from main" in capsys.readouterr().out
