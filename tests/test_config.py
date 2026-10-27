"""Tests for aifred.config."""

from __future__ import annotations

from pathlib import Path

import pytest

from aifred.config import SETTING_SPECS, RuntimeConfig, SettingError, parse_value
from aifred.store import Store
from aifred.vault import SecretBox, Vault, load_or_create_key


@pytest.fixture
def config(tmp_path: Path, store: Store) -> RuntimeConfig:
    return RuntimeConfig(store, Vault(store, SecretBox(load_or_create_key(tmp_path / "k"))))


@pytest.mark.parametrize(
    ("key", "raw", "expected"),
    [
        ("LLM_TEMPERATURE", "0.2", 0.2),
        ("MAX_ITERATIONS", 5, 5),
        ("ENABLE_TERMINAL", "true", True),
        ("ENABLE_TERMINAL", False, False),
        ("LLM_THINKING", "on", "on"),
        ("LLM_EXTRA_BODY", '{"top_p": 0.9}', {"top_p": 0.9}),
        ("LLM_EXTRA_BODY", "", {}),
        ("TOOLSETS", "memory, web,web, mcp:proxmox", ["memory", "web", "mcp:proxmox"]),
        ("TOOLSETS", "", []),
        ("TOOLSETS_CLI", "", None),
        ("TOOLSETS_CLI", None, None),
        ("TELEGRAM_ALLOWED_IDS", "123, 456", [123, 456]),
        ("SEARXNG_URL", "http://searxng:8080/", "http://searxng:8080"),
        ("SEARXNG_URL", "", ""),
        ("TELEGRAM_TOKEN", "", None),
        ("TELEGRAM_TOKEN", " 123:abc ", "123:abc"),
    ],
)
def test_parse_valid(key: str, raw: object, expected: object) -> None:
    assert parse_value(SETTING_SPECS[key], raw) == expected


@pytest.mark.parametrize(
    ("key", "raw"),
    [
        ("LLM_TEMPERATURE", "hot"),
        ("LLM_TEMPERATURE", 5),
        ("MAX_ITERATIONS", 0),
        ("MAX_ITERATIONS", True),
        ("ENABLE_TERMINAL", "maybe"),
        ("LLM_THINKING", "auto"),
        ("LLM_EXTRA_BODY", "[1, 2]"),
        ("LLM_EXTRA_BODY", "{bad json"),
        ("TOOLSETS", "memory,shell"),
        ("TOOLSETS", "mcp:Bad-Name"),
        ("TELEGRAM_ALLOWED_IDS", "abc"),
        ("SEARXNG_URL", "ftp://host"),
        ("SEARXNG_URL", "javascript:alert(1)"),
        ("ROUTE_CLI", ""),
        ("HISTORY_LIMIT", None),
    ],
)
def test_parse_invalid(key: str, raw: object) -> None:
    with pytest.raises(SettingError):
        parse_value(SETTING_SPECS[key], raw)


def test_defaults(config: RuntimeConfig) -> None:
    assert config.get("LLM_TEMPERATURE") == 0.6
    assert config.get("TOOLSETS") == ["memory", "search", "skills", "web", "file"]
    assert config.get("TOOLSETS_CLI") is None
    assert config.get("TELEGRAM_TOKEN") is None
    with pytest.raises(KeyError):
        config.get("NOPE")


def test_update_persists_journals_and_notifies(tmp_path: Path, store: Store, config: RuntimeConfig) -> None:
    seen: list[set[str]] = []
    config.subscribe(seen.append)
    changed = config.update({"LLM_TEMPERATURE": "0.9", "MAX_ITERATIONS": 12})
    assert changed == {"LLM_TEMPERATURE"}  # MAX_ITERATIONS already 12
    assert seen == [{"LLM_TEMPERATURE"}]
    assert store.journal_list(kind="settings")[0]["summary"] == "LLM_TEMPERATURE: 0.6 → 0.9"

    reopened = RuntimeConfig(store, Vault(store, SecretBox(load_or_create_key(tmp_path / "k"))))
    assert reopened.get("LLM_TEMPERATURE") == 0.9


def test_invalid_update_applies_nothing(store: Store, config: RuntimeConfig) -> None:
    with pytest.raises(SettingError) as exc:
        config.update({"LLM_TEMPERATURE": 0.1, "MAX_ITERATIONS": -1, "BOGUS": 1})
    assert "MAX_ITERATIONS" in str(exc.value) and "BOGUS" in str(exc.value)
    assert config.get("LLM_TEMPERATURE") == 0.6
    assert store.journal_list() == []


