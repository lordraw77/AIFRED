"""Sub-agents: definitions, the intersection rule, runs, limits, delegation tools, commands and API."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from aifred.agents import AgentError
from aifred.app import Services
from aifred.commands import Commands, Context
from aifred.config import Settings
from aifred.tools.registry import ToolContext
from tests.conftest import login
from tests.fake_llm import FakeLLM, Silence, Text, ToolCall

pytestmark = pytest.mark.anyio

RESEARCH = {"description": "Researches on the web", "role": "You are a researcher.", "toolsets": ["web", "search"], "enabled": True}


def caller(services: Services, source: str = "web", session: str = "web:main", restricted: bool = False) -> ToolContext:
    return ToolContext(services, session, source, restricted)


def tool_names(fake: FakeLLM, index: int = 0) -> set[str]:
    return {t["function"]["name"] for t in fake.requests[index].body.get("tools") or []}


def system_prompt(fake: FakeLLM, index: int = 0) -> str:
    return str(fake.requests[index].body["messages"][0]["content"])


@pytest.fixture
def ready(services: Services, llm_ready: FakeLLM) -> Services:
    services.config.update({"TOOLSETS": ["memory", "search", "skills", "web", "file", "agents"]})
    return services


# Definitions


def test_create_update_delete(services: Services, llm_ready: FakeLLM) -> None:
    a = services.agents.save("researcher", RESEARCH)
    assert a["enabled"] and a["toolsets"] == ["search", "web"] and a["memory"] == "none" and a["route"] == "default" and a["max_iterations"] is None
    b = services.agents.save("researcher", {"memory": "read", "max_iterations": 4, "timeout": None})
    assert b["memory"] == "read" and b["max_iterations"] == 4 and b["role"] == "You are a researcher."
    services.agents.delete("researcher")
    with pytest.raises(AgentError, match="no agent"):
        services.agents.delete("researcher")
    with pytest.raises(AgentError, match="no agent"):
        services.agents.get("researcher")


@pytest.mark.parametrize("name, values, why", [
    ("Bad Name", RESEARCH, "name must be"),
    ("a", {**RESEARCH, "description": ""}, "description"),
    ("a", {**RESEARCH, "role": "x" * 4001}, "role"),
    ("a", {**RESEARCH, "description": "x" * 241}, "description"),
    ("a", {**RESEARCH, "memory": "all"}, "memory must be"),
    ("a", {**RESEARCH, "toolsets": ["nonsense"]}, "unknown"),
    ("a", {**RESEARCH, "toolsets": ["agents"]}, "not allowed"),
    ("a", {**RESEARCH, "toolsets": ["cron"]}, "not allowed"),
    ("a", {**RESEARCH, "toolsets": ["review"]}, "not allowed"),
    ("a", {**RESEARCH, "skills": ["ghost"]}, "no skill"),
    ("a", {**RESEARCH, "max_iterations": 0.5}, "whole number"),
    ("a", {**RESEARCH, "max_iterations": 1000}, "whole number"),
    ("a", {**RESEARCH, "timeout": 5}, "whole number"),
    ("a", {**RESEARCH, "token_budget": 10}, "whole number"),
    ("a", {"description": "d"}, "role is required"),
])
def test_validation(services: Services, llm_ready: FakeLLM, name: str, values: dict, why: str) -> None:
    with pytest.raises(AgentError, match=why):
        services.agents.save(name, values)
    assert services.store.agent_list() == []


def test_enabling_needs_a_usable_route(services: Services) -> None:
    with pytest.raises(AgentError, match="cannot be used"):
        services.agents.save("a", RESEARCH)  # no provider yet
    services.agents.save("a", {**RESEARCH, "enabled": False})
    with pytest.raises(AgentError, match="cannot be used"):
        services.agents.save("a", {"enabled": True})
    assert services.store.agent_get("a")["enabled"] is False


def test_agent_limit(services: Services) -> None:
    services.config.update({"AGENT_MAX_AGENTS": 2})
    for n in ("a", "b"):
        services.agents.save(n, {**RESEARCH, "enabled": False})
    with pytest.raises(AgentError, match="AGENT_MAX_AGENTS"):
        services.agents.save("c", {**RESEARCH, "enabled": False})


def test_starters_are_disabled_and_created_once(services: Services) -> None:
    services.agents.ensure_starters()
    rows = {r["name"]: r for r in services.store.agent_list()}
    assert set(rows) == {"researcher", "sysadmin", "writer"} and not any(r["enabled"] for r in rows.values())
    services.agents.delete("writer")
    services.agents.ensure_starters()
    assert services.store.agent_get("writer") is None  # deleted stays deleted


def test_a_skill_can_be_preloaded(services: Services, llm_ready: FakeLLM) -> None:
    services.skills.save("deploy", "How to deploy", "Run the deploy script, then check the health page.")
    assert services.agents.save("a", {**RESEARCH, "skills": ["deploy"]})["skills"] == ["deploy"]


# The intersection rule


def effective(services: Services, spec_toolsets: list[str], memory: str = "none", **caller_args: object) -> frozenset[str]:
    services.agents.save("a", {**RESEARCH, "toolsets": [t for t in spec_toolsets if t != "memory"], "memory": memory, "enabled": False})
    spec = services.store.agent_get("a")
    return services.agents.effective_toolsets(spec, caller(services, **caller_args))  # type: ignore[arg-type]


def test_intersection_with_the_calling_gateway(services: Services) -> None:
    services.config.update({"TOOLSETS": ["web", "file"], "TOOLSETS_WEB": ["web"]})
    assert effective(services, ["web", "file", "terminal"]) == {"web", "help"}  # the web chat only has web
    services.config.update({"TOOLSETS_WEB": ["web", "file", "terminal"]})
    assert effective(services, ["web", "file", "terminal"]) == {"web", "file", "terminal", "help"}
    assert effective(services, ["web"]) == {"web", "help"}  # and never more than the agent asked for


def test_never_agents_cron_or_review(services: Services) -> None:
    services.config.update({"TOOLSETS_WEB": ["web", "agents", "cron", "memory"]})
    assert effective(services, ["web"]) == {"web", "help"}
    # the definition cannot hold them, so force them into the database to prove a run still refuses
    services.store._conn.execute("UPDATE agents SET toolsets = ? WHERE name = 'a'", (json.dumps(["web", "agents", "cron", "review"]),))
    forced = services.agents.effective_toolsets(services.store.agent_get("a"), caller(services))
    assert forced == {"web", "help"}


def test_memory_modes(services: Services) -> None:
    services.config.update({"TOOLSETS_WEB": ["web", "memory"]})
    assert "memory" not in effective(services, ["web"], "none") and "memory" not in effective(services, ["web"], "read")
    assert "memory" in effective(services, ["web"], "write")
    services.config.update({"TOOLSETS_WEB": ["web"]})
    assert "memory" not in effective(services, ["web"], "write")  # the caller cannot write memory either


def test_mcp_toolsets_intersect(services: Services) -> None:
    services.config.update({"TOOLSETS_WEB": ["mcp:linux"]})
    assert effective(services, ["mcp"]) == {"help"}  # no such server tools are registered here, but nothing wider is granted


# Runs


async def test_a_run_uses_role_task_context_and_limited_tools(ready: Services, llm_ready: FakeLLM) -> None:
    ready.config.update({"TOOLSETS_WEB": ["web", "search", "file", "agents", "memory"]})
    ready.agents.save("researcher", {**RESEARCH, "toolsets": ["web", "file"]})
    llm_ready.queue(Text("The answer is 42."))
    run = await ready.agents.run("researcher", "What is the answer?", "Asked by Anna", caller(ready))
    assert run["status"] == "ok" and run["output"] == "The answer is 42." and run["tokens"] >= 0
    request = llm_ready.requests[0].body["messages"]
    assert request[0]["content"].startswith("You are a researcher.") and "sub-agent 'researcher'" in request[0]["content"]
    assert request[1]["content"] == "What is the answer?\n\nContext:\nAsked by Anna" and len(request) == 2  # no history of the caller
    names = tool_names(llm_ready)
    assert "web_fetch" in names and "file_read" in names
    assert not names & {"memory", "delegate", "delegate_many", "cronjob", "session_search", "terminal", "skill_save"}


async def test_the_run_is_a_child_session_and_is_recorded(ready: Services, llm_ready: FakeLLM) -> None:
    ready.agents.save("researcher", RESEARCH)
    llm_ready.queue(Text("done"))
    run = await ready.agents.run("researcher", "go", "", caller(ready, session="web:parent"))
    session = ready.store.get_session(run["session_id"])
    assert session["source"] == "sub:researcher" and session["parent_id"] == "web:parent" and session["message_count"] == 2
    row = ready.store.agent_runs("researcher")[0]
    assert (row["status"], row["parent_session"], row["task"], row["output"]) == ("ok", "web:parent", "go", "done")
    usage = ready.store._query("SELECT agent FROM llm_usage")
    assert usage and usage[-1]["agent"] == "sub:researcher"


async def test_it_runs_on_its_own_route(ready: Services, llm_ready: FakeLLM, fake_llm: Callable[[], FakeLLM]) -> None:
    other = fake_llm()
    provider = ready.providers.create({"slug": "cheap", "type": "local", "base_url": other.url})
    ready.providers.save_models(provider.id, [{"model_id": "small"}])
    ready.store.model_set_test(provider.id, "small", True)
    ready.agents.save("researcher", {**RESEARCH, "route": "cheap/small"})
    other.queue(Text("from the cheap model"))
    run = await ready.agents.run("researcher", "go", "", caller(ready))
    assert run["output"] == "from the cheap model" and llm_ready.requests == [] and other.requests[0].body["model"] == "small"


async def test_unknown_disabled_and_empty_requests_are_refused(ready: Services, llm_ready: FakeLLM) -> None:
    ready.agents.save("off", {**RESEARCH, "enabled": False})
    ready.agents.save("on", RESEARCH)
    for name, task, why in [("ghost", "x", "no agent"), ("off", "x", "switched off"), ("on", "  ", "give a task"), ("on", "x" * 8001, "give a task")]:
        with pytest.raises(AgentError, match=why):
            await ready.agents.run(name, task, "", caller(ready))
    assert llm_ready.requests == []


async def test_open_chats_and_sub_agents_cannot_delegate(ready: Services, llm_ready: FakeLLM) -> None:
    ready.agents.save("a", RESEARCH)
    with pytest.raises(AgentError, match="open chat"):
        await ready.agents.run("a", "x", "", caller(ready, "telegram", "tg:9:1", restricted=True))
    with pytest.raises(AgentError, match="another one"):
        await ready.agents.run("a", "x", "", caller(ready, "sub:a", "sub:a:1"))
    assert "delegate" not in {t.name for t in ready.tools.for_context(caller(ready, "sub:a", "sub:a:1"))}
    assert "delegate" in {t.name for t in ready.tools.for_context(caller(ready))}
    assert "delegate" not in {t.name for t in ready.tools.for_context(caller(ready, "telegram", "tg:9:1", restricted=True))}


async def test_iteration_limit_and_failure_modes(ready: Services, llm_ready: FakeLLM) -> None:
    ready.agents.save("a", {**RESEARCH, "max_iterations": 2})
    llm_ready.queue(ToolCall("session_search", {"query": "x"}), ToolCall("session_search", {"query": "y"}), Text("never"))
    run = await ready.agents.run("a", "loop", "", caller(ready))
    assert run["status"] == "ok" and "stopped after 2 tool rounds" in run["output"]
    assert len(llm_ready.requests) == 2


async def test_token_budget(ready: Services, llm_ready: FakeLLM) -> None:
    ready.agents.save("a", {**RESEARCH, "token_budget": 1000})
    llm_ready.queue(ToolCall("session_search", {"query": "x"}, usage=(900, 200)), Text("never"))
    run = await ready.agents.run("a", "loop", "", caller(ready))
    assert run["status"] == "ok" and "token budget of 1,000" in run["output"] and len(llm_ready.requests) == 1


async def test_timeout_and_no_provider(ready: Services, llm_ready: FakeLLM) -> None:
    ready.agents.save("a", RESEARCH)
    ready.store.agent_save("a", {"timeout": 10})
    llm_ready.queue(Silence(30))
    original = asyncio.wait_for

    async def short(awaitable, timeout):  # noqa: ANN001
        return await original(awaitable, 0.3)

    asyncio.wait_for = short  # type: ignore[assignment]
    try:
        run = await ready.agents.run("a", "go", "", caller(ready))
    finally:
        asyncio.wait_for = original  # type: ignore[assignment]
    assert run["status"] == "error" and "longer than 10 s" in run["error"]
    assert ready.store.agent_runs("a")[0]["status"] == "error"


async def test_a_failing_model_is_reported_not_raised(services: Services) -> None:
    services.store.agent_save("a", {**RESEARCH, "toolsets": ["web"], "enabled": True})
    run = await services.agents.run("a", "go", "", caller(services))
    assert run["status"] == "error" and "provider" in run["error"].lower()


def test_run_history_is_trimmed_and_interrupted_runs_closed(services: Services) -> None:
    store = services.store
    for _ in range(5):
        store.agent_run_finish(store.agent_run_start("a", "p", "s", "t"), "ok", "o", keep=3)
    assert len(store.agent_runs("a", 100)) == 3
    store.agent_run_start("a", "p", "s", "t")
    assert services.agents.interrupted() == 1 and store.agent_runs("a")[0]["status"] == "error"


async def test_memory_in_the_prompt_follows_the_mode(ready: Services, llm_ready: FakeLLM) -> None:
    ready.memory.add("memory", "The NAS is called vault")
    ready.config.update({"TOOLSETS_WEB": ["web", "memory"]})
    for mode, in_prompt, has_tool in (("none", False, False), ("read", True, False), ("write", True, True)):
        ready.agents.save("a", {**RESEARCH, "toolsets": ["web"], "memory": mode})
        llm_ready.queue(Text("ok"))
        before = len(llm_ready.requests)
        await ready.agents.run("a", f"go {mode}", "", caller(ready))
        assert ("The NAS is called vault" in system_prompt(llm_ready, before)) is in_prompt, mode
        assert ("memory" in tool_names(llm_ready, before)) is has_tool, mode


async def test_preloaded_skill_and_capabilities_are_in_the_prompt(ready: Services, llm_ready: FakeLLM) -> None:
    ready.skills.save("deploy", "How to deploy", "Run the deploy script, then check the health page.")
    ready.instructions.set("toolset:web", "Use the web tools for anything that needs current information.")
    ready.agents.save("a", {**RESEARCH, "skills": ["deploy"]})
    llm_ready.queue(Text("ok"))
    await ready.agents.run("a", "go", "", caller(ready))
    prompt = system_prompt(llm_ready)
    assert "## Skill: deploy" in prompt and "Run the deploy script" in prompt and "Use the web tools for anything" in prompt
    assert "untrusted data" in prompt


# Delegation through the model


async def test_the_model_delegates_and_uses_the_answer(ready: Services, llm_ready: FakeLLM) -> None:
    ready.agents.save("researcher", RESEARCH)
    llm_ready.queue(
        ToolCall("delegate", {"agent": "researcher", "task": "Find the capital of Peru", "context": "geography quiz"}),
        Text("Lima."),  # the sub-agent
        Text("The researcher says: Lima."),  # the main agent
    )
    answer = await ready.agent.reply("web:main", "What is the capital of Peru?", "web")
    assert answer == "The researcher says: Lima."
    assert "researcher: Researches on the web" in system_prompt(llm_ready, 0)  # the main agent is told what exists
    final = llm_ready.requests[2].body["messages"]
    assert final[-1]["role"] == "tool" and final[-1]["content"] == "Lima."
    assert ready.store.agent_runs("researcher")[0]["parent_session"] == "web:main"


async def test_only_enabled_agents_are_offered(ready: Services, llm_ready: FakeLLM) -> None:
    ready.agents.save("on", RESEARCH)
    ready.agents.save("off", {**RESEARCH, "description": "Hidden helper", "enabled": False})
    llm_ready.queue(Text("hi"))
    await ready.agent.reply("web:main", "hello", "web")
    prompt = system_prompt(llm_ready)
    assert "- on:" in prompt and "Hidden helper" not in prompt


async def test_no_agent_section_without_the_toolset(services: Services, llm_ready: FakeLLM) -> None:
    services.agents.save("on", RESEARCH)
    llm_ready.queue(Text("hi"))
    await services.agent.reply("web:main", "hello", "web")
    assert "Sub-agents" not in system_prompt(llm_ready) and "delegate" not in tool_names(llm_ready)


async def test_delegate_errors_reach_the_model(ready: Services, llm_ready: FakeLLM) -> None:
    llm_ready.queue(ToolCall("delegate", {"agent": "ghost", "task": "x"}), Text("ok"))
    await ready.agent.reply("web:main", "hello", "web")
    assert llm_ready.requests[1].body["messages"][-1]["content"] == "Error: no agent named 'ghost'"


async def test_delegate_many_runs_in_order_and_isolates_failures(ready: Services, llm_ready: FakeLLM) -> None:
    ready.agents.save("a", RESEARCH)
    ready.agents.save("b", {**RESEARCH, "description": "Second"})
    llm_ready.queue(
        ToolCall("delegate_many", {"jobs": [{"agent": "a", "task": "one"}, {"agent": "ghost", "task": "two"}, {"agent": "b", "task": "three"}]}),
        Text("answer-1"), Text("answer-3"), Text("final"),
    )
    answer = await ready.agent.reply("web:main", "do three things", "web")
    assert answer == "final"
    result = llm_ready.requests[-1].body["messages"][-1]["content"]
    assert result.index("### a") < result.index("### ghost") < result.index("### b")
    assert "Error: the sub-agent 'ghost' failed: no agent named 'ghost'" in result and "answer-1" in result and "answer-3" in result


async def test_parallel_limit(ready: Services, llm_ready: FakeLLM, monkeypatch: pytest.MonkeyPatch) -> None:
    ready.config.update({"AGENT_MAX_PARALLEL": 2})
    ready.agents.save("a", RESEARCH)
    active = peak = 0

    async def tracked(*args: object, **kwargs: object) -> dict:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.05)
        active -= 1
        return {"agent": "a", "status": "ok", "output": "x", "error": ""}

    monkeypatch.setattr(ready.agents, "run", tracked)
    await ready.agents.run_many([("a", "t", "")] * 6, caller(ready))
    assert peak == 2


# Commands


async def test_agent_commands(ready: Services, llm_ready: FakeLLM) -> None:
    cmds = Commands(ready)

    async def run(text: str, c: Context | None = None) -> str:
        reply = await cmds.run(text, c or Context("cli:1", "cli"))
        assert reply is not None
        return reply.text

    assert "No sub-agents" in await run("/agents")
    ready.agents.save("researcher", RESEARCH)
    assert "on  researcher  [default]  Researches on the web" in await run("/agents")
    llm_ready.queue(Text("Lima."))
    assert await run("/agent researcher Find the capital of Peru") == "Lima."
    assert "Usage" in await run("/agent researcher") and "Error: no agent" in await run("/agent ghost do it")
    assert "not available" in await run("/agent researcher x", Context("tg:9:1", "telegram", restricted=True))
    assert "not available" in await run("/agents", Context("tg:9:1", "telegram", restricted=True))
    assert "/agent" in await run("/help")


# API


@pytest.fixture
def api(settings: Settings) -> TestClient:
    from aifred.app import create_app

    with TestClient(create_app(settings), base_url="https://testserver") as c:
        c.headers["X-CSRF-Token"] = login(c)
        yield c


def test_api_lifecycle(api: TestClient) -> None:
    listing = api.get("/api/agents").json()
    assert {a["name"] for a in listing["agents"]} == {"researcher", "sysadmin", "writer"} and "web" in listing["toolsets"] and "agents" not in listing["toolsets"]
    assert listing["memory_modes"] == ["none", "read", "write"] and listing["defaults"] == {"max_iterations": 8, "timeout": 300}
    made = api.post("/api/agents", json={"name": "ops", "description": "Ops helper", "role": "You do ops.", "toolsets": ["web"]})
    assert made.status_code == 201 and made.json()["enabled"] is False
    assert api.post("/api/agents", json={"name": "ops", "description": "d", "role": "r"}).status_code == 422
    assert api.post("/api/agents", json={"name": "x", "description": "d"}).status_code == 422
    assert api.put("/api/agents/ops", json={"role": "New role", "max_iterations": 3}).json()["role"] == "New role"
    assert api.put("/api/agents/ops", json={"enabled": True}).status_code == 422  # no model yet
    assert api.put("/api/agents/ops", json={"toolsets": ["agents"]}).status_code == 422
    assert api.put("/api/agents/ghost", json={"role": "r"}).status_code == 404
    assert api.post("/api/agents/ops/run", json={"task": "x"}).status_code == 422  # switched off
    assert api.post("/api/agents/ghost/run", json={"task": "x"}).status_code == 404
    assert api.get("/api/agents/ops/runs").json() == [] and api.get("/api/agents/ghost/runs").status_code == 404
    assert api.delete("/api/agents/ops").status_code == 200 and api.delete("/api/agents/ops").status_code == 404


def test_api_needs_admin(api: TestClient) -> None:
    anonymous = TestClient(api.app, base_url="https://testserver")
    for method, path in [("get", "/api/agents"), ("post", "/api/agents"), ("put", "/api/agents/x"), ("delete", "/api/agents/x"), ("post", "/api/agents/x/run"), ("get", "/api/agents/x/runs")]:
        assert getattr(anonymous, method)(path).status_code in (401, 403, 422), path


def test_api_run(api: TestClient, fake_llm: Callable[[], FakeLLM]) -> None:
    services: Services = api.app.state.services  # type: ignore[attr-defined]
    fake = fake_llm()
    provider = services.providers.create({"slug": "fake", "type": "local", "base_url": fake.url})
    services.providers.save_models(provider.id, [{"model_id": "m"}])
    services.store.model_set_test(provider.id, "m", True)
    services.llm.set_route("default", [{"provider": "fake", "model": "m"}])
    assert api.put("/api/agents/researcher", json={"enabled": True}).json()["enabled"] is True
    fake.queue(Text("api answer"))
    run = api.post("/api/agents/researcher/run", json={"task": "say something"}).json()
    assert run["status"] == "ok" and run["output"] == "api answer"
    assert api.get("/api/agents/researcher/runs").json()[0]["output"] == "api answer"
