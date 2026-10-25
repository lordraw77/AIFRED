"""LLM providers, API keys and models (spec 6.1).

Provider API keys are stored encrypted in the vault under ``provider_key:<id>``;
the database only keeps the last four characters for display.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from aifred.llm import (
    Done,
    Endpoint,
    HttpFactory,
    LLMError,
    default_http_factory,
    stream_chat,
)
from aifred.store import Store
from aifred.vault import Vault, VaultUnavailable

logger = logging.getLogger(__name__)

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
MODEL_ID_RE = re.compile(r"^[^\s\x00-\x1f]{1,200}$")
THINKING_CONTROLS = ("none", "qwen_template", "reasoning_effort")
_URL_RE = re.compile(r"^https?://[^\s/?#]+[^\s?#]*$")
_ACCOUNT_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass(frozen=True)
class Preset:
    """A provider type with its editable defaults.

    Attributes:
        type: Type slug stored on the provider.
        label: Display name.
        base_url: Default base URL (may contain ``{account_id}``).
        key: Whether an API key is normally needed.
        notes: Quirks shown in the dashboard.
        headers: Suggested extra headers.
        config_fields: Extra fields the type needs (stored in ``config``).
        first_token: Default first-token timeout (s); local engines load models on demand.
    """

    type: str
    label: str
    base_url: str
    key: bool = True
    notes: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    config_fields: tuple[str, ...] = ()
    first_token: float = 30.0


# [VERIFY] URLs and quirks are re-checked against each provider's documentation before release
PRESETS: dict[str, Preset] = {
    p.type: p
    for p in [
        Preset("local", "Local OpenAI-compatible", "", key=False, notes="vLLM, llama.cpp, LM Studio; for example http://10.70.0.30:8000/v1", first_token=120.0),
        Preset("ollama", "Ollama", "http://localhost:11434", key=False, notes="Chat through /v1; model list from /api/tags; a cold model may take a while to load", first_token=120.0),
        Preset("groq", "Groq", "https://api.groq.com/openai/v1"),
        Preset(
            "openrouter", "OpenRouter", "https://openrouter.ai/api/v1",
            notes="Model ids look like vendor/model, including :free variants",
            headers={"HTTP-Referer": "", "X-Title": "AIFRED"},
        ),
        Preset("gemini", "Google Gemini", "https://generativelanguage.googleapis.com/v1beta/openai"),
        Preset(
            "cloudflare", "Cloudflare Workers AI",
            "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1",
            notes="Needs the account id", config_fields=("account_id",),
        ),
        Preset("cerebras", "Cerebras", "https://api.cerebras.ai/v1"),
        Preset("mistral", "Mistral", "https://api.mistral.ai/v1", notes="Tool-call ids are rewritten to 9 alphanumeric characters"),
        Preset("nvidia", "NVIDIA", "https://integrate.api.nvidia.com/v1"),
        Preset("huggingface", "Hugging Face", "https://router.huggingface.co/v1", notes="A model id may carry a :provider suffix"),
        Preset("custom", "Custom OpenAI-compatible", "", key=False, notes="Anything with a chat-completions API"),
    ]
}


class ProviderError(ValueError):
    """Invalid provider, key, model or route data."""


@dataclass(frozen=True)
class Provider:
    """A configured provider (public fields; keys are separate).

    Attributes:
        id: Database id.
        slug: Unique name.
        type: Preset type.
        base_url: Base URL as stored.
        headers: Extra request headers (not secret).
        extra_body: Extra request body fields.
        config: Type-specific fields such as ``account_id``.
        connect: Connect timeout (s).
        first_token: First-token timeout (s).
        stall: Stall timeout (s).
        total: Total timeout (s).
        verify_tls: ``True``, ``False`` or a PEM CA bundle path.
        enabled: Whether the provider is used.
        budget_daily: Daily token budget.
        budget_monthly: Monthly token budget.
    """

    id: int
    slug: str
    type: str
    base_url: str
    headers: dict[str, str]
    extra_body: dict[str, Any]
    config: dict[str, Any]
    connect: float
    first_token: float
    stall: float
    total: float
    verify_tls: bool | str
    enabled: bool
    budget_daily: int | None
    budget_monthly: int | None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> Provider:
        """Build from a database row."""
        raw = row["verify_tls"]
        verify: bool | str = {"true": True, "false": False}.get(raw, raw)
        return cls(
            id=row["id"], slug=row["slug"], type=row["type"], base_url=row["base_url"],
            headers=row["headers"], extra_body=row["extra_body"], config=row["config"],
            connect=row["connect_timeout"], first_token=row["first_token_timeout"],
            stall=row["stall_timeout"], total=row["total_timeout"], verify_tls=verify,
            enabled=bool(row["enabled"]), budget_daily=row["budget_tokens_daily"],
            budget_monthly=row["budget_tokens_monthly"],
        )

    @property
    def chat_url(self) -> str:
        """Chat-completions URL (Ollama is reached through its OpenAI-compatible ``/v1``)."""
        base = self.base_url.rstrip("/")
        if self.type == "ollama" and not base.endswith("/v1"):
            base += "/v1"
        return f"{base}/chat/completions"

    @property
    def models_url(self) -> str:
        """URL that lists the provider's models."""
        base = self.base_url.rstrip("/")
        if self.type == "ollama":
            return re.sub(r"/v1$", "", base) + "/api/tags"
        if self.type == "cloudflare":
            # the OpenAI-compatible endpoint has no GET /models; the catalogue lives next to it
            return re.sub(r"/v1$", "", base) + "/models/search?task=Text%20Generation&per_page=100"
        return f"{base}/models"


