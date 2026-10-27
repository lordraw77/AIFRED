"""Streaming OpenAI-compatible client (spec section 6).

The client performs a single attempt against one endpoint and classifies the
outcome; retries and failover belong to :mod:`aifred.router`.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from enum import StrEnum
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_BODY_EXCERPT = 500


class Outcome(StrEnum):
    """Classification of a failed attempt (failover table, spec 6.1)."""

    QUOTA = "quota"
    RATE_LIMIT = "rate_limit"
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    AUTH = "auth"
    MODEL_MISSING = "model_missing"
    CONTEXT = "context_too_long"
    FATAL = "fatal"


class LLMError(Exception):
    """A failed model call.

    Attributes:
        outcome: Failure class.
        status: HTTP status, if any.
        code: Provider error code or type, if any.
        body: Excerpt of the response body.
        retry_after: Seconds the provider asked to wait or until quota reset.
        started: Whether output had already been produced when it failed.
    """

    def __init__(
        self,
        message: str,
        outcome: Outcome,
        *,
        status: int | None = None,
        code: str = "",
        body: str = "",
        retry_after: float | None = None,
        started: bool = False,
    ) -> None:
        super().__init__(message)
        self.outcome = outcome
        self.status = status
        self.code = code
        self.body = body
        self.retry_after = retry_after
        self.started = started


_QUOTA_WORDS = (
    "quota", "billing", "insufficient", "credit", "resource_exhausted", "resource exhausted",
    "exceeded your current", "out of funds", "payment required", "spend limit", "budget",
)
_CONTEXT_WORDS = (
    "context length", "context_length", "context window", "maximum context", "too many tokens",
    "reduce the length", "prompt is too long", "input is too long", "exceeds the maximum",
)
_DURATION_RE = re.compile(r"(\d+(?:\.\d+)?)(ms|s|m|h|d)")
_DURATION_UNITS = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}
_RESET_HEADERS = (
    "retry-after", "x-ratelimit-reset-tokens", "x-ratelimit-reset-requests", "ratelimit-reset",
    "x-ratelimit-reset",
)


def parse_duration(value: str) -> float | None:
    """Parse ``Retry-After`` style values: seconds, an HTTP date or ``1m30.5s``.

    Returns:
        Seconds from now, or ``None`` if the value cannot be understood.
    """
    value = value.strip()
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    parts = _DURATION_RE.findall(value)
    if parts and "".join(n + u for n, u in parts) == value.replace(" ", ""):
        return sum(float(n) * _DURATION_UNITS[u] for n, u in parts)
    try:
        return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
    except (TypeError, ValueError):
        return None


def retry_after_from(headers: httpx.Headers | dict[str, str]) -> float | None:
    """Seconds to wait according to ``Retry-After`` or rate-limit reset headers."""
    lowered = {k.lower(): v for k, v in headers.items()}
    for name in _RESET_HEADERS:
        if name in lowered and (seconds := parse_duration(lowered[name])) is not None:
            return seconds
    return None


def _error_fields(body: str) -> tuple[str, str]:
    """Extract (message, code) from a provider error body."""
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return body.strip(), ""
    if isinstance(data, list) and data:
        data = data[0]
    err = data.get("error", data) if isinstance(data, dict) else data
    if isinstance(err, dict):
        message = str(err.get("message") or err.get("detail") or "")
        code = str(err.get("code") or err.get("type") or err.get("status") or "")
        return message or body.strip(), code
    return str(err), ""


def _is_json_object(body: str) -> bool:
    try:
        return isinstance(json.loads(body), dict)
    except (json.JSONDecodeError, TypeError):
        return False


def classify_http(status: int, body: str, headers: httpx.Headers | dict[str, str]) -> LLMError:
    """Turn an HTTP error response into a classified :class:`LLMError`."""
    message, code = _error_fields(body)
    text = f"{message} {code}".lower()
    retry_after = retry_after_from(headers)
    excerpt = body.strip()[:_BODY_EXCERPT]
    label = f"HTTP {status}" + (f": {message[:200]}" if message else "")

    def error(outcome: Outcome) -> LLMError:
        return LLMError(label, outcome, status=status, code=code, body=excerpt, retry_after=retry_after)

    if status == 402:
        return error(Outcome.QUOTA)
    if status == 429:
        return error(Outcome.QUOTA if any(w in text for w in _QUOTA_WORDS) else Outcome.RATE_LIMIT)
    if status in (401, 403):
        return error(Outcome.AUTH)
    if status == 404:
        # a structured error naming the missing resource (model, "Function ... not found for account")
        # means the model is not usable; a bare "page not found" means a wrong URL
        return error(Outcome.MODEL_MISSING if "model" in text or _is_json_object(body) else Outcome.UNAVAILABLE)
    if status == 408:
        return error(Outcome.TIMEOUT)
    if status >= 500:
        return error(Outcome.UNAVAILABLE)
    if status == 400 and any(w in text for w in _CONTEXT_WORDS):
        return error(Outcome.CONTEXT)
    return error(Outcome.FATAL)


def classify_exception(exc: BaseException, *, started: bool = False) -> LLMError:
    """Turn a transport exception into a classified :class:`LLMError`."""
    if isinstance(exc, LLMError):
        return exc
    if isinstance(exc, (httpx.ConnectTimeout, httpx.PoolTimeout)):
        return LLMError("connection timed out", Outcome.TIMEOUT, started=started)
    if isinstance(exc, httpx.TimeoutException):
        return LLMError("timed out waiting for the model", Outcome.TIMEOUT, started=started)
    if isinstance(exc, httpx.ConnectError):
        return LLMError(f"cannot connect: {exc}"[:200], Outcome.UNAVAILABLE, started=started)
    if isinstance(exc, (httpx.TransportError, OSError)):
        return LLMError(f"connection error: {exc}"[:200], Outcome.UNAVAILABLE, started=started)
    return LLMError(f"unexpected error: {exc!r}"[:200], Outcome.FATAL, started=started)


class ThinkFilter:
    """Stateful ``<think>…</think>`` remover that copes with tags split across chunks."""

    _OPEN = "<think>"
    _CLOSE = "</think>"

    def __init__(self) -> None:
        self._inside = False
        self._buffer = ""
        self._strip_leading = False

    def feed(self, text: str) -> str:
        """Consume a chunk and return the visible text it completes."""
        self._buffer += text
        out: list[str] = []
        while self._buffer:
            tag = self._CLOSE if self._inside else self._OPEN
            index = self._buffer.find(tag)
            if index >= 0:
                if not self._inside:
                    out.append(self._buffer[:index])
                self._buffer = self._buffer[index + len(tag) :]
                self._inside = not self._inside
                self._strip_leading = not self._inside
                continue
            keep = self._partial_suffix(self._buffer, tag)
            emit, self._buffer = self._buffer[: len(self._buffer) - keep], self._buffer[len(self._buffer) - keep :]
            if not self._inside:
                out.append(emit)
            break
        visible = "".join(out)
        if self._strip_leading and visible:
            visible = visible.lstrip()
            self._strip_leading = not visible
        return visible

    def flush(self) -> str:
        """Return held-back text at the end of the stream (an unfinished tag is visible text)."""
        rest, self._buffer = self._buffer, ""
        return "" if self._inside else rest

    @staticmethod
    def _partial_suffix(text: str, tag: str) -> int:
        for size in range(min(len(tag) - 1, len(text)), 0, -1):
            if text.endswith(tag[:size]):
                return size
        return 0


@dataclass
class Usage:
    """Token counts of one call."""

    prompt: int = 0
    completion: int = 0
    estimated: bool = False


@dataclass
class ChatResult:
    """A completed model response.

    Attributes:
        content: Visible text (reasoning removed).
        tool_calls: OpenAI-format tool calls.
        finish_reason: Provider finish reason.
        usage: Token counts.
        latency_ms: Time from request to completion.
    """

    content: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: str | None = None
    usage: Usage = field(default_factory=Usage)
    latency_ms: int = 0


@dataclass(frozen=True)
class Token:
    """A streamed piece of visible text."""

    text: str


@dataclass(frozen=True)
class Done:
    """End of a successful stream."""

    result: ChatResult


@dataclass(frozen=True)
class Endpoint:
    """Everything needed for one attempt, resolved from a route entry.

    Attributes:
        provider_type: Provider type (selects the adapter).
        chat_url: Full chat-completions URL.
        model: Model id.
        api_key: Key for this attempt, if any.
        headers: Extra headers.
        extra_body: Provider-level extra request body.
        verify: TLS verification (``True``, ``False`` or a CA bundle path).
        connect: Connect timeout (s).
        first_token: Time allowed before the first output (s).
        stall: Maximum silence between chunks (s).
        total: Overall time limit (s).
        thinking_control: How reasoning is switched (model setting).
    """

    provider_type: str
    chat_url: str
    model: str
    api_key: str | None
    headers: dict[str, str]
    extra_body: dict[str, Any]
    verify: bool | str
    connect: float
    first_token: float
    stall: float
    total: float
    thinking_control: str = "none"


HttpFactory = Callable[[Endpoint], httpx.AsyncClient]


def default_http_factory(endpoint: Endpoint) -> httpx.AsyncClient:
    """Build a client honouring the provider's TLS option."""
    # reads are bounded by stream_chat's own first-token/stall/total timers, not by httpx
    return httpx.AsyncClient(
        verify=endpoint.verify,
        timeout=httpx.Timeout(None, connect=endpoint.connect, write=30.0, pool=endpoint.connect),
        follow_redirects=False,
    )


