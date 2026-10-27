"""Tests for aifred.providers."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from aifred.app import Services
from aifred.providers import PRESETS, Provider, ProviderError
from tests.fake_llm import FakeLLM, Status, Text, ToolCall

pytestmark = pytest.mark.anyio


def add(services: Services, fake: FakeLLM, slug: str = "local1", key: str | None = None, **kw: object) -> Provider:
    return services.providers.create({"slug": slug, "type": "local", "base_url": fake.url, **kw}, key)


def test_presets_cover_the_spec() -> None:
    assert set(PRESETS) == {
        "local", "ollama", "groq", "openrouter", "gemini", "cloudflare", "cerebras", "mistral", "nvidia",
        "huggingface", "custom",
    }
    assert PRESETS["groq"].base_url == "https://api.groq.com/openai/v1"
    assert PRESETS["cloudflare"].config_fields == ("account_id",)


def test_create_defaults(services: Services, fake_llm: Callable[[], FakeLLM]) -> None:
    p = add(services, fake_llm())
    assert (p.connect, p.first_token, p.stall, p.total) == (10.0, 120.0, 60.0, 300.0)  # local engines load on demand
    assert p.verify_tls is True and p.enabled and p.chat_url.endswith("/v1/chat/completions")
    assert services.store.journal_list(kind="settings")[0]["summary"].startswith("provider 'local1'")
    groq = services.providers.create({"slug": "g", "type": "groq"})
    assert (groq.connect, groq.first_token, groq.stall, groq.total) == (10.0, 30.0, 60.0, 300.0)


@pytest.mark.parametrize(
    "data",
    [
        {"slug": "Bad Slug", "type": "local", "base_url": "http://x/v1"},
        {"slug": "a", "type": "nope", "base_url": "http://x/v1"},
        {"slug": "a", "type": "local", "base_url": "ftp://x"},
        {"slug": "a", "type": "local", "base_url": ""},
        {"slug": "a", "type": "local", "base_url": "http://x/v1", "headers": {"Authorization": "x"}},
        {"slug": "a", "type": "local", "base_url": "http://x/v1", "headers": {"X-Bad": "a\nb"}},
        {"slug": "a", "type": "local", "base_url": "http://x/v1", "connect_timeout": 0},
        {"slug": "a", "type": "local", "base_url": "http://x/v1", "total_timeout": "slow"},
        {"slug": "a", "type": "local", "base_url": "http://x/v1", "verify_tls": "/no/such/ca.pem"},
        {"slug": "a", "type": "local", "base_url": "http://x/v1", "extra_body": [1]},
        {"slug": "a", "type": "cloudflare"},
        {"slug": "a", "type": "cloudflare", "config": {"account_id": "bad id!"}},
    ],
)
def test_invalid_provider_rejected(services: Services, data: dict[str, object]) -> None:
    with pytest.raises(ProviderError):
        services.providers.create(data)
    assert services.providers.list() == []


def test_duplicate_slug(services: Services, fake_llm: Callable[[], FakeLLM]) -> None:
    fake = fake_llm()
    add(services, fake)
    with pytest.raises(ProviderError, match="already exists"):
        add(services, fake)


def test_cloudflare_url_uses_account_id(services: Services) -> None:
    p = services.providers.create({"slug": "cf", "type": "cloudflare", "config": {"account_id": "abc123"}})
    assert p.base_url == "https://api.cloudflare.com/client/v4/accounts/abc123/ai/v1"
    p = services.providers.update(p.id, {"config": {"account_id": "zzz999"}})
    assert p.base_url.endswith("/accounts/zzz999/ai/v1")


def test_ollama_urls(services: Services) -> None:
    p = services.providers.create({"slug": "oll", "type": "ollama", "base_url": "http://10.0.0.5:11434"})
    assert p.chat_url == "http://10.0.0.5:11434/v1/chat/completions"
    assert p.models_url == "http://10.0.0.5:11434/api/tags"


def test_tls_options(services: Services, fake_llm: Callable[[], FakeLLM], tmp_path: Path) -> None:
    fake = fake_llm()
    ca = tmp_path / "ca.pem"
    ca.write_text("pem")
    p = add(services, fake, verify_tls=False)
    assert p.verify_tls is False and services.providers.public(p)["tls_unverified"] is True
    p = services.providers.update(p.id, {"verify_tls": str(ca)})
    assert p.verify_tls == str(ca)
    p = services.providers.update(p.id, {"verify_tls": True})
    assert p.verify_tls is True
    assert "TLS verification disabled" not in services.store.journal_list()[0]["summary"]
    services.providers.update(p.id, {"verify_tls": False})
    assert "TLS verification disabled" in services.store.journal_list()[0]["summary"]


def test_keys_are_encrypted_and_never_exposed(services: Services, fake_llm: Callable[[], FakeLLM]) -> None:
    p = services.providers.create({"slug": "k", "type": "local", "base_url": fake_llm().url}, "gsk_SUPERSECRETKEY1234", "main")
    view = services.providers.public(p)
    assert view["keys"] == [{"id": view["keys"][0]["id"], "label": "main", "last4": "1234", "set": True, "status": "ok"}]
    assert "SUPERSECRET" not in str(view)
    assert b"SUPERSECRET" not in Path(services.store.path).read_bytes()
    assert b"SUPERSECRET" not in Path(services.store.path + "-wal").read_bytes()
    assert "SUPERSECRET" not in str(services.store.journal_list())
    assert services.providers.key_value(view["keys"][0]["id"]) == "gsk_SUPERSECRETKEY1234"


def test_key_lifecycle(services: Services, fake_llm: Callable[[], FakeLLM]) -> None:
    p = add(services, fake_llm(), key="first-key-value")
    [k1] = services.store.key_list(p.id)
    k2 = services.providers.add_key(p.id, "second-key-value", "backup")
    assert [k["id"] for k in services.store.key_list(p.id)] == [k1["id"], k2]
    with pytest.raises(ProviderError):
        services.providers.add_key(p.id, "has space")
    services.store.health_set("key", str(k2), "disabled", None, 1, "invalid key", "401")
    assert services.providers.public(p)["keys"][1]["status"] == "invalid"
    services.providers.replace_key(k2, "third-key-value")  # editing clears the invalid state
    assert services.providers.public(p)["keys"][1]["status"] == "ok"
    assert services.providers.key_value(k2) == "third-key-value"
    other = add(services, fake_llm(), slug="other")
    with pytest.raises(ProviderError):
        services.providers.delete_key(other.id, k2)
    services.providers.delete_key(p.id, k2)
    assert services.providers.key_value(k2) is None


def test_delete_provider_removes_secrets_and_routes(services: Services, fake_llm: Callable[[], FakeLLM]) -> None:
    p = add(services, fake_llm(), key="to-be-removed-key")
    [k] = services.store.key_list(p.id)
    services.store.model_save(p.id, "m", {})
    services.store.model_set_test(p.id, "m", True)
    services.llm.set_route("default", [{"provider": p.slug, "model": "m"}])
    services.providers.delete(p.id)
    assert services.providers.list() == []
    assert services.providers.key_value(k["id"]) is None
    assert services.store.route_entries("default") == []
    assert services.store.model_list(p.id) == []


async def test_refresh_models_openai_and_ollama(services: Services, fake_llm: Callable[[], FakeLLM]) -> None:
    fake = fake_llm()
    fake.models = [{"id": "qwen3", "max_model_len": 32768}, {"id": "other", "context_length": 8000}, {"id": "qwen3"}]
    p = add(services, fake)
    models = await services.providers.refresh_models(p.id)
    assert {m["model_id"]: m["context_window"] for m in models} == {"qwen3": 32768, "other": 8000}

    fake.models = [{"id": "qwen3"}, {"id": "new"}]
    services.providers.save_models(p.id, [{"model_id": "handmade"}])
    models = {m["model_id"]: m for m in await services.providers.refresh_models(p.id)}
    assert models["other"]["available"] is False  # no longer listed
    assert models["handmade"]["available"] is True and models["handmade"]["manual"] is True
    assert models["qwen3"]["context_window"] == 32768  # kept

    oll = services.providers.create({"slug": "oll", "type": "ollama", "base_url": fake.root})
    listed = await services.providers.refresh_models(oll.id)
    assert {m["model_id"] for m in listed} == {"qwen3", "new"}


async def test_fetch_models_errors_are_readable(services: Services, fake_llm: Callable[[], FakeLLM]) -> None:
    fake = fake_llm()
    fake.api_key = "right"
    probe = services.providers.preview({"slug": "x", "type": "local", "base_url": fake.url})
    with pytest.raises(ProviderError, match="rejected the API key"):
        await services.providers.test_connection(probe, "wrong")
    assert await services.providers.test_connection(probe, "right") == 1
    fake.models_status = 500
    with pytest.raises(ProviderError, match="HTTP 500"):
        await services.providers.test_connection(probe, "right")
    dead = services.providers.preview({"slug": "d", "type": "local", "base_url": "http://127.0.0.1:9/v1"})
    with pytest.raises(ProviderError, match="cannot reach"):
        await services.providers.test_connection(dead)


async def test_model_tool_test_pass_and_fail(services: Services, fake_llm: Callable[[], FakeLLM]) -> None:
    fake = fake_llm()
    p = add(services, fake)
    fake.queue(ToolCall("ping", {"word": "pong"}))
    ok = await services.providers.test_model(p.id, "test-model")
    assert ok["ok"] is True
    assert fake.requests[0].body["tools"][0]["function"]["name"] == "ping"
    model = services.providers.models(p.id)[0]
    assert model["test_ok"] is True and model["tools"] is True and model["manual"] is True

    fake.queue(Text("I will not call tools"))
    bad = await services.providers.test_model(p.id, "test-model")
    assert bad["ok"] is False and "did not call" in bad["error"]
    fake.queue(Status(401))
    bad = await services.providers.test_model(p.id, "test-model")
    assert bad["ok"] is False and "401" in bad["error"]
    assert services.providers.models(p.id)[0]["test_ok"] is False


def test_model_overrides_validated(services: Services, fake_llm: Callable[[], FakeLLM]) -> None:
    p = add(services, fake_llm())
    [m] = services.providers.save_models(
        p.id, [{"model_id": "m1", "context_window": "32768", "thinking_control": "qwen_template", "price_in": 0.5, "vision": True}]
    )
    assert m["context_window"] == 32768 and m["thinking_control"] == "qwen_template" and m["vision"] is True
    for bad in ({"model_id": "m1", "thinking_control": "magic"}, {"model_id": "m1", "context_window": -1}, {"model_id": "bad id"}, {"model_id": "m1", "price_in": -2}):
        with pytest.raises(ProviderError):
            services.providers.save_models(p.id, [bad])


def test_cloudflare_catalogue_uses_search_endpoint_and_name_field(services: Services) -> None:
    p = services.providers.create({"slug": "cf", "type": "cloudflare", "config": {"account_id": "abc123"}})
    assert p.models_url == (
        "https://api.cloudflare.com/client/v4/accounts/abc123/ai/models/search?task=Text%20Generation&per_page=100"
    )
    payload = {"success": True, "result": [
        {"id": "f9f2250b-1048-4a52-9910-d0bf976616a1", "name": "@cf/openai/gpt-oss-120b"},
        {"id": "0000-uuid", "name": "@cf/meta/llama-3.1-8b-instruct"},
    ]}
    assert [m["model_id"] for m in services.providers._parse_models(payload, "cloudflare")] == [
        "@cf/openai/gpt-oss-120b", "@cf/meta/llama-3.1-8b-instruct",
    ]


def test_embeddings_url_and_usable_key(services: Services, fake_llm: Callable[[], FakeLLM]) -> None:
    oll = services.providers.create({"slug": "oll", "type": "ollama", "base_url": "http://10.0.0.5:11434"})
    assert oll.embeddings_url == "http://10.0.0.5:11434/v1/embeddings"
    p = add(services, fake_llm(), key="first-key-value")
    second = services.providers.add_key(p.id, "second-key-value")
    [k1, _] = services.store.key_list(p.id)
    assert services.providers.usable_key(p) == "first-key-value"
    services.store.health_set("key", str(k1["id"]), "disabled", None, 1, "invalid key", "401")
    assert services.providers.usable_key(p) == "second-key-value"  # a key marked invalid is skipped
    services.store.health_set("key", str(second), "disabled", None, 1, "invalid key", "401")
    assert services.providers.usable_key(p) is None
    assert services.providers.usable_key(oll) is None  # keyless provider
    assert p.embeddings_url.endswith("/v1/embeddings")


def test_embedding_target_timeout_follows_the_provider(services: Services, fake_llm: Callable[[], FakeLLM]) -> None:
    from aifred.providers import embed_target

    local = add(services, fake_llm())  # a local engine: first_token 120 s, so a cold model load may take up to 240 s
    hosted = services.providers.create({"slug": "g", "type": "groq"})  # hosted: first_token 30 s
    assert embed_target(local, "m", None).total == 240.0 and embed_target(hosted, "m", "k").total == 60.0
    short = services.providers.update(local.id, {"total_timeout": 100})
    assert embed_target(short, "m", None).total == 100.0  # never longer than the provider's own total
    assert embed_target(hosted, "m", "k").url.endswith("/openai/v1/embeddings") and embed_target(hosted, "m", "k").api_key == "k"