_TIMEOUT_LIMITS = {
    "connect_timeout": (1.0, 120.0),
    "first_token_timeout": (1.0, 600.0),
    "stall_timeout": (1.0, 600.0),
    "total_timeout": (5.0, 3600.0),
}
_TIMEOUT_DEFAULTS = {"connect_timeout": 10.0, "first_token_timeout": 30.0, "stall_timeout": 60.0, "total_timeout": 300.0}


def _verify_to_db(value: Any) -> str:
    if value is True or value in ("true", None):
        return "true"
    if value is False or value == "false":
        return "false"
    if isinstance(value, str) and value.strip():
        path = Path(value.strip())
        if not path.is_file():
            raise ProviderError(f"verify_tls: CA bundle {value!r} does not exist")
        return str(path)
    raise ProviderError("verify_tls must be true, false or the path of a PEM CA bundle")


def _validate_headers(headers: Any) -> dict[str, str]:
    if not isinstance(headers, dict):
        raise ProviderError("headers must be an object")
    out: dict[str, str] = {}
    for name, value in headers.items():
        if not re.fullmatch(r"[A-Za-z0-9-]{1,64}", str(name)):
            raise ProviderError(f"invalid header name {name!r}")
        if str(name).lower() in ("authorization", "content-type", "accept", "host", "content-length"):
            raise ProviderError(f"header {name!r} is managed by AIFRED")
        if value in (None, ""):
            continue
        if not isinstance(value, str) or len(value) > 500 or re.search(r"[\r\n]", value):
            raise ProviderError(f"invalid value for header {name!r}")
        out[str(name)] = value
    return out


def _validate_budget(value: Any, name: str) -> int | None:
    if value in (None, ""):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ProviderError(f"{name} must be a whole number") from None
    if number < 1:
        raise ProviderError(f"{name} must be positive")
    return number


