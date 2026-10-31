"""Routes, failover, health and usage tracking (spec 6.1).

A *route* is an ordered list of ``(provider, model)`` entries. Every request walks
the route: it tries the first available entry and moves on when it hits a failover
condition. :class:`LLMRouter` owns that logic; the HTTP call itself is
:func:`aifred.llm.stream_chat`.
"""

from __future__ import annotations

import asyncio
import calendar
import logging
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from aifred.config import RuntimeConfig
from aifred.llm import (
    ChatResult,
    Done,
    Endpoint,
    HttpFactory,
    LLMError,
    Outcome,
    Token,
    stream_chat,
)
from aifred.providers import SLUG_RE, Provider, ProviderError, ProviderService
from aifred.store import Store
from aifred.vault import Vault, VaultUnavailable

logger = logging.getLogger(__name__)

PROTECTED_ROUTES = ("default", "review", "vision")
MAX_ROUTE_ENTRIES = 12
_SHORT_RATE_LIMIT = 5.0
_UNAVAILABLE_BASE = 30.0
_UNAVAILABLE_MAX = 600.0
_TIMEOUT_COOLDOWN = 120.0
_AUTH_ENTRY_COOLDOWN = 600.0
_INF = float("inf")

_REASONS = {
    Outcome.QUOTA: "quota exhausted",
    Outcome.RATE_LIMIT: "rate limited",
    Outcome.UNAVAILABLE: "unavailable",
    Outcome.TIMEOUT: "timeout",
    Outcome.AUTH: "invalid key",
    Outcome.MODEL_MISSING: "model missing",
    Outcome.CONTEXT: "context too long",
    Outcome.FATAL: "request rejected",
}


class RouteError(Exception):
    """A route cannot be used."""


class NoProvider(RouteError):
    """No LLM provider is configured yet (first-run state)."""

    def __init__(self) -> None:
        super().__init__("No LLM provider is configured yet. Open the AIFRED dashboard (Models page) to add one.")


class RouteExhausted(RouteError):
    """Every entry of the route failed.

    Attributes:
        failures: ``(entry label, reason)`` pairs.
    """

    def __init__(self, failures: list[tuple[str, str]]) -> None:
        self.failures = failures
        detail = "; ".join(f"{label}: {reason}" for label, reason in failures) or "no entry is available"
        super().__init__(f"Every model in the route failed ({detail})")


@dataclass(frozen=True)
class Entry:
    """One resolved route entry.

    Attributes:
        id: Route entry id, or ``None`` for a pinned ``provider/model``.
        provider: The provider.
        model: Model id.
        key_id: Pinned provider key, if any.
        timeouts: Per-entry overrides of the provider timeouts.
        context_window: Model context window, if known.
        thinking_control: Model thinking control.
        model_available: Whether the model is marked available.
    """

    id: int | None
    provider: Provider
    model: str
    key_id: int | None = None
    timeouts: dict[str, float] = field(default_factory=dict)
    context_window: int | None = None
    thinking_control: str = "none"
    model_available: bool = True

    @property
    def ref(self) -> str:
        """Health key shared by every route that uses this provider/model."""
        return f"{self.provider.id}|{self.model}"

    @property
    def label(self) -> str:
        """Display name ``provider/model``."""
        return f"{self.provider.slug}/{self.model}"


@dataclass(frozen=True)
class Reset:
    """Discard the partial text of the current iteration (mid-stream failover)."""


@dataclass(frozen=True)
class Notice:
    """A route switch to show according to ``NOTIFICATIONS``."""

    text: str


@dataclass(frozen=True)
class Completed:
    """A finished call and the entry that served it."""

    result: ChatResult
    entry: Entry


@dataclass
class _Status:
    keys: list[int | None]
    blocked_until: float | None = None
    reason: str = ""


