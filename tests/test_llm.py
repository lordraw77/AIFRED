"""Tests for aifred.llm."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from aifred.llm import (
    ChatResult,
    Done,
    Endpoint,
    LLMError,
    Outcome,
    ThinkFilter,
    Token,
    adapt_messages,
    apply_thinking,
    build_body,
    classify_http,
    mistral_tool_id,
    parse_duration,
    retry_after_from,
    stream_chat,
)
from tests.fake_llm import Break, FakeLLM, Silence, StallAfter, Status, StreamError, Text, ToolCall

pytestmark = pytest.mark.anyio

MESSAGES = [{"role": "user", "content": "hello there"}]


def endpoint(fake: FakeLLM | None, **kw: Any) -> Endpoint:
    base = dict(
        provider_type="local", chat_url=(fake.url if fake else "http://127.0.0.1:9/v1") + "/chat/completions",
        model="test-model", api_key=None, headers={}, extra_body={}, verify=True,
        connect=2.0, first_token=2.0, stall=2.0, total=10.0,
    )
    base.update(kw)
    return Endpoint(**base)

async def run(ep: Endpoint, tools: list[dict[str, Any]] | None = None, **kw: Any) -> tuple[list[str], ChatResult]:
    tokens: list[str] = []
    result: ChatResult | None = None
    async for ev in stream_chat(ep, MESSAGES, tools, **kw):
        if isinstance(ev, Token):
            tokens.append(ev.text)
        elif isinstance(ev, Done):
            result = ev.result
    assert result is not None
    return tokens, result


# ThinkFilter


def _filter(chunks: list[str]) -> str:
    f = ThinkFilter()
    return "".join(f.feed(c) for c in chunks) + f.flush()


def test_think_removed_in_one_chunk() -> None:
    assert _filter(["<think>reasoning</think>\n\nAnswer"]) == "Answer"


def test_think_tags_split_across_chunks() -> None:
    text = "Before<think>hidden thoughts</think>After"
    for size in (1, 2, 3, 5):
        chunks = [text[i : i + size] for i in range(0, len(text), size)]
        assert _filter(chunks) == "BeforeAfter", size


def test_think_empty_block_and_whitespace() -> None:
    assert _filter(["<think>\n\n</think>\n\nHi ", "there"]) == "Hi there"


def test_text_without_tags_is_untouched() -> None:
    assert _filter(["a < b and c > d", " 1 <thing"]) == "a < b and c > d 1 <thing"


def test_unfinished_thinking_is_dropped() -> None:
    assert _filter(["Hi<think>never closed"]) == "Hi"


def test_lone_partial_tag_is_flushed_as_text() -> None:
    assert _filter(["x <thi"]) == "x <thi"


# Parsing and classification


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("5", 5.0), ("2.5", 2.5), ("1m30s", 90.0), ("12.5s", 12.5), ("250ms", 0.25), ("2h", 7200.0), ("soon", None), ("", None)],
)
def test_parse_duration(raw: str, expected: float | None) -> None:
    assert parse_duration(raw) == expected


def test_retry_after_headers() -> None:
    assert retry_after_from({"Retry-After": "7"}) == 7
    assert retry_after_from({"x-ratelimit-reset-tokens": "1m0s"}) == 60
    assert retry_after_from({}) is None


@pytest.mark.parametrize(
    ("status", "body", "headers", "outcome"),
    [
        (402, "", {}, Outcome.QUOTA),
        (429, '{"error":{"message":"You exceeded your current quota, please check your plan and billing"}}', {}, Outcome.QUOTA),
        (429, '{"error":{"message":"insufficient credits"}}', {}, Outcome.QUOTA),
        (429, '[{"error":{"code":429,"status":"RESOURCE_EXHAUSTED","message":"Quota exceeded"}}]', {}, Outcome.QUOTA),
        (429, '{"error":{"message":"Rate limit reached"}}', {"retry-after": "2"}, Outcome.RATE_LIMIT),
        (500, "boom", {}, Outcome.UNAVAILABLE),
        (503, '{"error":{"message":"overloaded"}}', {}, Outcome.UNAVAILABLE),
        (529, "", {}, Outcome.UNAVAILABLE),
        (408, "", {}, Outcome.TIMEOUT),
        (401, "", {}, Outcome.AUTH),
        (403, "", {}, Outcome.AUTH),
        (404, '{"error":{"message":"The model `x` does not exist"}}', {}, Outcome.MODEL_MISSING),
        (404, "page not found", {}, Outcome.UNAVAILABLE),
        (404, '{"status":404,"title":"Not Found","detail":"Function \'abc\': Not found for account \'xyz\'"}', {}, Outcome.MODEL_MISSING),
        (400, '{"error":{"message":"maximum context length is 8192 tokens","code":"context_length_exceeded"}}', {}, Outcome.CONTEXT),
        (400, '{"error":{"message":"Your prompt was flagged by content policy"}}', {}, Outcome.FATAL),
        (422, "", {}, Outcome.FATAL),
    ],
)
def test_classify_http(status: int, body: str, headers: dict[str, str], outcome: Outcome) -> None:
    err = classify_http(status, body, headers)
    assert err.outcome is outcome
    assert err.status == status


def test_classify_keeps_retry_after_and_body() -> None:
    err = classify_http(429, '{"error":{"message":"slow down"}}', {"Retry-After": "3"})
    assert err.retry_after == 3 and "slow down" in err.body and err.code == ""


# Adapters and body


def test_mistral_ids_are_nine_alphanumerics_and_consistent() -> None:
    mapped = mistral_tool_id("call_abc123xyz_long_id")
    assert len(mapped) == 9 and mapped.isalnum()
    assert mistral_tool_id("call_abc123xyz_long_id") == mapped
    assert mistral_tool_id("abc123XYZ") == "abc123XYZ"
    history = [
        {"role": "assistant", "content": None, "tool_calls": [{"id": "call_long_identifier", "type": "function", "function": {"name": "a", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "call_long_identifier", "content": "r"},
    ]
    out = adapt_messages("mistral", history)
    assert out[0]["tool_calls"][0]["id"] == out[1]["tool_call_id"] != "call_long_identifier"
    assert history[0]["tool_calls"][0]["id"] == "call_long_identifier"  # stored history stays neutral
    assert adapt_messages("groq", history) is history


def test_thinking_switches() -> None:
    body: dict[str, Any] = {"chat_template_kwargs": {"other": 1}}
    apply_thinking(body, "qwen_template", True)
    assert body["chat_template_kwargs"] == {"other": 1, "enable_thinking": True}
    body = {}
    apply_thinking(body, "none", True)
    assert body == {}
    apply_thinking(body, "reasoning_effort", False)
    assert body["reasoning_effort"] == "low"


def test_build_body_precedence() -> None:
    ep = endpoint(None, extra_body={"top_p": 0.5, "temperature": 9}, thinking_control="qwen_template")
    body = build_body(ep, MESSAGES, None, temperature=0.3, global_extra={"top_p": 0.9, "seed": 1}, thinking=False)
    assert body["top_p"] == 0.5 and body["seed"] == 1 and body["temperature"] == 0.3
    assert body["stream"] is True and body["stream_options"] == {"include_usage": True}
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert "tools" not in body


# Streaming against the fake server


async def test_streams_text_and_usage(fake_llm: Callable[[], FakeLLM]) -> None:
    fake = fake_llm().queue(Text("Hello world", chunks=3, usage=(21, 4)))
    tokens, result = await run(endpoint(fake, api_key="sk-test"))
    assert len(tokens) == 3 and "".join(tokens) == "Hello world"
    assert result.content == "Hello world" and result.finish_reason == "stop"
    assert (result.usage.prompt, result.usage.completion, result.usage.estimated) == (21, 4, False)
    sent = fake.requests[0]
    assert sent.headers["authorization"] == "Bearer sk-test"
    assert sent.body["stream"] is True and sent.body["model"] == "test-model"


async def test_missing_usage_is_estimated(fake_llm: Callable[[], FakeLLM]) -> None:
    fake = fake_llm().queue(Text("some answer text", usage=None))
    _, result = await run(endpoint(fake))
    assert result.usage.estimated and result.usage.completion > 0 and result.usage.prompt > 0


async def test_reasoning_content_is_ignored_and_think_tags_removed(fake_llm: Callable[[], FakeLLM]) -> None:
    fake = fake_llm().queue(Text(pieces=["<thi", "nk>secret plan</th", "ink>\n\nVisible ", "answer"]))
    tokens, result = await run(endpoint(fake))
    assert result.content == "Visible answer" and "hidden" not in result.content and "secret" not in "".join(tokens)


async def test_tool_call_assembled_from_deltas(fake_llm: Callable[[], FakeLLM]) -> None:
    fake = fake_llm().queue(ToolCall("memory", {"action": "add", "content": "likes tea"}))
    tools = [{"type": "function", "function": {"name": "memory", "parameters": {"type": "object"}}}]
    tokens, result = await run(endpoint(fake), tools)
    assert tokens == []
    [call] = result.tool_calls
    assert call["id"] == "call_abc123xyz" and call["type"] == "function"
    assert call["function"]["name"] == "memory"
    assert json.loads(call["function"]["arguments"]) == {"action": "add", "content": "likes tea"}
    assert fake.requests[0].body["tools"] == tools


async def test_missing_tool_call_id_is_generated(fake_llm: Callable[[], FakeLLM]) -> None:
    fake = fake_llm().queue(ToolCall("ping", {"word": "pong"}, id=None))
    _, result = await run(endpoint(fake))
    assert result.tool_calls[0]["id"] == "call_0"


async def test_thinking_flag_is_sent(fake_llm: Callable[[], FakeLLM]) -> None:
    fake = fake_llm()
    await run(endpoint(fake, thinking_control="qwen_template"), thinking=True, temperature=0.2, global_extra={"seed": 3})
    body = fake.requests[0].body
    assert body["chat_template_kwargs"] == {"enable_thinking": True}
    assert body["temperature"] == 0.2 and body["seed"] == 3


HTTP_ERROR_CASES: list[tuple[Any, Outcome]] = [
        (Status(402), Outcome.QUOTA),
        (Status(429, {"error": {"message": "quota exceeded"}}), Outcome.QUOTA),
        (Status(429, {"error": {"message": "slow"}}, {"Retry-After": "1"}), Outcome.RATE_LIMIT),
        (Status(503), Outcome.UNAVAILABLE),
        (Status(401), Outcome.AUTH),
        (Status(404, {"error": {"message": "model not found"}}), Outcome.MODEL_MISSING),
        (Status(400, {"error": {"message": "This model's maximum context length is 100 tokens"}}), Outcome.CONTEXT),
        (Status(400, {"error": {"message": "content policy"}}), Outcome.FATAL),
        (StreamError({"message": "upstream overloaded", "code": 503}), Outcome.UNAVAILABLE),
    ]


async def test_http_errors_are_classified(fake_llm: Callable[[], FakeLLM]) -> None:
    for scripted, outcome in HTTP_ERROR_CASES:
        fake = fake_llm().queue(scripted)
        with pytest.raises(LLMError) as exc:
            await run(endpoint(fake))
        assert exc.value.outcome is outcome, scripted
        assert exc.value.started is False


async def test_retry_after_is_carried(fake_llm: Callable[[], FakeLLM]) -> None:
    fake = fake_llm().queue(Status(429, {"error": {"message": "wait"}}, {"Retry-After": "4"}))
    with pytest.raises(LLMError) as exc:
        await run(endpoint(fake))
    assert exc.value.retry_after == 4


async def test_connection_refused_is_unavailable() -> None:
    with pytest.raises(LLMError) as exc:
        await run(endpoint(None))
    assert exc.value.outcome is Outcome.UNAVAILABLE


async def test_no_first_token_times_out(fake_llm: Callable[[], FakeLLM]) -> None:
    fake = fake_llm().queue(Silence(5))
    with pytest.raises(LLMError) as exc:
        await run(endpoint(fake, first_token=0.3))
    assert exc.value.outcome is Outcome.TIMEOUT and not exc.value.started
    assert "first token" in str(exc.value) or "response" in str(exc.value)


async def test_stall_after_output_times_out_as_started(fake_llm: Callable[[], FakeLLM]) -> None:
    fake = fake_llm().queue(StallAfter("some text", 5))
    with pytest.raises(LLMError) as exc:
        await run(endpoint(fake, stall=0.3))
    assert exc.value.outcome is Outcome.TIMEOUT and exc.value.started


async def test_total_timeout(fake_llm: Callable[[], FakeLLM]) -> None:
    fake = fake_llm().queue(StallAfter("x", 5))
    with pytest.raises(LLMError) as exc:
        await run(endpoint(fake, stall=3.0, total=0.4))
    assert exc.value.outcome is Outcome.TIMEOUT and "total" in str(exc.value)


async def test_dropped_connection_marks_started(fake_llm: Callable[[], FakeLLM]) -> None:
    fake = fake_llm().queue(Break("half an ans"))
    with pytest.raises(LLMError) as exc:
        await run(endpoint(fake))
    assert exc.value.outcome in (Outcome.UNAVAILABLE, Outcome.TIMEOUT) and exc.value.started


def test_default_factory_honours_verify_option() -> None:
    from aifred.llm import default_http_factory

    client = default_http_factory(endpoint(None, verify=False))
    assert isinstance(client, httpx.AsyncClient)


async def test_reasoning_chunks_count_as_first_data(fake_llm: Callable[[], FakeLLM]) -> None:
    from tests.fake_llm import Reasoning

    fake = fake_llm().queue(Reasoning(pieces=4, gap=0.3, then="done"))
    # silence between chunks (0.3 s) is below stall; total thinking (1.2 s) exceeds first_token
    tokens, result = await run(endpoint(fake, first_token=0.6, stall=1.0))
    assert result.content == "done" and "".join(tokens) == "done"


async def test_first_token_timeout_is_not_cut_short_by_the_stall_timeout(fake_llm: Callable[[], FakeLLM]) -> None:
    fake = fake_llm().queue(Silence(0.9))  # headers now, data after 0.9 s: slower than stall, within first_token
    _, result = await run(endpoint(fake, first_token=5.0, stall=0.3))
    assert result.content == ""