class ProviderService:
    """Provider, key and model management.

    Args:
        store: Database.
        vault: Encrypted secrets.
        http_factory: Builds the HTTP client for each call (replaced in tests).
    """

    def __init__(self, store: Store, vault: Vault, http_factory: HttpFactory = default_http_factory) -> None:
        self._store = store
        self._vault = vault
        self.http_factory = http_factory

    # Reading

    def get(self, provider_id: int) -> Provider:
        """Return a provider.

        Raises:
            ProviderError: If it does not exist.
        """
        row = self._store.provider_get(provider_id)
        if row is None:
            raise ProviderError(f"provider {provider_id} not found")
        return Provider.from_row(row)

    def by_slug(self, slug: str) -> Provider | None:
        """Return a provider by slug."""
        row = self._store.provider_by_slug(slug)
        return Provider.from_row(row) if row else None

    def list(self) -> list[Provider]:
        """Return all providers."""
        return [Provider.from_row(r) for r in self._store.provider_list()]

    def public(self, provider: Provider) -> dict[str, Any]:
        """Dashboard view: configuration plus keys as ``{id, label, last4, ...}`` (never values)."""
        keys = []
        for k in self._store.key_list(provider.id):
            health = self._store.health_get("key", str(k["id"]))
            keys.append(
                {
                    "id": k["id"], "label": k["label"], "last4": k["last4"], "set": self._vault.exists(self._secret(k["id"])),
                    "status": "invalid" if health and health["state"] == "disabled" else "ok",
                }
            )
        return {
            "id": provider.id, "slug": provider.slug, "type": provider.type, "base_url": provider.base_url,
            "headers": provider.headers, "extra_body": provider.extra_body, "config": provider.config,
            "connect_timeout": provider.connect, "first_token_timeout": provider.first_token,
            "stall_timeout": provider.stall, "total_timeout": provider.total,
            "verify_tls": provider.verify_tls, "enabled": provider.enabled,
            "budget_tokens_daily": provider.budget_daily, "budget_tokens_monthly": provider.budget_monthly,
            "keys": keys, "tls_unverified": provider.verify_tls is False,
        }

    # Writing

    def _normalise(self, data: dict[str, Any], current: Provider | None) -> dict[str, Any]:
        """Validate input fields and return database column values."""
        out: dict[str, Any] = {}
        ptype = data.get("type", current.type if current else None)
        if ptype not in PRESETS:
            raise ProviderError(f"unknown provider type {ptype!r}; use one of: {', '.join(PRESETS)}")
        out["type"] = ptype
        preset = PRESETS[ptype]

        if "slug" in data or current is None:
            slug = str(data.get("slug", "")).strip().lower()
            if not SLUG_RE.match(slug):
                raise ProviderError("slug must be 1-32 characters: lowercase letters, digits, - and _")
            out["slug"] = slug

        config = dict(current.config if current else {})
        config.update({k: v for k, v in (data.get("config") or {}).items() if k in preset.config_fields})
        for name in preset.config_fields:
            value = str(config.get(name, "")).strip()
            if not _ACCOUNT_RE.match(value):
                raise ProviderError(f"{name} is required for {preset.label}")
            config[name] = value
        out["config"] = config

        base = data.get("base_url")
        if base is None and current is not None:
            # a changed account id must rebuild a templated URL
            base = preset.base_url if preset.config_fields and "config" in data else current.base_url
        if not base:
            base = preset.base_url
        base = str(base or "").strip()
        if "{account_id}" in base:
            base = base.replace("{account_id}", config.get("account_id", ""))
        base = base.rstrip("/")
        if not _URL_RE.match(base):
            raise ProviderError("base_url must be an http:// or https:// URL")
        out["base_url"] = base

        if "headers" in data or current is None:
            out["headers"] = _validate_headers(data.get("headers") or {})
        if "extra_body" in data or current is None:
            extra = data.get("extra_body") or {}
            if not isinstance(extra, dict):
                raise ProviderError("extra_body must be an object")
            out["extra_body"] = extra
        for column, (low, high) in _TIMEOUT_LIMITS.items():
            if column in data or current is None:
                default = preset.first_token if column == "first_token_timeout" else _TIMEOUT_DEFAULTS[column]
                raw = data.get(column, default)
                try:
                    value = float(raw)
                except (TypeError, ValueError):
                    raise ProviderError(f"{column} must be a number") from None
                if not low <= value <= high:
                    raise ProviderError(f"{column} must be between {low:g} and {high:g}")
                out[column] = value
        if "verify_tls" in data or current is None:
            out["verify_tls"] = _verify_to_db(data.get("verify_tls", True))
        if "enabled" in data:
            out["enabled"] = int(bool(data["enabled"]))
        for name in ("budget_tokens_daily", "budget_tokens_monthly"):
            if name in data:
                out[name] = _validate_budget(data[name], name)
        return out

    def preview(self, data: dict[str, Any]) -> Provider:
        """Validate new-provider data and return an unsaved :class:`Provider` (``id`` 0) for testing.

        Raises:
            ProviderError: On invalid input.
        """
        values = self._normalise(data, None)
        row = {
            "id": 0, "enabled": 1, "budget_tokens_daily": None, "budget_tokens_monthly": None,
            **values,
        }
        return Provider.from_row(row)

    def create(self, data: dict[str, Any], api_key: str | None = None, key_label: str = "") -> Provider:
        """Create a provider (and its first key). The caller tests the connection first.

        Raises:
            ProviderError: On invalid input or a duplicate slug.
        """
        values = self._normalise(data, None)
        if self._store.provider_by_slug(values["slug"]):
            raise ProviderError(f"a provider named {values['slug']!r} already exists")
        provider_id = self._store.provider_create(values)
        if api_key:
            self.add_key(provider_id, api_key, key_label)
        self._store.journal_add("settings", "provider", f"provider {values['slug']!r} ({values['type']}) added")
        return self.get(provider_id)

    def update(self, provider_id: int, data: dict[str, Any]) -> Provider:
        """Update a provider's configuration."""
        current = self.get(provider_id)
        values = self._normalise({**data, "type": current.type}, current)
        if "slug" in values and values["slug"] != current.slug:
            if self._store.provider_by_slug(values["slug"]):
                raise ProviderError(f"a provider named {values['slug']!r} already exists")
        before = current.verify_tls
        self._store.provider_update(provider_id, values)
        updated = self.get(provider_id)
        summary = f"provider {updated.slug!r} updated"
        if updated.verify_tls is False and before is not False:
            summary += " (TLS verification disabled)"
        self._store.journal_add("settings", "provider", summary)
        return updated

    def delete(self, provider_id: int) -> None:
        """Delete a provider with its keys, models and route entries."""
        provider = self.get(provider_id)
        for key_id in self._store.provider_delete(provider_id):
            self._vault.delete(self._secret(key_id))
        self._store.journal_add("settings", "provider", f"provider {provider.slug!r} deleted")

    # Keys

    @staticmethod
    def _secret(key_id: int) -> str:
        return f"provider_key:{key_id}"

    def add_key(self, provider_id: int, value: str, label: str = "") -> int:
        """Store a key encrypted; returns its id."""
        provider = self.get(provider_id)
        value = value.strip()
        if not value or len(value) > 2048 or re.search(r"\s", value):
            raise ProviderError("the API key is empty or malformed")
        key_id = self._store.key_create(provider_id, label.strip()[:64], value[-4:] if len(value) >= 8 else "")
        try:
            self._vault.set(self._secret(key_id), value)
        except VaultUnavailable as exc:
            self._store.key_delete(key_id)  # never leave a key row without its secret
            raise ProviderError(f"the key was not saved: the secret store is unavailable ({exc})") from None
        self._store.journal_add("settings", "provider_key", f"key added to provider {provider.slug!r}")
        return key_id

    def replace_key(self, key_id: int, value: str, label: str | None = None) -> None:
        """Replace a key's value; this also clears its invalid/cooldown state."""
        row = self._store.key_get(key_id)
        if row is None:
            raise ProviderError("key not found")
        value = value.strip()
        if not value or len(value) > 2048 or re.search(r"\s", value):
            raise ProviderError("the API key is empty or malformed")
        try:
            self._vault.set(self._secret(key_id), value)
        except VaultUnavailable as exc:
            raise ProviderError(f"the key was not replaced: the secret store is unavailable ({exc})") from None
        self._store.key_update(key_id, label=None if label is None else label.strip()[:64], last4=value[-4:] if len(value) >= 8 else "")
        self._store.health_clear("key", str(key_id))
        self._store.journal_add("settings", "provider_key", f"key #{key_id} replaced")

    def delete_key(self, provider_id: int, key_id: int) -> None:
        """Delete a key of a provider."""
        row = self._store.key_get(key_id)
        if row is None or row["provider_id"] != provider_id:
            raise ProviderError("key not found")
        self._store.key_delete(key_id)
        self._vault.delete(self._secret(key_id))
        self._store.journal_add("settings", "provider_key", f"key #{key_id} deleted")

    def key_value(self, key_id: int) -> str | None:
        """Return a decrypted key (internal use only)."""
        return self._vault.get(self._secret(key_id))

    # Models

    def models(self, provider_id: int) -> list[dict[str, Any]]:
        """Return a provider's models."""
        return [self._model_view(m) for m in self._store.model_list(provider_id)]

    @staticmethod
    def _model_view(row: dict[str, Any]) -> dict[str, Any]:
        def flag(v: Any) -> bool | None:
            return None if v is None else bool(v)

        return {
            "model_id": row["model_id"], "context_window": row["context_window"], "tools": flag(row["tools"]),
            "streaming": flag(row["streaming"]), "vision": flag(row["vision"]),
            "thinking_control": row["thinking_control"], "price_in": row["price_in"], "price_out": row["price_out"],
            "manual": bool(row["manual"]), "available": bool(row["available"]), "test_ok": flag(row["test_ok"]),
            "test_at": row["test_at"], "test_error": row["test_error"],
        }

    def save_models(self, provider_id: int, models: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Create or update models (manual entries and per-model overrides)."""
        self.get(provider_id)
        for m in models:
            model_id = str(m.get("model_id", "")).strip()
            if not MODEL_ID_RE.match(model_id):
                raise ProviderError(f"invalid model id {model_id!r}")
            values: dict[str, Any] = {}
            if "thinking_control" in m:
                if m["thinking_control"] not in THINKING_CONTROLS:
                    raise ProviderError(f"thinking_control must be one of: {', '.join(THINKING_CONTROLS)}")
                values["thinking_control"] = m["thinking_control"]
            if "context_window" in m:
                values["context_window"] = self._positive_int(m["context_window"], "context_window")
            for name in ("price_in", "price_out"):
                if name in m:
                    values[name] = None if m[name] in (None, "") else self._non_negative_float(m[name], name)
            for name in ("streaming", "vision"):
                if name in m:
                    values[name] = None if m[name] is None else int(bool(m[name]))
            existing = self._store.model_get(provider_id, model_id)
            self._store.model_save(provider_id, model_id, values, manual=existing is None)
        return self.models(provider_id)

    @staticmethod
    def _positive_int(value: Any, name: str) -> int | None:
        if value in (None, ""):
            return None
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise ProviderError(f"{name} must be a whole number") from None
        if number < 1:
            raise ProviderError(f"{name} must be positive")
        return number

    @staticmethod
    def _non_negative_float(value: Any, name: str) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise ProviderError(f"{name} must be a number") from None
        if number < 0:
            raise ProviderError(f"{name} must not be negative")
        return number

    def _client(self, provider: Provider, api_key: str | None) -> tuple[httpx.AsyncClient, dict[str, str]]:
        endpoint = self._endpoint(provider, "-", api_key)
        headers = {"Accept": "application/json", **provider.headers}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return self.http_factory(endpoint), headers

    def _endpoint(self, provider: Provider, model: str, api_key: str | None, control: str = "none") -> Endpoint:
        return Endpoint(
            provider_type=provider.type, chat_url=provider.chat_url, model=model, api_key=api_key,
            headers=provider.headers, extra_body=provider.extra_body, verify=provider.verify_tls,
            connect=provider.connect, first_token=provider.first_token, stall=provider.stall,
            total=provider.total, thinking_control=control,
        )

    def _first_key(self, provider: Provider) -> str | None:
        try:
            for k in self._store.key_list(provider.id):
                if value := self._vault.get(self._secret(k["id"])):
                    return value
        except VaultUnavailable as exc:
            raise ProviderError(f"the secret store is unavailable ({exc})") from None
        return None

    async def fetch_models(self, provider: Provider, api_key: str | None = None) -> list[dict[str, Any]]:
        """Fetch the provider's model list.

        Args:
            provider: Provider (not necessarily saved yet; ``id`` may be 0).
            api_key: Key to use; defaults to the provider's first stored key.

        Returns:
            Dicts with ``model_id`` and ``context_window`` (when advertised).

        Raises:
            ProviderError: With a readable reason if the listing fails.
        """
        key = api_key if api_key is not None else (self._first_key(provider) if provider.id else None)
        client, headers = self._client(provider, key)
        try:
            async with client:
                response = await client.get(provider.models_url, headers=headers)
        except httpx.HTTPError as exc:
            raise ProviderError(f"cannot reach {provider.models_url}: {type(exc).__name__}: {exc}"[:300]) from None
        if response.status_code in (401, 403):
            raise ProviderError(f"the endpoint rejected the API key (HTTP {response.status_code})")
        if response.status_code >= 400:
            raise ProviderError(f"{provider.models_url} answered HTTP {response.status_code}: {response.text[:200]}")
        try:
            return self._parse_models(response.json(), provider.type)
        except (ValueError, KeyError, TypeError):
            raise ProviderError(f"{provider.models_url} did not return a model list") from None

    @staticmethod
    def _parse_models(payload: Any, ptype: str = "custom") -> list[dict[str, Any]]:
        if isinstance(payload, dict) and ptype == "cloudflare":
            items = payload.get("result")  # entries have a UUID "id" and the model id in "name"
            payload = {"data": [{"id": i.get("name")} for i in items if isinstance(i, dict)]} if isinstance(items, list) else payload
        items = payload.get("data") if isinstance(payload, dict) and "data" in payload else (
            payload.get("models") if isinstance(payload, dict) else payload
        )
        if not isinstance(items, list):
            raise ValueError("no list")
        out: dict[str, dict[str, Any]] = {}
        for item in items:
            if isinstance(item, str):
                model_id, context = item, None
            elif isinstance(item, dict):
                model_id = item.get("id") or item.get("name") or item.get("model")
                context = item.get("context_length") or item.get("context_window") or item.get("max_model_len")
            else:
                continue
            if isinstance(model_id, str) and MODEL_ID_RE.match(model_id):
                window = context if isinstance(context, int) and context > 0 else None
                previous = out.get(model_id)
                out[model_id] = {"model_id": model_id, "context_window": window or (previous or {}).get("context_window")}
        return list(out.values())

    async def test_connection(self, provider: Provider, api_key: str | None = None) -> int:
        """Check the endpoint and key; returns the number of models listed."""
        return len(await self.fetch_models(provider, api_key))

    async def refresh_models(self, provider_id: int) -> list[dict[str, Any]]:
        """Re-read the model list from the provider."""
        provider = self.get(provider_id)
        listed = await self.fetch_models(provider)
        self._store.model_upsert_listed(provider_id, listed)
        return self.models(provider_id)

    async def test_model(self, provider_id: int, model_id: str, key_id: int | None = None) -> dict[str, Any]:
        """Send a minimal tool-calling request and record whether the model calls the tool."""
        provider = self.get(provider_id)
        row = self._store.model_get(provider_id, model_id)
        if row is None:
            if not MODEL_ID_RE.match(model_id):
                raise ProviderError(f"invalid model id {model_id!r}")
            self._store.model_save(provider_id, model_id, {}, manual=True)
            row = self._store.model_get(provider_id, model_id)
        assert row is not None
        try:
            key = self._vault.get(self._secret(key_id)) if key_id else self._first_key(provider)
        except VaultUnavailable as exc:
            raise ProviderError(f"the secret store is unavailable ({exc})") from None
        endpoint = self._endpoint(provider, model_id, key, row["thinking_control"])
        tool = {
            "type": "function",
            "function": {
                "name": "ping",
                "description": "Reply to the user by calling this function.",
                "parameters": {"type": "object", "properties": {"word": {"type": "string"}}, "required": ["word"]},
            },
        }
        messages = [{"role": "user", "content": "Call the ping function with the word 'pong'. Do not answer in text."}]
        started = time.monotonic()
        error = ""
        ok = False
        try:
            async for event in stream_chat(endpoint, messages, [tool], temperature=0.0, http_factory=self.http_factory):
                if isinstance(event, Done):
                    calls = event.result.tool_calls
                    ok = any(c["function"]["name"] == "ping" and _json_object(c["function"]["arguments"]) for c in calls)
                    if not ok:
                        error = "the model did not call the test tool"
        except LLMError as exc:
            error = str(exc)
        except asyncio.TimeoutError:
            error = "timed out"
        self._store.model_set_test(provider_id, model_id, ok, error)
        return {"model_id": model_id, "ok": ok, "error": error, "latency_ms": int((time.monotonic() - started) * 1000)}


def _json_object(text: str) -> bool:
    try:
        return isinstance(json.loads(text), dict)
    except json.JSONDecodeError:
        return False
