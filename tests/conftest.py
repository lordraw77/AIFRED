"""Shared fixtures."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from aifred.app import Services, create_app
from aifred.config import Settings
from aifred.store import Store
from tests.fake_llm import FakeLLM
from tests.fake_mcp import FakeMcp
from tests.fake_openbao import FakeOpenBao
from tests.rag_support import KB

ADMIN_USER = "admin"
ADMIN_PASSWORD = "correct horse battery"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Bootstrap settings pointing at a temporary data directory."""
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        admin_user=ADMIN_USER,
        admin_password=SecretStr(ADMIN_PASSWORD),
        data_dir=tmp_path / "data",
    )


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    """A fresh database."""
    s = Store(tmp_path / "test.db")
    yield s
    s.close()


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    """An HTTP client against a fresh application."""
    with TestClient(create_app(settings), base_url="https://testserver") as c:
        yield c


def login(client: TestClient, password: str = ADMIN_PASSWORD) -> str:
    """Log in and return the CSRF token."""
    resp = client.post("/api/auth/login", json={"username": ADMIN_USER, "password": password})
    assert resp.status_code == 200, resp.text
    return str(resp.json()["csrf_token"])


@pytest.fixture
def fake_llm() -> Iterator[Callable[[], FakeLLM]]:
    """Factory for running fake LLM servers; all are stopped after the test."""
    servers: list[FakeLLM] = []

    def make() -> FakeLLM:
        server = FakeLLM().start()
        servers.append(server)
        return server

    yield make
    for server in servers:
        server.stop()


@pytest.fixture
def fake_mcp(tmp_path: Path) -> Iterator[Callable[..., FakeMcp]]:
    """Factory for running fake MCP servers (``tls=True`` for HTTPS); all are stopped after the test."""
    servers: list[FakeMcp] = []

    def make(tls: bool = False, **options: object) -> FakeMcp:
        directory = tmp_path / f"mcp{len(servers)}"
        directory.mkdir()
        server = FakeMcp(tls=directory if tls else None, **options).start()  # type: ignore[arg-type]
        servers.append(server)
        return server

    yield make
    for server in servers:
        server.stop()


@pytest.fixture
def services(settings: Settings) -> Iterator[Services]:
    """Services over a fresh data directory with the admin bootstrapped."""
    svc = Services.open(settings)
    svc.bootstrap()
    yield svc
    svc.close()


@pytest.fixture
def anyio_backend() -> str:
    """Run async tests on asyncio (anyio's pytest plugin ships with Starlette)."""
    return "asyncio"



@pytest.fixture
def llm_ready(services: Services, fake_llm: Callable[[], FakeLLM]) -> FakeLLM:
    """A fake provider with a tool-capable model, set as the ``default`` route."""
    fake = fake_llm()
    provider = services.providers.create({"slug": "fake", "type": "local", "base_url": fake.url})
    services.providers.save_models(provider.id, [{"model_id": "test-model"}])
    services.store.model_set_test(provider.id, "test-model", True)
    services.llm.set_route("default", [{"provider": "fake", "model": "test-model"}])
    return fake


@pytest.fixture
def fake_openbao() -> Iterator[FakeOpenBao]:
    """A running fake OpenBao."""
    server = FakeOpenBao().start()
    yield server
    server.stop()


@pytest.fixture
def kb(services: Services, fake_llm: Callable[[], FakeLLM]) -> KB:
    """A knowledge base switched on, with a fake embedding provider and no sleeping between retries."""
    from aifred.embeddings import EmbeddingClient

    fake = fake_llm()
    services.providers.create({"slug": "emb", "type": "local", "base_url": fake.url})
    services.config.update({"RAG_ENABLED": True, "RAG_EMBED_PROVIDER": "emb", "RAG_EMBED_MODEL": "embed-1"})
    services.rag.embedder = EmbeddingClient(sleep=lambda s: None)
    return KB(services, fake)