def test_secret_never_exposed(store: Store, config: RuntimeConfig) -> None:
    config.update({"TELEGRAM_TOKEN": "123456:SECRET-TOKEN"})
    assert config.get("TELEGRAM_TOKEN") == "123456:SECRET-TOKEN"
    item = next(i for i in config.describe() if i["key"] == "TELEGRAM_TOKEN")
    assert item["value"] == "••••" and item["set"] is True
    assert "SECRET" not in str(config.describe())
    assert "SECRET" not in str(store.journal_list())
    assert "SECRET" not in str(store.settings_get_all())
    assert "TELEGRAM_TOKEN" not in config.export()

    assert config.update({"TELEGRAM_TOKEN": "123456:SECRET-TOKEN"}) == set()
    assert config.update({"TELEGRAM_TOKEN": ""}) == {"TELEGRAM_TOKEN"}
    assert config.get("TELEGRAM_TOKEN") is None
    assert [e["summary"] for e in store.journal_list()] == ["TELEGRAM_TOKEN: cleared", "TELEGRAM_TOKEN: updated"]


def test_import_rejects_secrets(config: RuntimeConfig) -> None:
    with pytest.raises(SettingError, match="cannot be imported"):
        config.update({"TELEGRAM_TOKEN": "x"}, allow_secrets=False)


def test_export_contains_only_changes(config: RuntimeConfig) -> None:
    assert config.export() == {}
    config.update({"NOTIFICATIONS": "verbose"})
    assert config.export() == {"NOTIFICATIONS": "verbose"}


def test_corrupt_stored_value_falls_back(tmp_path: Path, store: Store) -> None:
    store.settings_set_many({"MAX_ITERATIONS": '"lots"'})
    config = RuntimeConfig(store, Vault(store, SecretBox(load_or_create_key(tmp_path / "k"))))
    assert config.get("MAX_ITERATIONS") == 12


def test_failing_listener_does_not_break_update(config: RuntimeConfig) -> None:
    def boom(_: set[str]) -> None:
        raise RuntimeError("listener failure")

    config.subscribe(boom)
    assert config.update({"LOG_LEVEL": "DEBUG"}) == {"LOG_LEVEL"}
    assert config.get("LOG_LEVEL") == "DEBUG"


def test_knowledge_base_settings(config: RuntimeConfig) -> None:
    assert config.get("RAG_ENABLED") is False and config.get("RAG_EMBED_PROVIDER") is None and config.get("RAG_EMBED_MODEL") is None
    assert (config.get("RAG_CHUNK_CHARS"), config.get("RAG_CHUNK_OVERLAP"), config.get("RAG_EMBED_BATCH"), config.get("RAG_TOP_K")) == (1200, 150, 16, 5)
    assert (config.get("RAG_MIN_SIMILARITY"), config.get("RAG_MAX_DOCUMENT_BYTES"), config.get("RAG_MAX_CHUNKS")) == (0.30, 5_242_880, 100_000)
    assert (config.get("RAG_CACHE_MB"), config.get("RAG_SCAN_MINUTES")) == (512, 0)  # memory is larger by default and configurable
    config.update({"RAG_ENABLED": "true", "RAG_EMBED_PROVIDER": "ollama", "RAG_EMBED_MODEL": "bge-m3", "RAG_CACHE_MB": 2048, "RAG_MAX_CHUNKS": 500000})
    assert config.get("RAG_CACHE_MB") == 2048 and config.get("RAG_EMBED_MODEL") == "bge-m3"
    config.update({"RAG_EMBED_MODEL": ""})
    assert config.get("RAG_EMBED_MODEL") is None  # empty clears it: keyword-only search
    config.update({"RAG_CACHE_MB": 0})
    assert config.get("RAG_CACHE_MB") == 0  # 0 turns the cache off


@pytest.mark.parametrize(
    ("key", "raw"),
    [("RAG_CHUNK_CHARS", 100), ("RAG_CHUNK_CHARS", 9000), ("RAG_CHUNK_OVERLAP", -1), ("RAG_EMBED_BATCH", 0), ("RAG_TOP_K", 11),
     ("RAG_MIN_SIMILARITY", 1.5), ("RAG_MAX_DOCUMENT_BYTES", 10), ("RAG_MAX_CHUNKS", 5), ("RAG_CACHE_MB", -1), ("RAG_ENABLED", "perhaps")],
)
def test_knowledge_base_settings_are_validated(key: str, raw: object) -> None:
    with pytest.raises(SettingError):
        parse_value(SETTING_SPECS[key], raw)


def test_knowledge_is_a_known_toolset() -> None:
    assert parse_value(SETTING_SPECS["TOOLSETS"], "memory,knowledge") == ["memory", "knowledge"]
    assert parse_value(SETTING_SPECS["TOOLSETS_CLI"], ["knowledge"]) == ["knowledge"]
