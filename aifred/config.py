"""Configuration: bootstrap environment and database-backed runtime settings.

Two layers (spec section 4):

* :class:`Settings` reads the bootstrap environment (prefix ``AIFRED_``): the admin
  account plus the image-level data directory and listen host.
* :class:`RuntimeConfig` holds every other setting. Values live in the ``settings``
  table (secrets in the encrypted ``secrets`` table), are validated against
  :data:`SETTING_SPECS`, and are applied live through change listeners.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from aifred.store import Store
from aifred.vault import Vault, VaultUnavailable

logger = logging.getLogger(__name__)

API_PORT = 8700
"""Listen port inside the container (image constant, see docs/analysis.md A2)."""


class Settings(BaseSettings):
    """Bootstrap environment.

    Attributes:
        admin_user: Username of the admin account.
        admin_password: Initial admin password, used only when no admin exists.
        data_dir: Directory holding all state.
        api_host: Listen address of the HTTP server.
        secrets_backend: ``file`` (master key in the data directory) or ``openbao`` (Transit).
        openbao_addr: OpenBao URL; when set, OpenBao can also decrypt during a migration.
        openbao_token: Static OpenBao token (or ``openbao_token_file``).
        openbao_role_id: AppRole role id (use with a secret id).
        openbao_secret_id: AppRole secret id (or ``openbao_secret_id_file``).
        openbao_mount: Transit mount path.
        openbao_key: Transit key name.
        openbao_cacert: PEM CA bundle for an HTTPS OpenBao.
        openbao_verify_tls: Set false to skip certificate verification (logged as a warning).
    """

    model_config = SettingsConfigDict(env_prefix="AIFRED_", env_file=".env", extra="ignore")

    admin_user: str | None = None
    admin_password: SecretStr | None = None
    data_dir: Path = Path("/data")
    api_host: str = "127.0.0.1"
    secrets_backend: Literal["file", "openbao"] = "file"
    openbao_addr: str | None = None
    openbao_token: SecretStr | None = None
    openbao_token_file: Path | None = None
    openbao_role_id: str | None = None
    openbao_secret_id: SecretStr | None = None
    openbao_secret_id_file: Path | None = None
    openbao_mount: str = "transit"
    openbao_key: str = "aifred"
    openbao_cacert: Path | None = None
    openbao_verify_tls: bool = True


Kind = Literal["str", "int", "float", "bool", "enum", "json", "list", "int_list", "url", "secret"]

_TOOLSET_RE = re.compile(r"^(memory|search|skills|web|file|knowledge|terminal|cron|agents|mcp|mcp:[a-z0-9_]{1,24})$")
_URL_RE = re.compile(r"^https?://[^\s/]+(/\S*)?$")


@dataclass(frozen=True)
class SettingSpec:
    """Definition of one runtime setting.

    Attributes:
        key: Uppercase setting name.
        kind: Value type used for parsing and validation.
        default: Default value (already parsed).
        group: Dashboard group.
        description: One-line help text.
        choices: Allowed values for ``enum`` settings.
        minimum: Lower bound for numeric settings.
        maximum: Upper bound for numeric settings.
        nullable: Whether ``None`` is a valid value (meaning "inherit").
        item_pattern: Regex every item of a ``list`` setting must match.
    """

    key: str
    kind: Kind
    default: Any
    group: str
    description: str
    choices: tuple[str, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    nullable: bool = False
    item_pattern: re.Pattern[str] | None = field(default=None, compare=False)

    @property
    def secret(self) -> bool:
        """Whether the value is stored encrypted and never returned."""
        return self.kind == "secret"


def _toolsets(key: str, default: list[str] | None, description: str) -> SettingSpec:
    return SettingSpec(
        key, "list", default, "tools", description, nullable=default is None, item_pattern=_TOOLSET_RE
    )


SETTING_SPECS: dict[str, SettingSpec] = {
    s.key: s
    for s in [
        SettingSpec("LLM_THINKING", "enum", "off", "llm", "Reasoning mode for models that support it", ("off", "on")),
        SettingSpec("LLM_TEMPERATURE", "float", 0.6, "llm", "Sampling temperature", minimum=0.0, maximum=2.0),
        SettingSpec("LLM_EXTRA_BODY", "json", {}, "llm", "JSON object merged into every request body"),
        SettingSpec("ROUTE_CLI", "str", "default", "routing", "Model route used by the CLI"),
        SettingSpec("ROUTE_TELEGRAM", "str", "default", "routing", "Model route used by Telegram"),
        SettingSpec("ROUTE_API", "str", "default", "routing", "Model route used by the API"),
        SettingSpec("ROUTE_WEB", "str", "default", "routing", "Model route used by the web chat"),
        SettingSpec("ROUTE_CRON", "str", "default", "routing", "Model route used by scheduled tasks"),
        SettingSpec("ROUTE_DEADLINE", "int", 120, "routing", "Time budget (s) for walking a route", minimum=5, maximum=3600),
        SettingSpec("COOLDOWN_QUOTA", "int", 3600, "routing", "Default cooldown (s) after quota exhaustion", minimum=0, maximum=86400 * 31),
        SettingSpec("MAX_ITERATIONS", "int", 12, "agent", "Maximum tool rounds per request", minimum=1, maximum=100),
        SettingSpec("HISTORY_LIMIT", "int", 40, "agent", "History messages sent to the model", minimum=2, maximum=1000),
        SettingSpec("IDLE_MINUTES", "int", 30, "agent", "Idle time before the system prompt is rebuilt", minimum=1, maximum=10080),
        SettingSpec("TOOL_OUTPUT_LIMIT", "int", 8000, "agent", "Maximum characters of tool output", minimum=500, maximum=200000),
        SettingSpec("LOG_LEVEL", "enum", "INFO", "system", "Log level", ("DEBUG", "INFO", "WARNING", "ERROR")),
        SettingSpec("TELEGRAM_TOKEN", "secret", None, "telegram", "Bot token; enables the Telegram gateway"),
        SettingSpec("TELEGRAM_ALLOWED_IDS", "int_list", [], "telegram", "Allowlist of numeric user ids (empty: open bot)"),
        SettingSpec("TELEGRAM_API_BASE", "url", "https://api.telegram.org", "telegram", "Telegram Bot API base URL"),
        _toolsets("TOOLSETS", ["memory", "search", "skills", "web", "file"], "Enabled toolsets"),
        _toolsets("TOOLSETS_CLI", None, "Toolsets for the CLI (empty: inherit)"),
        _toolsets("TOOLSETS_TELEGRAM", None, "Toolsets for Telegram (empty: inherit)"),
        _toolsets("TOOLSETS_API", None, "Toolsets for the API (empty: inherit)"),
        _toolsets("TOOLSETS_WEB", None, "Toolsets for the web chat (empty: inherit)"),
        _toolsets("TOOLSETS_CRON", ["search", "web", "skills"], "Toolsets for scheduled tasks, which run unattended (add more only for tasks you trust)"),
        SettingSpec("ENABLE_TERMINAL", "bool", False, "tools", "Shortcut that adds terminal to TOOLSETS"),
        SettingSpec("TERMINAL_TIMEOUT", "int", 60, "tools", "Terminal command timeout (s)", minimum=1, maximum=3600),
        SettingSpec("WEB_ALLOW_PRIVATE", "bool", False, "tools", "Let web_fetch reach private addresses"),
        SettingSpec("SEARXNG_URL", "url", "", "tools", "SearXNG base URL; enables web_search"),
        SettingSpec("REVIEW_ENABLED", "bool", True, "review", "Background self-improvement review"),
        SettingSpec("REVIEW_EVERY_TURNS", "int", 5, "review", "Minimum turns between reviews", minimum=1, maximum=1000),
        SettingSpec("REVIEW_MAX_INPUT_TOKENS", "int", 6000, "review", "Cost cap for one review", minimum=500, maximum=1000000),
        SettingSpec("WRITE_APPROVAL", "enum", "off", "review", "Queue saves for approval", ("off", "memory", "skills", "all")),
        SettingSpec("NOTIFICATIONS", "enum", "on", "review", "Show saves and fallbacks in chat", ("off", "on", "verbose")),
        SettingSpec("SCHEDULER_ENABLED", "bool", True, "scheduler", "Run scheduled tasks"),
        SettingSpec("SCHEDULER_TIMEZONE", "str", "", "scheduler", "Time zone of cron expressions, for example Europe/Rome (empty: the server's)"),
        SettingSpec("SCHEDULER_MIN_INTERVAL", "int", 5, "scheduler", "Shortest gap (minutes) between runs of one task", minimum=1, maximum=1440),
        SettingSpec("SCHEDULER_MAX_TASKS", "int", 20, "scheduler", "Most scheduled tasks that can exist", minimum=1, maximum=500),
        SettingSpec("SCHEDULER_RUN_TIMEOUT", "int", 600, "scheduler", "Seconds one scheduled run may take", minimum=10, maximum=7200),
        SettingSpec("SCHEDULER_MAX_PARALLEL", "int", 2, "scheduler", "Scheduled runs that can go at the same time", minimum=1, maximum=8),
        SettingSpec("AGENT_MAX_ITERATIONS", "int", 8, "agents", "Tool rounds a sub-agent may take (unless its own limit is set)", minimum=1, maximum=100),
        SettingSpec("AGENT_TIMEOUT", "int", 300, "agents", "Seconds a sub-agent run may take (unless its own limit is set)", minimum=10, maximum=7200),
        SettingSpec("AGENT_MAX_PARALLEL", "int", 3, "agents", "Sub-agents that delegate_many runs at the same time", minimum=1, maximum=10),
        SettingSpec("AGENT_MAX_AGENTS", "int", 20, "agents", "Most sub-agents that can be defined", minimum=1, maximum=200),
        SettingSpec("MCP_CONNECT_TIMEOUT", "int", 15, "mcp", "Default MCP connect timeout (s)", minimum=1, maximum=600),
        SettingSpec("MCP_CALL_TIMEOUT", "int", 120, "mcp", "Default MCP call timeout (s)", minimum=1, maximum=3600),
        SettingSpec("MCP_MAX_TOOLS_PER_SERVER", "int", 40, "mcp", "Cap on tools exposed per MCP server", minimum=1, maximum=500),
        SettingSpec(
            "INSTRUCTIONS_BUDGET", "int", 3000, "agent", "Maximum characters of the Capabilities prompt section",
            minimum=100, maximum=50000,
        ),
        SettingSpec("RAG_ENABLED", "bool", False, "knowledge", "Turn the local knowledge base on (ingestion, API, tools)"),
        SettingSpec("RAG_EMBED_PROVIDER", "str", None, "knowledge", "Provider slug of the embedding model (empty: keyword search only)", nullable=True),
        SettingSpec("RAG_EMBED_MODEL", "str", None, "knowledge", "Embedding model; overridable per collection", nullable=True),
        SettingSpec("RAG_CHUNK_CHARS", "int", 1200, "knowledge", "Target chunk size in characters", minimum=300, maximum=4000),
        SettingSpec("RAG_CHUNK_OVERLAP", "int", 150, "knowledge", "Characters repeated between consecutive chunks", minimum=0, maximum=1000),
        SettingSpec("RAG_EMBED_BATCH", "int", 16, "knowledge", "Chunks per embedding request", minimum=1, maximum=128),
        SettingSpec("RAG_TOP_K", "int", 5, "knowledge", "Results returned by default", minimum=1, maximum=10),
        SettingSpec("RAG_MIN_SIMILARITY", "float", 0.30, "knowledge", "Dense candidates below this cosine similarity are dropped", minimum=0.0, maximum=1.0),
        SettingSpec("RAG_MAX_DOCUMENT_BYTES", "int", 5_242_880, "knowledge", "Largest document accepted", minimum=1024, maximum=104_857_600),
        SettingSpec("RAG_MAX_CHUNKS", "int", 100_000, "knowledge", "Cap on chunks in the whole knowledge base", minimum=100, maximum=10_000_000),
        SettingSpec("RAG_CACHE_MB", "int", 512, "knowledge", "Memory for cached vector matrices (0: no cache, scan the database)", minimum=0, maximum=65_536),
        SettingSpec("RAG_SCAN_MINUTES", "int", 0, "knowledge", "Rescan the knowledge folder every N minutes (0: only on request)", minimum=0, maximum=10_080),
        SettingSpec("RAG_FORMATS", "list", ["text", "pdf", "office", "image"], "knowledge", "Formats the knowledge base accepts: text, pdf, office, image", item_pattern=re.compile(r"^(text|pdf|office|image)$")),
        SettingSpec("RAG_VISION_ROUTE", "str", "vision", "knowledge", "Route whose model reads images and scanned pages (no default provider: you choose who receives the images)"),
        SettingSpec("RAG_VISION_DAILY_LIMIT", "int", 200, "knowledge", "Images and scanned pages sent to the vision model per day (0: vision off)", minimum=0, maximum=1_000_000),
        SettingSpec("RAG_MAX_IMAGE_BYTES", "int", 10_485_760, "knowledge", "Largest image accepted", minimum=1024, maximum=104_857_600),
        SettingSpec("RAG_MAX_PAGES", "int", 500, "knowledge", "Pages read from one PDF or presentation", minimum=1, maximum=100_000),
        SettingSpec("RAG_OCR_MIN_CHARS", "int", 40, "knowledge", "A PDF page with fewer characters is treated as a scan and read by the vision model", minimum=0, maximum=10_000),
        SettingSpec("RAG_RENDER_MAX_SIDE", "int", 1600, "knowledge", "Longest side in pixels of a scanned PDF page sent to the vision model (lower it for slow local models)", minimum=300, maximum=4000),
        SettingSpec("RAG_PARSE_TIMEOUT", "int", 60, "knowledge", "Seconds the sandboxed parser may run for one file", minimum=5, maximum=3600),
        SettingSpec("RAG_PARSE_MEMORY_MB", "int", 768, "knowledge", "Address-space limit of the sandboxed parser (MB; Python with pypdf and the cryptography library already maps about 300)", minimum=64, maximum=16_384),
    ]
}


class SettingError(ValueError):
    """A setting value failed validation."""


def parse_value(spec: SettingSpec, raw: Any) -> Any:
    """Parse and validate a raw value for a setting.

    Args:
        spec: Setting definition.
        raw: Value from JSON input; strings are accepted for every kind.

    Returns:
        The normalised value.

    Raises:
        SettingError: If the value is invalid.
    """
    if raw is None or (isinstance(raw, str) and raw.strip() == "" and spec.nullable):
        if spec.nullable or spec.secret:
            return None
        raise SettingError("a value is required")

    match spec.kind:
        case "str" | "secret":
            if not isinstance(raw, str):
                raise SettingError("expected a string")
            value: Any = raw.strip()
            if spec.kind == "str" and not value:
                raise SettingError("a value is required")
            if len(value) > 4096:
                raise SettingError("value too long")
            return value or None if spec.secret else value
        case "url":
            if not isinstance(raw, str):
                raise SettingError("expected a URL")
            value = raw.strip().rstrip("/")
            if value and not _URL_RE.match(value):
                raise SettingError("expected an http:// or https:// URL")
            return value
        case "enum":
            value = str(raw).strip()
            if value not in spec.choices:
                raise SettingError(f"expected one of: {', '.join(spec.choices)}")
            return value
        case "bool":
            if isinstance(raw, bool):
                return raw
            if isinstance(raw, str) and raw.strip().lower() in ("true", "false", "1", "0", "on", "off"):
                return raw.strip().lower() in ("true", "1", "on")
            raise SettingError("expected true or false")
        case "int" | "float":
            if isinstance(raw, bool):
                raise SettingError("expected a number")
            try:
                number: float = int(raw) if spec.kind == "int" else float(raw)
            except (TypeError, ValueError):
                raise SettingError("expected a number") from None
            if spec.minimum is not None and number < spec.minimum:
                raise SettingError(f"must be at least {spec.minimum:g}")
            if spec.maximum is not None and number > spec.maximum:
                raise SettingError(f"must be at most {spec.maximum:g}")
            return number
        case "json":
            if isinstance(raw, str):
                if not raw.strip():
                    return {}
                try:
                    raw = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise SettingError(f"invalid JSON: {exc.msg}") from None
            if not isinstance(raw, dict):
                raise SettingError("expected a JSON object")
            return raw
        case "list" | "int_list":
            items = _split_list(raw)
            if spec.kind == "int_list":
                try:
                    return [int(i) for i in items]
                except ValueError:
                    raise SettingError("expected numeric ids") from None
            for item in items:
                if spec.item_pattern and not spec.item_pattern.match(item):
                    raise SettingError(f"invalid item: {item!r}")
            return items
    raise SettingError(f"unsupported kind {spec.kind}")


def _split_list(raw: Any) -> list[str]:
    if isinstance(raw, str):
        parts: Iterable[Any] = raw.split(",")
    elif isinstance(raw, list):
        parts = raw
    else:
        raise SettingError("expected a list or a comma-separated string")
    items = [str(p).strip() for p in parts]
    return list(dict.fromkeys(i for i in items if i))


def _secret_name(key: str) -> str:
    return f"setting:{key}"


Listener = Callable[[set[str]], None]


class RuntimeConfig:
    """Database-backed settings with defaults, validation and change listeners.

    Args:
        store: Database store.
        vault: Encrypted secret storage.
    """

    def __init__(self, store: Store, vault: Vault) -> None:
        self._store = store
        self._vault = vault
        self._lock = threading.Lock()
        self._listeners: list[Listener] = []
        self._values: dict[str, Any] = {}
        self.reload()

    def reload(self) -> None:
        """Re-read stored values; invalid stored values fall back to defaults."""
        stored = self._store.settings_get_all()
        values: dict[str, Any] = {}
        for key, spec in SETTING_SPECS.items():
            if spec.secret or key not in stored:
                values[key] = spec.default
                continue
            try:
                values[key] = parse_value(spec, stored[key])
            except SettingError as exc:
                logger.warning("Stored setting %s is invalid (%s); using the default", key, exc)
                values[key] = spec.default
        with self._lock:
            self._values = values

    def get(self, key: str) -> Any:
        """Return a setting value (secrets are decrypted).

        Raises:
            KeyError: If the key is unknown.
        """
        spec = SETTING_SPECS[key]
        if spec.secret:
            return self._vault.get(_secret_name(key))
        with self._lock:
            return self._values[key]

    def is_set(self, key: str) -> bool:
        """Whether a secret setting has a value."""
        return self._vault.exists(_secret_name(key))

    def subscribe(self, listener: Listener) -> None:
        """Register a callback invoked with the set of changed keys."""
        self._listeners.append(listener)

    def describe(self) -> list[dict[str, Any]]:
        """Return every setting for the dashboard, with secrets masked."""
        out = []
        for key, spec in SETTING_SPECS.items():
            item: dict[str, Any] = {
                "key": key,
                "group": spec.group,
                "kind": spec.kind,
                "description": spec.description,
                "default": None if spec.secret else spec.default,
                "secret": spec.secret,
            }
            if spec.choices:
                item["choices"] = list(spec.choices)
            if spec.secret:
                is_set = self.is_set(key)
                item["value"] = "••••" if is_set else ""
                item["set"] = is_set
            else:
                item["value"] = self.get(key)
            out.append(item)
        return out

    def export(self) -> dict[str, Any]:
        """Return the non-secret settings that differ from the defaults."""
        with self._lock:
            return {
                k: v
                for k, v in self._values.items()
                if not SETTING_SPECS[k].secret and v != SETTING_SPECS[k].default
            }

    def validate(self, changes: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
        """Validate a set of changes without applying them.

        Returns:
            The parsed values and a mapping of key to error message.
        """
        parsed: dict[str, Any] = {}
        errors: dict[str, str] = {}
        for key, raw in changes.items():
            spec = SETTING_SPECS.get(key)
            if spec is None:
                errors[key] = "unknown setting"
                continue
            try:
                parsed[key] = parse_value(spec, raw)
            except SettingError as exc:
                errors[key] = str(exc)
        return parsed, errors

    def update(self, changes: dict[str, Any], *, allow_secrets: bool = True) -> set[str]:
        """Validate and apply changes atomically, journal them and notify listeners.

        Args:
            changes: Mapping of setting key to raw value. For secrets, an empty
                value or ``None`` clears the secret.
            allow_secrets: When false, secret keys are rejected (used by import).

        Returns:
            The keys whose value actually changed.

        Raises:
            SettingError: If any value is invalid; the message lists every error.
        """
        parsed, errors = self.validate(changes)
        if not allow_secrets:
            for key in parsed:
                if SETTING_SPECS[key].secret:
                    errors[key] = "secrets cannot be imported"
        if errors:
            raise SettingError("; ".join(f"{k}: {v}" for k, v in sorted(errors.items())))

        changed: set[str] = set()
        plain: dict[str, Any] = {}
        summaries: list[str] = []
        for key, value in parsed.items():
            spec = SETTING_SPECS[key]
            if spec.secret:
                try:
                    if value is None:
                        if self._vault.delete(_secret_name(key)):
                            changed.add(key)
                            summaries.append(f"{key}: cleared")
                    elif value != self._vault.get(_secret_name(key)):
                        self._vault.set(_secret_name(key), value)
                        changed.add(key)
                        summaries.append(f"{key}: updated")
                except VaultUnavailable as exc:
                    raise SettingError(f"{key}: the secret store is unavailable ({exc})") from None
                continue
            if value != self.get(key):
                plain[key] = value
                changed.add(key)
                summaries.append(f"{key}: {_short(self.get(key))} → {_short(value)}")

        if plain:
            self._store.settings_set_many({k: json.dumps(v) for k, v in plain.items()})
            with self._lock:
                self._values.update(plain)
        for summary in summaries:
            self._store.journal_add("settings", "update", summary)
        if changed:
            logger.info("Settings changed: %s", ", ".join(sorted(changed)))
            for listener in self._listeners:
                try:
                    listener(changed)
                except Exception:
                    logger.exception("Settings listener failed")
        return changed


def _short(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False)
    return text if len(text) <= 80 else text[:77] + "..."