_BASE62 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


def mistral_tool_id(tool_id: str) -> str:
    """Map a tool-call id to Mistral's required 9 alphanumeric characters (deterministic)."""
    if re.fullmatch(r"[A-Za-z0-9]{9}", tool_id):
        return tool_id
    number = int.from_bytes(hashlib.sha256(tool_id.encode()).digest()[:8], "big")
    chars = []
    for _ in range(9):
        number, rem = divmod(number, 62)
        chars.append(_BASE62[rem])
    return "".join(chars)


def adapt_messages(provider_type: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalise neutral OpenAI-format history for a provider (portable history, spec 6.1)."""
    if provider_type != "mistral":
        return messages
    out = []
    for msg in messages:
        msg = dict(msg)
        if msg.get("tool_call_id"):
            msg["tool_call_id"] = mistral_tool_id(msg["tool_call_id"])
        if msg.get("tool_calls"):
            msg["tool_calls"] = [{**tc, "id": mistral_tool_id(tc["id"])} for tc in msg["tool_calls"]]
        out.append(msg)
    return out


def apply_thinking(body: dict[str, Any], control: str, enabled: bool) -> None:
    """Set the reasoning switch according to the model's ``thinking_control``."""
    if control == "qwen_template":
        kwargs = dict(body.get("chat_template_kwargs") or {})
        kwargs["enable_thinking"] = enabled
        body["chat_template_kwargs"] = kwargs
    elif control == "reasoning_effort":
        # [VERIFY] accepted values differ between providers; "low"/"high" are the common denominator
        body["reasoning_effort"] = "high" if enabled else "low"


def build_body(
    endpoint: Endpoint,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    *,
    temperature: float | None,
    global_extra: dict[str, Any] | None,
    thinking: bool,
) -> dict[str, Any]:
    """Assemble the request body: global extras, then provider extras, then fixed fields."""
    body: dict[str, Any] = {}
    body.update(global_extra or {})
    body.update(endpoint.extra_body)
    body.update(
        {
            "model": endpoint.model,
            "messages": adapt_messages(endpoint.provider_type, messages),
            "stream": True,
            "stream_options": {"include_usage": True},
        }
    )
    if temperature is not None:
        body["temperature"] = temperature
    if tools:
        body["tools"] = tools
    apply_thinking(body, endpoint.thinking_control, thinking)
    return body


def estimate_tokens(text: str) -> int:
    """Rough token estimate (about four characters per token) for providers that omit usage."""
    return max(1, len(text) // 4) if text else 0


class _Assembler:
    """Builds a :class:`ChatResult` from streamed chunks."""

    def __init__(self) -> None:
        self.think = ThinkFilter()
        self.content: list[str] = []
        self.calls: dict[int, dict[str, Any]] = {}
        self.finish: str | None = None
        self.usage: Usage | None = None
        self.alive = False

    def feed(self, chunk: dict[str, Any]) -> str:
        """Consume one decoded SSE payload and return newly visible text."""
        usage = chunk.get("usage")
        if isinstance(usage, dict):
            self.usage = Usage(int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0))
        visible = ""
        for choice in chunk.get("choices") or []:
            if choice.get("finish_reason"):
                self.finish = choice["finish_reason"]
            delta = choice.get("delta") or {}
            if delta.get("content") or delta.get("tool_calls") or delta.get("reasoning_content") or delta.get("reasoning"):
                self.alive = True  # reasoning chunks are data too: the model is working
            if text := delta.get("content"):
                visible += self.think.feed(text)
            for tc in delta.get("tool_calls") or []:
                slot = self.calls.setdefault(int(tc.get("index", 0)), {"id": "", "name": "", "arguments": ""})
                slot["id"] = tc.get("id") or slot["id"]
                fn = tc.get("function") or {}
                slot["name"] += fn.get("name") or ""
                slot["arguments"] += fn.get("arguments") or ""
        if visible:
            self.content.append(visible)
        return visible

    def finish_stream(self) -> str:
        """Flush the think filter; returns any last visible text."""
        tail = self.think.flush()
        if tail:
            self.content.append(tail)
        return tail

    def result(self, prompt_text: str, latency_ms: int) -> ChatResult:
        """Build the final result."""
        content = "".join(self.content)
        tool_calls = [
            {
                "id": slot["id"] or f"call_{i}",
                "type": "function",
                "function": {"name": slot["name"], "arguments": slot["arguments"] or "{}"},
            }
            for i, (_, slot) in enumerate(sorted(self.calls.items()))
        ]
        usage = self.usage or Usage(
            estimate_tokens(prompt_text),
            estimate_tokens(content + "".join(t["function"]["arguments"] for t in tool_calls)),
            estimated=True,
        )
        return ChatResult(content, tool_calls, self.finish, usage, latency_ms)


async def _read_error_body(response: httpx.Response) -> str:
    try:
        raw = await asyncio.wait_for(response.aread(), 5)
    except Exception:  # noqa: BLE001 - the status is what matters
        return ""
    return raw.decode(errors="replace")


async def stream_chat(
    endpoint: Endpoint,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    *,
    temperature: float | None = None,
    global_extra: dict[str, Any] | None = None,
    thinking: bool = False,
    http_factory: HttpFactory = default_http_factory,
) -> AsyncIterator[Token | Done]:
    """Make one streaming chat-completions call.

    Yields:
        :class:`Token` for each piece of visible text, then :class:`Done`.

    Raises:
        LLMError: On any failure, classified for the route walker. ``started`` is
            true when output had already been emitted.
    """
    body = build_body(endpoint, messages, tools, temperature=temperature, global_extra=global_extra, thinking=thinking)
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream", **endpoint.headers}
    if endpoint.api_key:
        headers["Authorization"] = f"Bearer {endpoint.api_key}"
    prompt_text = json.dumps(body["messages"], ensure_ascii=False)

    loop = asyncio.get_running_loop()
    began = loop.time()
    deadline = began + endpoint.total
    assembler = _Assembler()
    emitted = False

    def remaining(limit: float) -> float:
        left = deadline - loop.time()
        if left <= 0:
            raise LLMError(f"total time limit of {endpoint.total:g} s exceeded", Outcome.TIMEOUT, started=emitted)
        return min(limit, left)

    async with http_factory(endpoint) as client:
        response: httpx.Response | None = None
        try:
            request = client.build_request("POST", endpoint.chat_url, json=body, headers=headers)
            try:
                response = await asyncio.wait_for(client.send(request, stream=True), remaining(endpoint.first_token))
            except asyncio.TimeoutError:
                raise LLMError(f"no response within {endpoint.first_token:g} s", Outcome.TIMEOUT) from None
            if response.status_code >= 400:
                raise classify_http(response.status_code, await _read_error_body(response), response.headers)

            lines = response.aiter_lines()
            while True:
                limit = endpoint.stall if assembler.alive else endpoint.first_token
                try:
                    line = await asyncio.wait_for(lines.__anext__(), remaining(limit))
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError:
                    if loop.time() >= deadline:
                        raise LLMError(
                            f"total time limit of {endpoint.total:g} s exceeded", Outcome.TIMEOUT, started=emitted
                        ) from None
                    what = "stream stalled" if assembler.alive else "no first token"
                    raise LLMError(f"{what} for {limit:g} s", Outcome.TIMEOUT, started=emitted) from None
                line = line.strip()
                if not line or line.startswith(":") or not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    logger.debug("Ignoring undecodable SSE payload")
                    continue
                if isinstance(chunk, dict) and "error" in chunk and not chunk.get("choices"):
                    raise _stream_error(chunk, started=emitted)
                if visible := assembler.feed(chunk):
                    emitted = True
                    yield Token(visible)
            if tail := assembler.finish_stream():
                emitted = True
                yield Token(tail)
        except LLMError as exc:
            exc.started = exc.started or emitted
            raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - classified below
            raise classify_exception(exc, started=emitted) from exc
        finally:
            if response is not None:
                await response.aclose()

    yield Done(assembler.result(prompt_text, int((loop.time() - began) * 1000)))


def _stream_error(chunk: dict[str, Any], *, started: bool) -> LLMError:
    """Classify an error object delivered inside the SSE stream."""
    err = chunk.get("error")
    status = err.get("code") if isinstance(err, dict) else None
    if isinstance(status, str) and status.isdigit():
        status = int(status)
    if isinstance(status, int) and status >= 400:
        error = classify_http(status, json.dumps(chunk), {})
    else:
        message, code = _error_fields(json.dumps(chunk))
        error = LLMError(f"stream error: {message[:200]}", Outcome.UNAVAILABLE, code=code)
    error.started = started
    return error
