"""Tests for aifred.agent against a scripted fake LLM."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import pytest

from aifred.agent import (
    Agent,
    DoneEvent,
    ErrorEvent,
    Event,
    ResetEvent,
    TokenEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from aifred.app import Services
from tests.fake_llm import Break, FakeLLM, Status, Text, ToolCall

pytestmark = pytest.mark.anyio


async def run(services: Services, text: str = "hello", session: str = "cli:1", source: str = "cli", **kw: Any) -> list[Event]:
    return [e async for e in services.agent.run(session, text, source, **kw)]


def types(events: list[Event]) -> list[str]:
    return [e.type for e in events]


async def test_plain_answer(services: Services, llm_ready: FakeLLM) -> None:
    llm_ready.queue(Text("Hi there!", chunks=3))
    events = await run(services, "hello")
    assert types(events) == ["token", "token", "token", "done"]
    assert events[-1] == DoneEvent("Hi there!")
    assert "".join(e.text for e in events if isinstance(e, TokenEvent)) == "Hi there!"
    assert [(m["role"], m["content"]) for m in services.store.messages("cli:1")] == [("user", "hello"), ("assistant", "Hi there!")]
    assert services.store.get_session("cli:1")["title"] == "hello"  # type: ignore[index]
    body = llm_ready.requests[0].body
    assert [m["role"] for m in body["messages"]] == ["system", "user"] and body["messages"][1]["content"] == "hello"


async def test_events_serialise(services: Services) -> None:
    assert TokenEvent("a").to_dict() == {"type": "token", "text": "a"}
    assert ToolCallEvent("memory", "{}").to_dict() == {"type": "tool_call", "name": "memory", "arguments": "{}"}
    assert ToolResultEvent("memory", "ok").to_dict()["type"] == "tool_result"
    assert ResetEvent().to_dict() == {"type": "reset"}
    assert ErrorEvent("x").to_dict() == {"type": "error", "message": "x"}


async def test_tool_round_trip_is_stored_and_replayed(services: Services, llm_ready: FakeLLM) -> None:
    llm_ready.queue(ToolCall("memory", {"action": "add", "target": "user", "content": "Prefers tea"}), Text("Noted."))
    events = await run(services, "remember that I prefer tea")
    assert types(events) == ["tool_call", "tool_result", "token", "token", "done"]
    call, result = events[0], events[1]
    assert isinstance(call, ToolCallEvent) and call.name == "memory" and '"Prefers tea"' in call.arguments
    assert isinstance(result, ToolResultEvent) and result.content.startswith("Done. user memory now uses 11 of")
    assert services.memory.snapshot("user").entries == ["Prefers tea"]

    rows = services.store.messages("cli:1")
    assert [r["role"] for r in rows] == ["user", "assistant", "tool", "assistant"]
    assert rows[1]["tool_calls"][0]["function"]["name"] == "memory" and rows[2]["tool_call_id"] == "call_abc123xyz"
    second = llm_ready.requests[1].body["messages"]  # the model sees its own call and the tool result
    assert [m["role"] for m in second] == ["system", "user", "assistant", "tool"]
    assert second[2]["tool_calls"][0]["id"] == second[3]["tool_call_id"]
    assert llm_ready.requests[0].body["tools"] and {t["function"]["name"] for t in llm_ready.requests[0].body["tools"]} >= {"memory", "session_search"}


async def test_tool_errors_are_returned_to_the_model(services: Services, llm_ready: FakeLLM) -> None:
    llm_ready.queue(
        ToolCall("does_not_exist", {}), ToolCall("memory", {"action": "explode"}), ToolCall("file_read", {"path": "../../etc/passwd"}),
        Text("I could not do that."),
    )
    events = await run(services, "do things")
    results = [e.content for e in events if isinstance(e, ToolResultEvent)]
    assert results[0] == "Error: unknown tool 'does_not_exist'"
    assert results[1].startswith("Error: invalid arguments")
    assert results[2] == "Error: the path is outside the workspace"
    assert isinstance(events[-1], DoneEvent) and events[-1].content == "I could not do that."


async def test_iteration_limit_ends_with_an_explicit_message(services: Services, llm_ready: FakeLLM) -> None:
    services.config.update({"MAX_ITERATIONS": 2})
    llm_ready.always = ToolCall("session_search", {"query": "anything"})
    events = await run(services, "loop forever")
    assert isinstance(events[-1], DoneEvent) and "stopped after 2 tool rounds" in events[-1].content
    assert len(llm_ready.requests) == 2
    assert services.store.messages("cli:1")[-1]["content"] == events[-1].content


async def test_ui_result_is_shortened_but_the_model_gets_everything(services: Services, llm_ready: FakeLLM) -> None:
    (services.settings.data_dir / "workspace" / "a.txt").write_text("q" * 3000)
    llm_ready.queue(ToolCall("file_read", {"path": "a.txt"}), Text("done"))
    events = await run(services, "read it")
    shown = next(e for e in events if isinstance(e, ToolResultEvent))
    assert len(shown.content) == 500
    assert len(llm_ready.requests[1].body["messages"][-1]["content"]) == 3000


async def test_history_window_never_starts_with_an_orphan_tool_result(services: Services, llm_ready: FakeLLM) -> None:
    llm_ready.queue(ToolCall("session_search", {"query": "x"}), Text("first answer"))
    await run(services, "first question")
    services.config.update({"HISTORY_LIMIT": 3})  # window would start at the tool result
    await run(services, "second question")
    sent = llm_ready.requests[-1].body["messages"]
    assert [m["role"] for m in sent] == ["system", "assistant", "user"] or [m["role"] for m in sent] == ["system", "user"]
    assert sent[1]["role"] != "tool"
    assert sent[-1]["content"] == "second question"


async def test_system_prompt_is_frozen_until_idle(services: Services, llm_ready: FakeLLM) -> None:
    now = {"t": 1_000_000.0}
    clock_dt = datetime(2026, 10, 25, 14, 3, tzinfo=timezone.utc)
    agent = Agent(services, clock=lambda: now["t"], now=lambda: clock_dt)
    services.memory.add("user", "Lives in Bologna")
    services.skills.save("backup", "Back up the NAS", "steps")

    async def system_prompt(text: str) -> str:
        async for _ in agent.run("cli:1", text, "cli"):
            pass
        return str(llm_ready.requests[-1].body["messages"][0]["content"])

    first = await system_prompt("one")
    assert "Lives in Bologna" in first and "Your notes (MEMORY) — 0 / 2,200" in first
    assert "User profile (USER) — 16 / 1,375" in first
    assert "Sunday 2026-10-25 14:03" in first
    assert "- backup: Back up the NAS" in first and "untrusted data" in first and "Reply in the language" in first

    services.memory.add("user", "Prefers tea")
    services.skills.save("late", "Added later", "steps")
    now["t"] += 60 * 29  # inside IDLE_MINUTES (30): the prompt prefix stays byte-identical
    assert await system_prompt("two") == first
    now["t"] += 60 * 29  # 29 minutes after the last activity: still frozen, activity extends the window
    assert await system_prompt("three") == first
    now["t"] += 60 * 31  # idle for 31 minutes: rebuilt
    rebuilt = await system_prompt("four")
    assert "Prefers tea" in rebuilt and "- late: Added later" in rebuilt
    agent.reset_prompt("cli:1")  # /new
    assert agent._prompts.get("cli:1") is None


async def test_no_provider_gives_a_pointer_to_the_dashboard(services: Services) -> None:
    events = await run(services, "hello")
    assert types(events) == ["error"] and "dashboard" in events[0].message  # type: ignore[attr-defined]
    reply = await services.agent.reply("cli:2", "hello", "cli")
    assert "dashboard" in reply and "Models" in reply


async def test_model_failures_are_reported_not_raised(services: Services, llm_ready: FakeLLM) -> None:
    llm_ready.queue(Status(402))
    events = await run(services, "hello")
    assert types(events) == ["error"] and "quota exhausted" in events[0].message  # type: ignore[attr-defined]
    llm_ready.queue(Status(400, {"error": {"message": "content policy"}}))
    assert "content policy" in await services.agent.reply("cli:3", "hi", "cli")
    assert [m["role"] for m in services.store.messages("cli:1")] == ["user"]  # no assistant message for a failed turn


async def test_empty_message_is_rejected(services: Services, llm_ready: FakeLLM) -> None:
    assert types(await run(services, "   ")) == ["error"] and llm_ready.requests == []


async def test_reply_returns_the_final_text(services: Services, llm_ready: FakeLLM) -> None:
    llm_ready.queue(ToolCall("session_search", {"query": "x"}), Text("final words"))
    assert await services.agent.reply("cli:1", "q", "cli") == "final words"


async def test_midstream_failover_emits_reset_and_notice(services: Services, fake_llm: Any, llm_ready: FakeLLM) -> None:
    second = fake_llm()
    p = services.providers.create({"slug": "second", "type": "local", "base_url": second.url})
    services.providers.save_models(p.id, [{"model_id": "test-model"}])
    services.store.model_set_test(p.id, "test-model", True)
    services.llm.set_route("default", [{"provider": "fake", "model": "test-model"}, {"provider": "second", "model": "test-model"}])
    llm_ready.queue(Break("par"))
    second.queue(Text("complete answer", chunks=1))
    events = await run(services, "hello")
    assert types(events) == ["token", "reset", "token", "notice", "done"]  # the switch is announced once the answer is complete
    assert "Switched to second/test-model" in events[3].text  # type: ignore[attr-defined]
    llm_ready.queue(Status(503))
    services.config.update({"NOTIFICATIONS": "off"})
    events = await run(services, "again", session="cli:9")
    assert "notice" not in types(events) and isinstance(events[-1], DoneEvent)


async def test_per_session_thinking_and_route_overrides(services: Services, llm_ready: FakeLLM) -> None:
    services.providers.save_models(services.providers.by_slug("fake").id, [{"model_id": "test-model", "thinking_control": "qwen_template"}])  # type: ignore[union-attr]
    agent = services.agent
    await run(services)
    assert llm_ready.requests[-1].body["chat_template_kwargs"] == {"enable_thinking": False}
    agent.set_thinking("cli:1", True)
    assert agent.thinking("cli:1") is True
    await run(services, "again")
    assert llm_ready.requests[-1].body["chat_template_kwargs"] == {"enable_thinking": True}
    assert agent.thinking("cli:2") is None  # per session
    agent.set_thinking("cli:1", None)
    assert agent.thinking("cli:1") is None

    assert agent.route_spec("cli:1", "cli") == "default"
    services.config.update({"ROUTE_TELEGRAM": "review"})
    assert agent.route_spec("tg:1:1", "telegram") == "review"
    agent.set_route("cli:1", "fake/test-model")
    assert agent.route_spec("cli:1", "cli") == "fake/test-model"
    await run(services, "pinned")
    assert llm_ready.requests[-1].body["model"] == "test-model"
    agent.set_route("cli:1", "nowhere/model")
    events = await run(services, "bad pin")
    assert types(events) == ["error"] and "/model" in events[0].message  # type: ignore[attr-defined]
    agent.set_route("cli:1", None)
    assert agent.route_spec("cli:1", "cli") == "default"


async def test_toolsets_control_what_the_model_sees(services: Services, llm_ready: FakeLLM) -> None:
    services.config.update({"TOOLSETS": "memory", "TOOLSETS_WEB": []})  # an explicit empty list disables everything; "" would inherit
    await run(services)
    assert [t["function"]["name"] for t in llm_ready.requests[-1].body["tools"]] == ["memory"]
    await run(services, "web chat", session="web:abc", source="web")
    assert "tools" not in llm_ready.requests[-1].body  # nothing enabled: no tools key at all
    assert "memory tool" not in llm_ready.requests[-1].body["messages"][0]["content"]


async def test_restricted_callers_see_no_memory_and_cannot_use_it(services: Services, llm_ready: FakeLLM) -> None:
    services.config.update({"TOOLSETS": "memory,search,skills,web,file,terminal"})
    services.memory.add("user", "SECRET-PROFILE-FACT")
    services.memory.add("memory", "SECRET-NOTE")
    services.skills.save("public-skill", "Something harmless", "body")
    llm_ready.queue(ToolCall("memory", {"action": "add", "content": "poison"}), ToolCall("terminal", {"command": "id"}), Text("ok"))
    events = await run(services, "hi", session="tg:7:1", source="telegram", restricted=True)
    body = llm_ready.requests[0].body
    prompt = body["messages"][0]["content"]
    assert "SECRET" not in prompt and "no access to the owner's memory" in prompt and "- public-skill: Something harmless" in prompt
    assert {t["function"]["name"] for t in body["tools"]} == {"session_search", "skill_view", "web_fetch"}
    results = [e.content for e in events if isinstance(e, ToolResultEvent)]
    assert results == ["Error: unknown tool 'memory'", "Error: unknown tool 'terminal'"]
    assert services.memory.snapshot("memory").entries == ["SECRET-NOTE"]


async def test_turns_in_one_session_are_serialised(services: Services, llm_ready: FakeLLM) -> None:
    llm_ready.queue(Text("answer one"), Text("answer two"))
    await asyncio.gather(run(services, "question one", session="web:s"), run(services, "question two", session="web:s"))
    roles = [(m["role"], m["content"]) for m in services.store.messages("web:s")]
    assert [r for r, _ in roles] == ["user", "assistant", "user", "assistant"]  # never user, user, assistant, assistant
    assert roles[0][1].startswith("question") and roles[2][1].startswith("question")


async def test_different_sessions_run_concurrently(services: Services, llm_ready: FakeLLM) -> None:
    from tests.fake_llm import Silence

    llm_ready.queue(Silence(0.6), Text("quick"))
    started = asyncio.get_running_loop().time()
    slow = asyncio.create_task(run(services, "slow one", session="cli:a"))
    await asyncio.sleep(0.1)
    quick = await run(services, "quick one", session="cli:b")
    assert asyncio.get_running_loop().time() - started < 0.55 and isinstance(quick[-1], DoneEvent)
    await slow


async def test_closing_the_generator_releases_the_session_lock(services: Services, llm_ready: FakeLLM) -> None:
    llm_ready.queue(Text("abcdef", chunks=6), Text("next"))
    gen = services.agent.run("cli:1", "first", "cli")
    await gen.__anext__()
    await gen.aclose()  # client disconnected mid-answer
    events = await asyncio.wait_for(run(services, "second"), 5)
    assert isinstance(events[-1], DoneEvent)