@dataclass
class _Walk:
    route: str
    source: str
    agent: str | None
    primary: Entry
    allow_midstream_retry: bool
    thinking: bool
    deadline: float
    failures: list[tuple[str, str]] = field(default_factory=list)
    midstream_used: bool = False
    min_context: int | None = None
    context_failed: bool = False
    attempts: int = 0


@dataclass
class _Failed:
    """Marker: the entry was given up on, try the next one."""


def _day_start(now: float) -> float:
    d = datetime.fromtimestamp(now, timezone.utc)
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp()


def _month_bounds(now: float) -> tuple[float, float]:
    d = datetime.fromtimestamp(now, timezone.utc)
    start = datetime(d.year, d.month, 1, tzinfo=timezone.utc).timestamp()
    days = calendar.monthrange(d.year, d.month)[1]
    return start, start + days * 86400


class LLMRouter:
    """Resolves routes and walks them with failover.

    Args:
        store: Database.
        vault: Encrypted secrets.
        config: Runtime settings.
        providers: Provider service.
        http_factory: Builds HTTP clients (replaced in tests).
        clock: Wall clock, injectable for tests.
        sleep: Async sleep, injectable for tests.
    """

    def __init__(
        self,
        store: Store,
        vault: Vault,
        config: RuntimeConfig,
        providers: ProviderService,
        *,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Any] = asyncio.sleep,
    ) -> None:
        self._store = store
        self._vault = vault
        self._config = config
        self._providers = providers
        self._clock = clock
        self._sleep = sleep
        self._last_entry: dict[str, str] = {}

    @property
    def http_factory(self) -> HttpFactory:
        """HTTP client factory shared with the provider service."""
        return self._providers.http_factory

    # Resolution

    def _entry_from_row(self, row: dict[str, Any], providers: dict[int, Provider]) -> Entry:
        model = self._store.model_get(row["provider_id"], row["model_id"])
        return Entry(
            id=row["id"], provider=providers[row["provider_id"]], model=row["model_id"], key_id=row["key_id"],
            timeouts=row["timeouts"], context_window=model["context_window"] if model else None,
            thinking_control=model["thinking_control"] if model else "none",
            model_available=bool(model["available"]) if model else True,
        )

    def route_entries(self, name: str) -> list[Entry]:
        """Return the resolved entries of a named route (no fallback to another route)."""
        if not self._store.route_exists(name):
            raise RouteError(f"unknown route {name!r}")
        providers = {p.id: p for p in self._providers.list()}
        return [self._entry_from_row(r, providers) for r in self._store.route_entries(name)]

    def resolve(self, spec: str | None, default_route: str = "default") -> list[Entry]:
        """Resolve a route name, a pinned ``provider/model`` or an empty spec.

        Args:
            spec: Route name, ``provider/model`` or empty for ``default_route``.
            default_route: The gateway's route.

        Raises:
            NoProvider: If nothing is configured.
            RouteError: If the route is unknown or empty.
        """
        spec = (spec or "").strip() or default_route
        if spec == self._config.get("RAG_VISION_ROUTE"):
            raise RouteError(f"the {spec!r} route is reserved for reading images for the knowledge base, not for chat")
        if "/" in spec:
            slug, _, model = spec.partition("/")
            provider = self._providers.by_slug(slug)
            if provider is None or not model:
                raise RouteError(f"unknown provider/model {spec!r}")
            row = self._store.model_get(provider.id, model)
            return [
                Entry(
                    id=None, provider=provider, model=model,
                    context_window=row["context_window"] if row else None,
                    thinking_control=row["thinking_control"] if row else "none",
                    model_available=bool(row["available"]) if row else True,
                )
            ]
        entries = self.route_entries(spec)
        if not entries and spec != "default":
            entries = self.route_entries("default")
        if not entries:
            raise NoProvider()
        return entries

    # Status of entries

    def _budget_block(self, provider: Provider, now: float) -> tuple[float, str] | None:
        if provider.budget_daily is not None:
            start = _day_start(now)
            if self._store.usage_tokens_since(provider.id, start) >= provider.budget_daily:
                return start + 86400, "daily token budget reached"
        if provider.budget_monthly is not None:
            start, end = _month_bounds(now)
            if self._store.usage_tokens_since(provider.id, start) >= provider.budget_monthly:
                return end, "monthly token budget reached"
        return None

    def budget_block(self, provider: Provider) -> str | None:
        """Why the provider may not be used now because of its token budget, or ``None``."""
        block = self._budget_block(provider, self._clock())
        return block[1] if block else None

    def _status(self, entry: Entry, now: float, *, ignore_cooldown: bool = False) -> _Status:
        """Compute whether an entry can be tried now, and with which keys."""
        if not entry.provider.enabled:
            return _Status([], _INF, "provider disabled")
        if not entry.model_available:
            return _Status([], _INF, "model missing")
        health = self._store.health_get("entry", entry.ref)
        if health and health["state"] == "disabled":
            return _Status([], _INF, health["reason"] or "disabled")
        if not ignore_cooldown:
            if health and health["until"] and health["until"] > now:
                return _Status([], health["until"], health["reason"] or "cooling down")
            if block := self._budget_block(entry.provider, now):
                return _Status([], block[0], block[1])

        rows = self._store.key_list(entry.provider.id)
        if entry.key_id is not None:
            rows = [r for r in rows if r["id"] == entry.key_id]
            if not rows:
                return _Status([], _INF, "pinned key missing")
        if not rows:
            return _Status([None])
        usable: list[int | None] = []
        soonest = _INF
        reason = "all keys unavailable"
        for row in rows:
            key_health = self._store.health_get("key", str(row["id"]))
            if key_health and key_health["state"] == "disabled":
                reason = "invalid key"
                continue
            if not ignore_cooldown and key_health and key_health["until"] and key_health["until"] > now:
                if key_health["until"] < soonest:
                    soonest, reason = key_health["until"], key_health["reason"] or reason
                continue
            usable.append(row["id"])
        if usable:
            return _Status(usable)
        return _Status([], soonest, reason)

    def _plan(self, entries: list[Entry]) -> tuple[list[tuple[Entry, _Status]], list[tuple[str, str]]]:
        """Entries to try in order, plus skip reasons for the ones in cooldown.

        When every entry is cooling down, the one closest to recovery is tried.
        """
        now = self._clock()
        statuses = [(e, self._status(e, now)) for e in entries]
        usable = [(e, s) for e, s in statuses if s.keys]
        skipped = [(e.provider.slug, s.reason) for e, s in statuses if not s.keys]
        if usable:
            return usable, skipped
        recoverable = [(e, s) for e, s in statuses if s.blocked_until is not None and s.blocked_until != _INF]
        if recoverable:
            entry, _ = min(recoverable, key=lambda p: p[1].blocked_until or 0)
            forced = self._status(entry, now, ignore_cooldown=True)
            if forced.keys:
                return [(entry, forced)], [(e.provider.slug, s.reason) for e, s in statuses if e is not entry]
        return [], skipped

    # Health and usage writes (run in worker threads)

    def _cooldown(self, outcome: Outcome, exc: LLMError, failures: int) -> float:
        if outcome is Outcome.QUOTA:
            return exc.retry_after if exc.retry_after else float(self._config.get("COOLDOWN_QUOTA"))
        if outcome is Outcome.RATE_LIMIT:
            return exc.retry_after if exc.retry_after else _UNAVAILABLE_BASE
        if outcome is Outcome.TIMEOUT:
            return _TIMEOUT_COOLDOWN
        if outcome is Outcome.AUTH:
            return _AUTH_ENTRY_COOLDOWN
        return min(_UNAVAILABLE_BASE * 2 ** max(0, failures - 1), _UNAVAILABLE_MAX)

    def _mark_entry(self, entry: Entry, exc: LLMError) -> None:
        row = self._store.health_get("entry", entry.ref)
        failures = (row["failures"] if row else 0) + 1
        cooldown = self._cooldown(exc.outcome, exc, failures)
        self._store.health_set(
            "entry", entry.ref, "cooldown", self._clock() + cooldown, failures, _REASONS[exc.outcome], str(exc)
        )
        logger.warning("%s: %s (cooling down for %d s)", entry.label, _REASONS[exc.outcome], cooldown)

    def _mark_key(self, key_id: int, exc: LLMError) -> None:
        row = self._store.health_get("key", str(key_id))
        failures = (row["failures"] if row else 0) + 1
        if exc.outcome is Outcome.AUTH:
            self._store.health_set("key", str(key_id), "disabled", None, failures, "invalid key", str(exc))
            self._store.journal_add("settings", "provider_key", f"key #{key_id} marked invalid ({exc})")
            logger.error("Provider key #%d was rejected and is now disabled", key_id)
            return
        cooldown = self._cooldown(exc.outcome, exc, failures)
        self._store.health_set(
            "key", str(key_id), "cooldown", self._clock() + cooldown, failures, _REASONS[exc.outcome], str(exc)
        )

    def _mark_success(self, entry: Entry, key_id: int | None) -> None:
        if self._store.health_get("entry", entry.ref):
            self._store.health_clear("entry", entry.ref)
        if key_id is not None and (row := self._store.health_get("key", str(key_id))) and row["state"] == "cooldown":
            self._store.health_clear("key", str(key_id))

    def _record(
        self, walk: _Walk, entry: Entry, key_id: int | None, outcome: str, latency_ms: int,
        result: ChatResult | None = None, error: str = "",
    ) -> None:
        self._store.usage_add(
            {
                "ts": self._clock(), "provider_id": entry.provider.id, "provider": entry.provider.slug,
                "model": entry.model, "key_id": key_id, "route": walk.route, "source": walk.source,
                "agent": walk.agent, "tokens_in": result.usage.prompt if result else 0,
                "tokens_out": result.usage.completion if result else 0, "latency_ms": latency_ms,
                "outcome": outcome, "error": error[:300], "fallback": int(entry.ref != walk.primary.ref),
            }
        )

    # Endpoint

    def _endpoint(self, entry: Entry, key_id: int | None) -> Endpoint:
        p = entry.provider
        key = self._providers.key_value(key_id) if key_id is not None else None
        t = entry.timeouts
        return Endpoint(
            provider_type=p.type, chat_url=p.chat_url, model=entry.model, api_key=key, headers=p.headers,
            extra_body=p.extra_body, verify=p.verify_tls, connect=t.get("connect", p.connect),
            first_token=t.get("first_token", p.first_token), stall=t.get("stall", p.stall),
            total=t.get("total", p.total), thinking_control=entry.thinking_control,
        )

    # Walking

    async def chat(
        self,
        entries: list[Entry],
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        route: str = "default",
        session_key: str | None = None,
        source: str = "",
        agent: str | None = None,
        allow_midstream_retry: bool = True,
        thinking: bool | None = None,
    ) -> AsyncIterator[Token | Reset | Notice | Completed]:
        """Run one model call over a route.

        Failover normally happens before the first token. If a stream breaks after
        output started and ``allow_midstream_retry`` is true, a :class:`Reset` is
        yielded and the call is retried once on the next entry; otherwise the
        :class:`~aifred.llm.LLMError` (with ``started`` set) is raised.

        Yields:
            :class:`Token`, :class:`Reset`, :class:`Notice` and finally :class:`Completed`.

        Raises:
            RouteExhausted: If every entry failed.
            LLMError: For a request the provider rejected (no failover), a broken
                stream that may not be retried, or a context that fits no entry.
        """
        if not entries:
            raise NoProvider()
        walk = _Walk(
            route=route, source=source, agent=agent, primary=entries[0],
            allow_midstream_retry=allow_midstream_retry,
            thinking=(self._config.get("LLM_THINKING") == "on") if thinking is None else thinking,
            deadline=self._clock() + float(self._config.get("ROUTE_DEADLINE")),
        )
        plan, skipped = await asyncio.to_thread(self._plan, entries)
        walk.failures.extend((slug, reason) for slug, reason in skipped)
        failed_labels: list[tuple[str, str]] = []

        for entry, status in plan:
            if walk.attempts and self._clock() >= walk.deadline:
                failed_labels.append(("route", "time budget exhausted"))
                break
            if walk.min_context is not None and (entry.context_window or 0) <= walk.min_context:
                continue
            done: Completed | None = None
            async for event in self._run_entry(entry, status, messages, tools, walk):
                if isinstance(event, Completed):
                    done = event
                elif not isinstance(event, _Failed):
                    yield event
            if done is not None:
                if notice := self._switch_notice(done.entry, walk, session_key):
                    yield Notice(notice)
                yield done
                return

        if walk.context_failed and not failed_labels and all(r == "context too long" for _, r in walk.failures[len(skipped):]):
            raise LLMError(
                "The conversation is too long for every model in the route", Outcome.CONTEXT
            )
        raise RouteExhausted([*walk.failures, *failed_labels])

    def _switch_notice(self, entry: Entry, walk: _Walk, session_key: str | None) -> str | None:
        previous = self._last_entry.get(session_key or "")
        if session_key:
            if len(self._last_entry) > 1000:
                self._last_entry.pop(next(iter(self._last_entry)))
            self._last_entry[session_key] = entry.ref
        if entry.ref == walk.primary.ref or previous == entry.ref:
            return None
        reasons = "; ".join(dict.fromkeys(f"{slug}: {reason}" for slug, reason in walk.failures))
        return f"Switched to {entry.label}" + (f" ({reasons})" if reasons else "")

    async def _run_entry(
        self, entry: Entry, status: _Status, messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None, walk: _Walk,
    ) -> AsyncIterator[Token | Reset | Completed | _Failed]:
        for key_id in status.keys:
            retried = False
            while True:
                walk.attempts += 1
                began = self._clock()
                emitted = False
                result: ChatResult | None = None
                try:
                    try:
                        endpoint = await asyncio.to_thread(self._endpoint, entry, key_id)
                    except VaultUnavailable as exc:
                        # a sealed or unreachable secret store says nothing about the key itself: no cooldown, no failover
                        raise LLMError(f"the secret store is unavailable ({exc})", Outcome.FATAL) from None
                    async for ev in stream_chat(
                        endpoint, messages, tools,
                        temperature=float(self._config.get("LLM_TEMPERATURE")),
                        global_extra=self._config.get("LLM_EXTRA_BODY"), thinking=walk.thinking,
                        http_factory=self.http_factory,
                    ):
                        if isinstance(ev, Token):
                            emitted = True
                            yield ev
                        elif isinstance(ev, Done):
                            result = ev.result
                except LLMError as exc:
                    latency = int((self._clock() - began) * 1000)
                    exc.started = exc.started or emitted
                    await asyncio.to_thread(
                        self._record, walk, entry, key_id, exc.outcome.value, latency, None, str(exc)
                    )
                    action = await self._handle_failure(entry, key_id, exc, walk, retried)
                    if action == "retry":
                        retried = True
                        continue
                    if action == "raise":
                        raise
                    if exc.started:
                        yield Reset()
                    if action == "next_key":
                        break
                    yield _Failed()
                    return
                assert result is not None
                latency = int((self._clock() - began) * 1000)
                await asyncio.to_thread(self._record, walk, entry, key_id, "success", latency, result)
                await asyncio.to_thread(self._mark_success, entry, key_id)
                yield Completed(result, entry)
                return
        yield _Failed()

    async def _handle_failure(
        self, entry: Entry, key_id: int | None, exc: LLMError, walk: _Walk, retried: bool
    ) -> str:
        """Apply the failover table; returns ``retry``, ``next_key``, ``next_entry`` or ``raise``."""
        outcome = exc.outcome
        reason = _REASONS[outcome]
        if exc.started:
            if not walk.allow_midstream_retry or walk.midstream_used:
                await asyncio.to_thread(self._mark_entry, entry, exc)
                return "raise"
            walk.midstream_used = True
            reason = f"stream interrupted ({reason})"
        elif outcome is Outcome.FATAL:
            return "raise"

        if outcome is Outcome.RATE_LIMIT and not retried and not exc.started:
            wait = exc.retry_after
            if wait is not None and wait <= _SHORT_RATE_LIMIT:
                await self._sleep(wait)
                return "retry"

        walk.failures.append((entry.provider.slug, reason))

        if outcome is Outcome.CONTEXT:
            walk.context_failed = True
            walk.min_context = entry.context_window or 0
            return "next_entry"
        if outcome is Outcome.MODEL_MISSING:
            await asyncio.to_thread(self._store.model_set_available, entry.provider.id, entry.model, False)
            return "next_entry"
        if outcome in (Outcome.QUOTA, Outcome.AUTH) and key_id is not None and not exc.started:
            await asyncio.to_thread(self._mark_key, key_id, exc)
            return "next_key"
        await asyncio.to_thread(self._mark_entry, entry, exc)
        return "next_entry"

    async def complete(
        self, entries: list[Entry], messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None, **kwargs: Any
    ) -> tuple[ChatResult, Entry, list[str]]:
        """Run a call to completion without streaming.

        Returns:
            The result, the serving entry and any notices (route switches).
        """
        notices: list[str] = []
        async for ev in self.chat(entries, messages, tools, **kwargs):
            if isinstance(ev, Notice):
                notices.append(ev.text)
            elif isinstance(ev, Completed):
                return ev.result, ev.entry, notices
        raise RouteError("the model returned no result")  # pragma: no cover - chat always completes or raises

    # Management

    def set_route(self, name: str, entries: list[dict[str, Any]]) -> list[Entry]:
        """Replace a route's entries.

        Args:
            name: Route name (created if new).
            entries: Dicts with ``provider`` (slug or id), ``model``, optional ``key_id`` and ``timeouts``.

        Raises:
            ProviderError: If the name or an entry is invalid, or a model has not passed
                the tool-calling test.
        """
        if not SLUG_RE.match(name):
            raise ProviderError("route names use lowercase letters, digits, - and _ (1-32 characters)")
        if len(entries) > MAX_ROUTE_ENTRIES:
            raise ProviderError(f"a route has at most {MAX_ROUTE_ENTRIES} entries")
        rows: list[dict[str, Any]] = []
        for i, e in enumerate(entries, start=1):
            ref = e.get("provider")
            provider = self._providers.by_slug(ref) if isinstance(ref, str) else self._providers.get(int(ref))
            if provider is None:
                raise ProviderError(f"entry {i}: unknown provider {ref!r}")
            model_id = str(e.get("model", "")).strip()
            model = self._store.model_get(provider.id, model_id)
            if model is None:
                raise ProviderError(f"entry {i}: {provider.slug}/{model_id} is not in the provider's model list")
            if model["kind"] != "chat":
                raise ProviderError(f"entry {i}: {provider.slug}/{model_id} is an embedding model; routes carry chat models")
            if name == self._config.get("RAG_VISION_ROUTE"):
                # the vision route reads images for the knowledge base: it needs a model that can read, not one that calls tools
                if not (model["vision"] and model["vision_test_at"]):
                    raise ProviderError(
                        f"entry {i}: {provider.slug}/{model_id} has not passed the vision test; run Test vision on the model first"
                    )
            elif not model["test_ok"]:
                raise ProviderError(
                    f"entry {i}: {provider.slug}/{model_id} has not passed the tool-calling test; run Test on the model first"
                )
            key_id = e.get("key_id")
            if key_id is not None:
                key = self._store.key_get(int(key_id))
                if key is None or key["provider_id"] != provider.id:
                    raise ProviderError(f"entry {i}: the key does not belong to {provider.slug}")
            timeouts = {}
            for name_ in ("connect", "first_token", "stall", "total"):
                if (value := (e.get("timeouts") or {}).get(name_)) not in (None, ""):
                    try:
                        timeouts[name_] = float(value)
                    except (TypeError, ValueError):
                        raise ProviderError(f"entry {i}: timeout {name_} must be a number") from None
                    if not 1 <= timeouts[name_] <= 3600:
                        raise ProviderError(f"entry {i}: timeout {name_} must be between 1 and 3600 seconds")
            rows.append(
                {"provider_id": provider.id, "model_id": model_id, "key_id": int(key_id) if key_id is not None else None, "timeouts": timeouts}
            )
        self._store.route_create(name)
        self._store.route_set_entries(name, rows)
        self._store.journal_add("settings", "route", f"route {name!r} set to {len(rows)} entr{'y' if len(rows) == 1 else 'ies'}")
        return self.route_entries(name)

    def delete_route(self, name: str) -> None:
        """Delete a custom route.

        Raises:
            ProviderError: For the protected routes or an unknown name.
        """
        if name in PROTECTED_ROUTES:
            raise ProviderError(f"the {name!r} route cannot be deleted")
        if not self._store.route_delete(name):
            raise ProviderError(f"unknown route {name!r}")
        self._store.journal_add("settings", "route", f"route {name!r} deleted")

    def reset_health(self, entry_id: int) -> None:
        """Clear the cooldown state of a route entry's provider/model and keys.

        Raises:
            ProviderError: If the entry does not exist.
        """
        row = self._store.route_entry_get(entry_id)
        if row is None:
            raise ProviderError("route entry not found")
        keys = [k["id"] for k in self._store.key_list(row["provider_id"])]
        self._store.health_clear("entry", f"{row['provider_id']}|{row['model_id']}")
        for key_id in keys:
            self._store.health_clear("key", str(key_id))
        self._store.model_set_available(row["provider_id"], row["model_id"], True)

    def health_snapshot(self) -> list[dict[str, Any]]:
        """Per-provider health, usage today and route entries for the Models page."""
        now = self._clock()
        today = self._store.usage_provider_summary(_day_start(now))
        out = []
        for provider in self._providers.list():
            summary = today.get(provider.id, {})
            keys = []
            for k in self._store.key_list(provider.id):
                h = self._store.health_get("key", str(k["id"]))
                keys.append(
                    {"id": k["id"], "label": k["label"], "last4": k["last4"], **self._state(h, now)}
                )
            entries = []
            for name in self._store.route_names():
                for r in self._store.route_entries(name):
                    if r["provider_id"] != provider.id:
                        continue
                    h = self._store.health_get("entry", f"{provider.id}|{r['model_id']}")
                    entries.append(
                        {
                            "route": name, "entry_id": r["id"], "model": r["model_id"],
                            "model_available": bool(r["model_available"]) if r["model_available"] is not None else True,
                            **self._state(h, now),
                        }
                    )
            last = self._store.usage_last_error(provider.id)
            out.append(
                {
                    "provider_id": provider.id, "provider": provider.slug, "enabled": provider.enabled,
                    "tokens_today": int(summary.get("tokens") or 0), "calls_today": int(summary.get("calls") or 0),
                    "errors_today": int(summary.get("errors") or 0), "fallbacks_today": int(summary.get("fallbacks") or 0),
                    "budget_tokens_daily": provider.budget_daily, "budget_tokens_monthly": provider.budget_monthly,
                    "last_error": last, "keys": keys, "entries": entries,
                }
            )
        return out

    @staticmethod
    def _state(health: dict[str, Any] | None, now: float) -> dict[str, Any]:
        if health is None:
            return {"state": "healthy", "until": None, "reason": "", "last_error": ""}
        if health["state"] == "disabled":
            return {"state": "disabled", "until": None, "reason": health["reason"], "last_error": health["last_error"]}
        if health["until"] and health["until"] > now:
            return {"state": "cooldown", "until": health["until"], "reason": health["reason"], "last_error": health["last_error"]}
        return {"state": "healthy", "until": None, "reason": "", "last_error": health["last_error"]}

    def usage_overview(self, days: int = 7) -> dict[str, Any]:
        """Tokens, calls and cost per provider and model, and tokens per day.

        The cost of a model is ``tokens_in * price_in + tokens_out * price_out`` (prices are per million tokens). A model with no
        price set counts tokens but has no cost; a provider's cost is ``None`` until at least one of its models has a price.
        """
        since = self._clock() - days * 86400
        providers: dict[str, dict[str, Any]] = {}
        for row in self._store.usage_overview(since):
            priced = row["price_in"] is not None or row["price_out"] is not None
            cost = (row["tokens_in"] * (row["price_in"] or 0) + row["tokens_out"] * (row["price_out"] or 0)) / 1_000_000 if priced else None
            entry = providers.setdefault(row["provider"], {"provider": row["provider"], "calls": 0, "errors": 0, "tokens_in": 0, "tokens_out": 0, "cost": None, "unpriced_models": 0, "models": []})
            for key in ("calls", "errors", "tokens_in", "tokens_out"):
                entry[key] += row[key]
            if cost is None:
                entry["unpriced_models"] += 1
            else:
                entry["cost"] = (entry["cost"] or 0) + cost
            entry["models"].append({"model": row["model"], "calls": row["calls"], "errors": row["errors"], "tokens_in": row["tokens_in"], "tokens_out": row["tokens_out"],
                                    "price_in": row["price_in"], "price_out": row["price_out"], "cost": cost})
        rows = sorted(providers.values(), key=lambda p: p["tokens_in"] + p["tokens_out"], reverse=True)
        costs = [p["cost"] for p in rows if p["cost"] is not None]
        totals = {"calls": sum(p["calls"] for p in rows), "errors": sum(p["errors"] for p in rows), "tokens_in": sum(p["tokens_in"] for p in rows),
                  "tokens_out": sum(p["tokens_out"] for p in rows), "cost": sum(costs) if costs else None, "unpriced_models": sum(p["unpriced_models"] for p in rows)}
        return {"days": days, "providers": rows, "totals": totals, "daily": self._store.usage_daily(since), "by_day": self._usage_by_day(since)}

    def _usage_by_day(self, since: float) -> list[dict[str, Any]]:
        """Per day (newest first) with activity: totals and one entry per provider, priced like ``usage_overview``."""
        days: dict[str, dict[str, Any]] = {}
        for row in self._store.usage_daily_models(since):
            priced = row["price_in"] is not None or row["price_out"] is not None
            cost = (row["tokens_in"] * (row["price_in"] or 0) + row["tokens_out"] * (row["price_out"] or 0)) / 1_000_000 if priced else None
            day = days.setdefault(row["day"], {"day": row["day"], "calls": 0, "errors": 0, "tokens_in": 0, "tokens_out": 0, "cost": None, "_providers": {}})
            prov = day["_providers"].setdefault(row["provider"], {"provider": row["provider"], "calls": 0, "errors": 0, "tokens_in": 0, "tokens_out": 0, "cost": None})
            for entry in (day, prov):
                for key in ("calls", "errors", "tokens_in", "tokens_out"):
                    entry[key] += row[key]
                if cost is not None:
                    entry["cost"] = (entry["cost"] or 0) + cost
        out = []
        for day in days.values():
            providers = sorted(day.pop("_providers").values(), key=lambda p: p["tokens_in"] + p["tokens_out"], reverse=True)
            out.append({**day, "providers": providers})
        return sorted(out, key=lambda d: d["day"], reverse=True)

    def usage_report(self, days: int = 7) -> list[dict[str, Any]]:
        """Usage grouped by day, provider, model and agent."""
        return self._store.usage_report(self._clock() - days * 86400)
