"""Tests for aifred.router: route walking, failover, cooldowns, health and usage."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pytest

from aifred.app import Services
from aifred.llm import LLMError, Outcome, Token
from aifred.providers import Provider, ProviderError
from aifred.router import (
    Completed,
    Entry,
    LLMRouter,
    NoProvider,
    Notice,
    Reset,
    RouteError,
    RouteExhausted,
)
from tests.fake_llm import Break, FakeLLM, Silence, Status, Text

pytestmark = pytest.mark.anyio

T0 = 1_000_000.0  # a UTC day boundary (multiple of 86400)
QUOTA_BODY = {"error": {"message": "You exceeded your current quota, check your billing"}}


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@dataclass
class Env:
    services: Services
    make_fake: Callable[[], FakeLLM]
    clock: Clock = field(default_factory=Clock)
    sleeps: list[float] = field(default_factory=list)
    fakes: dict[str, FakeLLM] = field(default_factory=dict)
    router: LLMRouter = field(init=False)

    def __post_init__(self) -> None:
        async def fake_sleep(seconds: float) -> None:
            self.sleeps.append(seconds)

        s = self.services
        self.router = LLMRouter(s.store, s.vault, s.config, s.providers, clock=self.clock, sleep=fake_sleep)

    def add(
        self, slug: str, *, keys: tuple[str, ...] = (), model: str = "test-model", context: int | None = None,
        ptype: str = "local", control: str = "none", **prov: Any,
    ) -> tuple[Provider, FakeLLM]:
        fake = self.make_fake()
        fake.models = [{"id": model}]
        self.fakes[slug] = fake
        p = self.services.providers.create({"slug": slug, "type": ptype, "base_url": fake.url, **prov})
        for k in keys:
            self.services.providers.add_key(p.id, k, "")
        values: dict[str, Any] = {"model_id": model, "thinking_control": control}
        if context:
            values["context_window"] = context
        self.services.providers.save_models(p.id, [values])
        self.services.store.model_set_test(p.id, model, True)
        return p, fake
    def route(self, *slugs: str, name: str = "default", model: str = "test-model") -> None:
        self.services.llm.set_route(name, [{"provider": s, "model": model} for s in slugs])

    async def ask(self, route: str = "default", **kw: Any) -> list[Any]:
        entries = self.router.resolve(route)
        events: list[Any] = []
        async for ev in self.router.chat(entries, [{"role": "user", "content": "hi"}], route=route, **kw):
            events.append(ev)
        return events

    def health(self, kind: str, ref: str) -> dict[str, Any] | None:
        return self.services.store.health_get(kind, ref)


@pytest.fixture
def env(services: Services, fake_llm: Callable[[], FakeLLM]) -> Env:
    return Env(services, fake_llm)


def completed(events: list[Any]) -> Completed:
    assert isinstance(events[-1], Completed), events
    return events[-1]


def notices(events: list[Any]) -> list[str]:
    return [e.text for e in events if isinstance(e, Notice)]


async def test_primary_success_records_usage(env: Env) -> None:
    env.add("a")
    env.route("a")
    events = await env.ask(session_key="s1", source="cli")
    done = completed(events)
    assert done.result.content == "ok" and done.entry.label == "a/test-model"
    assert notices(events) == [] and [e.text for e in events if isinstance(e, Token)] == ["o", "k"]
    [row] = env.services.store.usage_report(0)
    assert (row["provider"], row["model"], row["calls"], row["tokens_in"], row["tokens_out"]) == ("a", "test-model", 1, 10, 5)
    raw = env.services.store._query("SELECT * FROM llm_usage")[0]
    assert raw["outcome"] == "success" and raw["source"] == "cli" and raw["route"] == "default" and raw["fallback"] == 0


async def test_quota_exhaustion_switches_announces_once_and_is_not_sticky(env: Env) -> None:
    pa, fa = env.add("a")
    _, fb = env.add("b")
    env.route("a", "b")
    fa.queue(Status(402))
    events = await env.ask(session_key="s1")
    assert completed(events).entry.label == "b/test-model"
    assert notices(events) == ["Switched to b/test-model (a: quota exhausted)"]
    assert env.health("entry", f"{pa.id}|test-model")["until"] == T0 + 3600  # COOLDOWN_QUOTA
    assert env.services.store._query("SELECT fallback FROM llm_usage WHERE outcome = 'success'") == [{"fallback": 1}]

    again = await env.ask(session_key="s1")  # primary is cooling: skipped, not contacted
    assert completed(again).entry.label == "b/test-model" and len(fa.requests) == 1
    assert notices(again) == []  # announced once

    env.clock.advance(3601)  # cooldown over: the primary is tried first again
    back = await env.ask(session_key="s1")
    assert completed(back).entry.label == "a/test-model" and len(fa.requests) == 2
    assert env.health("entry", f"{pa.id}|test-model") is None
    assert len(fb.requests) == 2


async def test_quota_tries_next_key_before_next_entry(env: Env) -> None:
    pa, fa = env.add("a", keys=("key-one-0001", "key-two-0002"))
    _, fb = env.add("b")
    env.route("a", "b")
    fa.queue(Status(429, QUOTA_BODY))
    events = await env.ask()
    assert completed(events).entry.label == "a/test-model" and notices(events) == []
    assert [r.headers["authorization"] for r in fa.requests] == ["Bearer key-one-0001", "Bearer key-two-0002"]
    assert fb.requests == []
    k1, k2 = env.services.store.key_list(pa.id)
    assert env.health("key", str(k1["id"]))["reason"] == "quota exhausted" and env.health("key", str(k2["id"])) is None

    await env.ask()  # key one is cooling, goes straight to key two
    assert [r.headers["authorization"] for r in fa.requests][-1] == "Bearer key-two-0002" and len(fa.requests) == 3


async def test_pinned_key_is_used(env: Env) -> None:
    pa, fa = env.add("a", keys=("key-one-0001", "key-two-0002"))
    _, k2 = env.services.store.key_list(pa.id)
    env.services.llm.set_route("default", [{"provider": "a", "model": "test-model", "key_id": k2["id"]}])
    await env.ask()
    assert fa.requests[0].headers["authorization"] == "Bearer key-two-0002"


async def test_every_key_exhausted_moves_to_next_entry(env: Env) -> None:
    _, fa = env.add("a", keys=("key-one-0001", "key-two-0002"))
    _, fb = env.add("b")
    env.route("a", "b")
    fa.queue(Status(402), Status(402))
    events = await env.ask()
    assert completed(events).entry.label == "b/test-model" and len(fa.requests) == 2


async def test_short_rate_limit_waits_and_retries_same_entry(env: Env) -> None:
    _, fa = env.add("a")
    _, fb = env.add("b")
    env.route("a", "b")
    fa.queue(Status(429, {"error": {"message": "slow down"}}, {"Retry-After": "2"}), Text("after the wait"))
    events = await env.ask()
    assert env.sleeps == [2.0] and completed(events).result.content == "after the wait"
    assert len(fa.requests) == 2 and fb.requests == []


async def test_short_rate_limit_retries_only_once(env: Env) -> None:
    _, fa = env.add("a")
    _, fb = env.add("b")
    env.route("a", "b")
    rate = Status(429, {"error": {"message": "slow down"}}, {"Retry-After": "1"})
    fa.queue(rate, rate)
    events = await env.ask()
    assert len(fa.requests) == 2 and completed(events).entry.label == "b/test-model"


async def test_long_rate_limit_fails_over_with_retry_after_cooldown(env: Env) -> None:
    pa, fa = env.add("a")
    env.add("b")
    env.route("a", "b")
    fa.queue(Status(429, {"error": {"message": "slow down"}}, {"Retry-After": "120"}))
    events = await env.ask()
    assert env.sleeps == [] and completed(events).entry.label == "b/test-model"
    assert env.health("entry", f"{pa.id}|test-model")["until"] == T0 + 120


async def test_unavailable_backs_off_exponentially_and_recovers(env: Env) -> None:
    pa, fa = env.add("a")
    env.add("b")
    env.route("a", "b")
    ref = f"{pa.id}|test-model"
    fa.always = Status(503)
    await env.ask()
    assert env.health("entry", ref)["until"] == T0 + 30
    env.clock.advance(31)
    await env.ask()
    assert env.health("entry", ref)["until"] == env.clock.now + 60 and env.health("entry", ref)["failures"] == 2
    fa.always = None
    env.clock.advance(61)
    events = await env.ask()
    assert completed(events).entry.label == "a/test-model" and env.health("entry", ref) is None


async def test_unavailable_cooldown_is_capped(env: Env) -> None:
    pa, fa = env.add("a")
    env.add("b")
    env.route("a", "b")
    fa.always = Status(500)
    for _ in range(8):
        await env.ask()
        env.clock.advance(1000)
    assert env.health("entry", f"{pa.id}|test-model")["until"] - (env.clock.now - 1000) == 600


async def test_connection_refused_fails_over(env: Env) -> None:
    pa, fa = env.add("a")
    env.add("b")
    env.route("a", "b")
    fa.stop()
    events = await env.ask()
    assert completed(events).entry.label == "b/test-model"
    assert "unavailable" in notices(events)[0]


async def test_timeout_fails_over_with_two_minute_cooldown(env: Env) -> None:
    pa, fa = env.add("a", first_token_timeout=1.0)
    env.add("b")
    env.route("a", "b")
    fa.queue(Silence(5))
    events = await env.ask()
    assert completed(events).entry.label == "b/test-model"
    assert env.health("entry", f"{pa.id}|test-model")["until"] == T0 + 120
    assert notices(events) == ["Switched to b/test-model (a: timeout)"]


async def test_invalid_key_is_disabled_until_edited(env: Env) -> None:
    pa, fa = env.add("a", keys=("bad-key-value1", "good-key-value2"))
    fa.api_key = "good-key-value2"
    _, fb = env.add("b")
    env.route("a", "b")
    events = await env.ask()
    assert completed(events).entry.label == "a/test-model"
    k1, k2 = env.services.store.key_list(pa.id)
    assert env.health("key", str(k1["id"]))["state"] == "disabled"
    assert env.services.providers.public(pa)["keys"][0]["status"] == "invalid"
    assert any("marked invalid" in e["summary"] for e in env.services.store.journal_list(kind="settings"))
    env.clock.advance(10**6)  # disabled keys do not heal by themselves
    await env.ask()
    assert env.health("key", str(k1["id"]))["state"] == "disabled"
    assert [r.headers["authorization"] for r in fa.requests][-1].endswith("good-key-value2")
    env.services.providers.replace_key(k1["id"], "another-good-key")
    assert env.health("key", str(k1["id"])) is None
    assert fb.requests == []


async def test_only_key_invalid_skips_entry(env: Env) -> None:
    pa, fa = env.add("a", keys=("bad-key-value1",))
    fa.api_key = "something-else"
    _, fb = env.add("b")
    env.route("a", "b")
    assert completed(await env.ask()).entry.label == "b/test-model"
    count = len(fa.requests)
    assert completed(await env.ask()).entry.label == "b/test-model" and len(fa.requests) == count


async def test_missing_model_marked_until_refresh(env: Env) -> None:
    pa, fa = env.add("a")
    env.add("b")
    env.route("a", "b")
    fa.queue(Status(404, {"error": {"message": "The model `test-model` does not exist"}}))
    assert completed(await env.ask()).entry.label == "b/test-model"
    assert env.services.store.model_get(pa.id, "test-model")["available"] == 0
    await env.ask()
    assert len(fa.requests) == 1  # not contacted again
    await env.services.providers.refresh_models(pa.id)  # still listed by the provider
    assert completed(await env.ask()).entry.label == "a/test-model"


async def test_context_too_long_only_tries_larger_windows(env: Env) -> None:
    ctx = Status(400, {"error": {"message": "maximum context length is 1000 tokens", "code": "context_length_exceeded"}})
    env.add("a", context=1000)
    _, fsmall = env.add("small", context=500)
    _, fbig = env.add("big", context=8000)
    env.route("a", "small", "big")
    env.fakes["a"].queue(ctx)
    events = await env.ask()
    assert completed(events).entry.label == "big/test-model" and fsmall.requests == []

    env.services.store.health_clear("entry", "x")
    pa = env.services.providers.by_slug("a")
    assert pa is not None
    env.route("a", "small", name="short")
    env.fakes["a"].queue(ctx)
    with pytest.raises(LLMError) as exc:
        await env.ask("short")
    assert exc.value.outcome is Outcome.CONTEXT
    assert env.health("entry", f"{pa.id}|test-model") is None  # no cooldown for context errors


async def test_other_4xx_fails_without_failover(env: Env) -> None:
    pa, fa = env.add("a")
    _, fb = env.add("b")
    env.route("a", "b")
    fa.queue(Status(400, {"error": {"message": "content policy violation"}}))
    with pytest.raises(LLMError) as exc:
        await env.ask()
    assert exc.value.outcome is Outcome.FATAL and fb.requests == []
    assert env.health("entry", f"{pa.id}|test-model") is None


async def test_route_exhausted_lists_every_failure(env: Env) -> None:
    env.add("a")
    env.add("b")
    env.route("a", "b")
    env.fakes["a"].always = Status(402)
    env.fakes["b"].always = Status(503)
    with pytest.raises(RouteExhausted) as exc:
        await env.ask()
    assert exc.value.failures == [("a", "quota exhausted"), ("b", "unavailable")]
    assert "a: quota exhausted; b: unavailable" in str(exc.value)


async def test_all_cooling_down_tries_closest_to_recovery(env: Env) -> None:
    pa, fa = env.add("a")
    pb, fb = env.add("b")
    env.route("a", "b")
    fa.queue(Status(503))
    fb.queue(Silence(5))
    env.services.store.provider_update(pb.id, {"first_token_timeout": 1.0})
    with pytest.raises(RouteExhausted):
        await env.ask()
    assert env.health("entry", f"{pa.id}|test-model")["until"] == T0 + 30
    assert env.health("entry", f"{pb.id}|test-model")["until"] == T0 + 120
    before = (len(fa.requests), len(fb.requests))
    events = await env.ask()  # both cooling: a recovers first, so it is tried
    assert completed(events).entry.label == "a/test-model"
    assert (len(fa.requests), len(fb.requests)) == (before[0] + 1, before[1])


async def test_midstream_failure_resets_and_retries_once(env: Env) -> None:
    env.add("a")
    env.add("b")
    env.route("a", "b")
    env.fakes["a"].queue(Break("par"))
    events = await env.ask()
    kinds = [type(e).__name__ for e in events]
    assert kinds[0] == "Token" and "Reset" in kinds and kinds.index("Reset") > 0
    assert any(isinstance(e, Reset) for e in events)
    assert completed(events).entry.label == "b/test-model" and completed(events).result.content == "ok"
    after_reset = events[kinds.index("Reset") + 1 :]
    assert "".join(e.text for e in after_reset if isinstance(e, Token)) == "ok"


async def test_midstream_failure_without_retry_raises(env: Env) -> None:
    pa, _ = env.add("a")
    _, fb = env.add("b")
    env.route("a", "b")
    env.fakes["a"].queue(Break("par"))
    seen: list[Any] = []
    with pytest.raises(LLMError) as exc:
        entries = env.router.resolve("default")
        async for ev in env.router.chat(entries, [{"role": "user", "content": "x"}], allow_midstream_retry=False):
            seen.append(ev)
    assert exc.value.started and [e.text for e in seen] == ["par"] and fb.requests == []
    assert env.health("entry", f"{pa.id}|test-model") is not None


async def test_second_midstream_failure_is_not_retried(env: Env) -> None:
    env.add("a")
    env.add("b")
    env.add("c")
    env.route("a", "b", "c")
    env.fakes["a"].queue(Break("one"))
    env.fakes["b"].queue(Break("two"))
    with pytest.raises(LLMError) as exc:
        await env.ask()
    assert exc.value.started and env.fakes["c"].requests == []


async def test_route_deadline_stops_the_walk(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    env.add("a")
    _, fb = env.add("b")
    env.route("a", "b")
    env.services.config.update({"ROUTE_DEADLINE": 5})
    env.fakes["a"].queue(Status(503))
    original = env.router._record

    def slow_record(*args: Any, **kw: Any) -> None:
        env.clock.advance(10)
        original(*args, **kw)

    monkeypatch.setattr(env.router, "_record", slow_record)
    with pytest.raises(RouteExhausted) as exc:
        await env.ask()
    assert ("route", "time budget exhausted") in exc.value.failures and fb.requests == []


async def test_pinned_entry_has_no_fallback(env: Env) -> None:
    env.add("a")
    _, fb = env.add("b")
    env.route("a", "b")
    env.fakes["a"].queue(Status(503))
    entries = env.router.resolve("a/test-model")
    assert len(entries) == 1 and entries[0].id is None
    with pytest.raises(RouteExhausted):
        async for _ in env.router.chat(entries, [{"role": "user", "content": "x"}]):
            pass
    assert fb.requests == []
    with pytest.raises(RouteError):
        env.router.resolve("nope/model")
    with pytest.raises(RouteError):
        env.router.resolve("missing-route")


async def test_token_budget_skips_provider_until_next_day(env: Env) -> None:
    pa, fa = env.add("a", budget_tokens_daily=10)
    _, fb = env.add("b")
    env.route("a", "b")
    assert completed(await env.ask()).entry.label == "a/test-model"  # 15 tokens used
    events = await env.ask(session_key="x")
    assert completed(events).entry.label == "b/test-model" and len(fa.requests) == 1
    assert notices(events) == ["Switched to b/test-model (a: daily token budget reached)"]
    env.clock.advance(86400)
    assert completed(await env.ask()).entry.label == "a/test-model"


async def test_review_route_falls_back_to_default(env: Env) -> None:
    with pytest.raises(NoProvider):
        env.router.resolve("default")
    env.add("a")
    env.route("a")
    assert [e.label for e in env.router.resolve("review")] == ["a/test-model"]
    assert [e.label for e in env.router.resolve("")] == ["a/test-model"]
    assert [e.label for e in env.router.resolve(None, "review")] == ["a/test-model"]
    with pytest.raises(RouteError):
        env.router.resolve("custom")


async def test_mistral_tool_ids_are_rewritten_on_the_wire(env: Env) -> None:
    _, fake = env.add("mis", ptype="mistral")
    env.route("mis")
    call = {"id": "call_0123456789_long", "type": "function", "function": {"name": "memory", "arguments": "{}"}}
    history = [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": None, "tool_calls": [call]},
        {"role": "tool", "tool_call_id": call["id"], "content": "r"},
    ]
    entries = env.router.resolve("default")
    async for _ in env.router.chat(entries, history):
        pass
    wire = fake.requests[0].body["messages"]
    assert wire[1]["tool_calls"][0]["id"] == wire[2]["tool_call_id"]
    assert len(wire[2]["tool_call_id"]) == 9 and history[2]["tool_call_id"] == "call_0123456789_long"


async def test_thinking_follows_setting_and_model_control(env: Env) -> None:
    _, fake = env.add("q", control="qwen_template")
    env.add("plain")
    env.route("q")
    await env.ask()
    assert fake.requests[-1].body["chat_template_kwargs"] == {"enable_thinking": False}
    env.services.config.update({"LLM_THINKING": "on"})
    await env.ask()
    assert fake.requests[-1].body["chat_template_kwargs"] == {"enable_thinking": True}
    await env.ask(thinking=False)
    assert fake.requests[-1].body["chat_template_kwargs"] == {"enable_thinking": False}
    env.route("plain", name="p")
    await env.ask("p")
    assert "chat_template_kwargs" not in env.fakes["plain"].requests[-1].body


async def test_extra_body_and_temperature(env: Env) -> None:
    _, fake = env.add("a", extra_body={"top_p": 0.4})
    env.route("a")
    env.services.config.update({"LLM_EXTRA_BODY": {"top_p": 0.9, "seed": 7}, "LLM_TEMPERATURE": 0.1})
    await env.ask()
    body = fake.requests[0].body
    assert body["top_p"] == 0.4 and body["seed"] == 7 and body["temperature"] == 0.1


async def test_complete_returns_result_and_notices(env: Env) -> None:
    env.add("a")
    env.add("b")
    env.route("a", "b")
    env.fakes["a"].queue(Status(402))
    entries = env.router.resolve("default")
    result, entry, notes = await env.router.complete(entries, [{"role": "user", "content": "x"}], session_key="s")
    assert result.content == "ok" and entry.label == "b/test-model" and len(notes) == 1


async def test_health_snapshot_and_reset(env: Env) -> None:
    pa, fa = env.add("a", keys=("key-one-0001",))
    env.add("b")
    env.route("a", "b")
    fa.queue(Status(402))
    await env.ask()
    snap = {s["provider"]: s for s in env.router.health_snapshot()}
    a = snap["a"]
    assert a["errors_today"] == 1 and a["last_error"]["outcome"] == "quota"
    assert a["keys"][0]["state"] == "cooldown" and a["keys"][0]["last4"] == "0001"
    assert snap["b"]["fallbacks_today"] == 1 and snap["b"]["tokens_today"] == 15
    [entry] = a["entries"]
    env.router.reset_health(entry["entry_id"])
    snap = {s["provider"]: s for s in env.router.health_snapshot()}
    assert snap["a"]["keys"][0]["state"] == "healthy" and snap["a"]["entries"][0]["state"] == "healthy"
    with pytest.raises(ProviderError):
        env.router.reset_health(9999)


async def test_usage_report_counts_errors(env: Env) -> None:
    env.add("a")
    env.add("b")
    env.route("a", "b")
    env.fakes["a"].queue(Status(503))
    await env.ask(agent="tester")
    rows = {r["provider"]: r for r in env.router.usage_report(1)}
    assert rows["a"]["errors"] == 1 and rows["a"]["calls"] == 0
    assert rows["b"]["calls"] == 1 and rows["b"]["agent"] == "tester"


def test_set_route_validation(env: Env, services: Services) -> None:
    pa, _ = env.add("a", keys=("key-one-0001",))
    pb, _ = env.add("b")
    services.providers.save_models(pa.id, [{"model_id": "untested"}])
    with pytest.raises(ProviderError, match="tool-calling test"):
        services.llm.set_route("x", [{"provider": "a", "model": "untested"}])
    with pytest.raises(ProviderError, match="not in the provider's model list"):
        services.llm.set_route("x", [{"provider": "a", "model": "ghost"}])
    with pytest.raises(ProviderError, match="unknown provider"):
        services.llm.set_route("x", [{"provider": "zzz", "model": "m"}])
    foreign_key = services.providers.add_key(pb.id, "b-key-value-1")
    with pytest.raises(ProviderError, match="does not belong"):
        services.llm.set_route("x", [{"provider": "a", "model": "test-model", "key_id": foreign_key}])
    with pytest.raises(ProviderError, match="timeout"):
        services.llm.set_route("x", [{"provider": "a", "model": "test-model", "timeouts": {"first_token": 0}}])
    with pytest.raises(ProviderError, match="route names"):
        services.llm.set_route("Bad Name", [])
    with pytest.raises(ProviderError, match="at most"):
        services.llm.set_route("x", [{"provider": "a", "model": "test-model"}] * 13)
    assert "x" not in services.store.route_names()
    entries = services.llm.set_route("x", [{"provider": "b", "model": "test-model", "timeouts": {"first_token": 5}}, {"provider": "a", "model": "test-model"}])
    assert [e.label for e in entries] == ["b/test-model", "a/test-model"] and entries[0].timeouts == {"first_token": 5.0}
    assert services.store.route_names() == ["default", "review", "x"]
    for protected in ("default", "review"):
        with pytest.raises(ProviderError, match="cannot be deleted"):
            services.llm.delete_route(protected)
    services.llm.delete_route("x")
    with pytest.raises(ProviderError):
        services.llm.delete_route("x")


async def test_per_entry_timeouts_override_provider(env: Env) -> None:
    pa, fa = env.add("a")
    env.add("b")
    env.services.llm.set_route("default", [
        {"provider": "a", "model": "test-model", "timeouts": {"first_token": 1}},
        {"provider": "b", "model": "test-model"},
    ])
    fa.queue(Silence(5))
    events = await env.ask()
    assert completed(events).entry.label == "b/test-model"


def test_entry_helpers(env: Env) -> None:
    p, _ = env.add("a")
    e = Entry(None, p, "m")
    assert e.label == "a/m" and e.ref == f"{p.id}|m"
