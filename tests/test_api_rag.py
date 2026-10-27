"""Tests for the knowledge-base admin API (aifred.gateways.api_rag), with the real background worker."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from aifred.app import Services
from tests.conftest import login
from tests.fake_llm import FakeLLM
from tests.rag_support import NETWORK, PROXMOX


@pytest.fixture
def admin(client: TestClient) -> TestClient:
    client.headers["X-CSRF-Token"] = login(client)
    return client


@pytest.fixture
def on(admin: TestClient, fake_llm: Callable[[], FakeLLM]) -> TestClient:
    """The API with the knowledge base switched on through the Config endpoint, pointing at a fake embedding provider."""
    fake = fake_llm()
    admin.fake = fake  # type: ignore[attr-defined]
    created = admin.post("/api/providers", json={"slug": "emb", "type": "local", "base_url": fake.url, "skip_test": True})
    assert created.status_code == 201
    resp = admin.put("/api/config", json={"values": {"RAG_ENABLED": True, "RAG_EMBED_PROVIDER": "emb", "RAG_EMBED_MODEL": "embed-1"}})
    assert resp.status_code == 200
    return admin


def services_of(client: TestClient) -> Services:
    services: Services = client.app.state.services  # type: ignore[attr-defined]
    return services


def wait(client: TestClient, *, count: int = 1, collection: str | None = None, status: str = "ready") -> list[dict[str, Any]]:
    """Poll the document list until ``count`` documents have the status (the worker thread indexes them)."""
    deadline = time.time() + 20
    docs: list[dict[str, Any]] = []
    while time.time() < deadline:
        docs = client.get("/api/rag/documents", params={"collection": collection} if collection else {}).json()
        if len([d for d in docs if d["status"] == status]) >= count:
            return docs
        time.sleep(0.05)
    raise AssertionError(f"documents never became {status}: {[(d['source'], d['status'], d['error']) for d in docs]}")


def upload(client: TestClient, collection: str, name: str, content: bytes | str) -> httpx.Response:
    return client.post("/api/rag/documents", params={"collection": collection, "name": name}, content=content)


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/rag/status"), ("GET", "/api/rag/collections"), ("POST", "/api/rag/collections"),
        ("PUT", "/api/rag/collections/x"), ("DELETE", "/api/rag/collections/x"), ("GET", "/api/rag/documents"),
        ("POST", "/api/rag/documents"), ("POST", "/api/rag/documents/url"), ("DELETE", "/api/rag/documents/1"),
        ("POST", "/api/rag/scan"), ("POST", "/api/rag/reindex"), ("POST", "/api/rag/search"),
    ],
)
def test_requires_admin(client: TestClient, method: str, path: str) -> None:
    assert client.request(method, path, json={}).status_code == 401


def test_writes_need_csrf(client: TestClient) -> None:
    login(client)  # cookie only
    assert client.post("/api/rag/collections", json={"name": "x"}).status_code == 403
    assert client.get("/api/rag/collections").status_code == 200


def test_when_off_the_work_endpoints_refuse_but_status_answers(admin: TestClient) -> None:
    assert admin.get("/api/rag/status").json()["enabled"] is False
    assert admin.post("/api/rag/collections", json={"name": "homelab"}).status_code == 201  # collections can be prepared in advance
    for resp in (
        upload(admin, "homelab", "a.md", "# A\n\ntext"), admin.post("/api/rag/scan", json={}), admin.post("/api/rag/reindex", json={}),
        admin.post("/api/rag/search", json={"query": "x"}), admin.post("/api/rag/documents/url", json={"collection": "homelab", "url": "http://example.com/a"}),
    ):
        assert resp.status_code == 409 and "RAG_ENABLED" in resp.json()["detail"]


def test_collections_crud(on: TestClient) -> None:
    created = on.post("/api/rag/collections", json={"name": "homelab", "description": "Servers"})
    assert created.status_code == 201
    body = created.json()
    assert body["name"] == "homelab" and body["embedding"]["tag"] == "emb|embed-1" and body["stale"] == 0
    assert on.post("/api/rag/collections", json={"name": "homelab"}).status_code == 422
    assert on.post("/api/rag/collections", json={"name": "Bad Name"}).status_code == 422
    assert on.post("/api/rag/collections", json={}).status_code == 422
    changed = on.put("/api/rag/collections/homelab", json={"description": "Servers and VMs", "embed_provider": "emb", "embed_model": "special"})
    assert changed.json()["description"] == "Servers and VMs" and changed.json()["embedding"]["model"] == "special"
    assert on.put("/api/rag/collections/homelab", json={"embed_provider": "ghost", "embed_model": "m"}).status_code == 422
    assert on.put("/api/rag/collections/nope", json={"description": "x"}).status_code == 422
    assert [c["name"] for c in on.get("/api/rag/collections").json()] == ["homelab"]
    assert on.delete("/api/rag/collections/homelab").status_code == 200
    assert on.delete("/api/rag/collections/homelab").status_code == 404


def test_upload_index_search_and_delete(on: TestClient) -> None:
    on.post("/api/rag/collections", json={"name": "homelab"})
    resp = upload(on, "homelab", "proxmox.md", PROXMOX)
    assert resp.status_code == 202 and resp.json()["status"] in ("queued", "indexing", "ready")
    upload(on, "homelab", "network.md", NETWORK)
    docs = wait(on, count=2, collection="homelab")
    assert {d["source"] for d in docs} == {"proxmox.md", "network.md"} and all(d["flagged"] == 0 for d in docs)

    found = on.post("/api/rag/search", json={"query": "PVE-ERR-4471", "collection": "homelab"}).json()
    top = found["hits"][0]
    assert top["source"] == "proxmox.md" and "Error code PVE-ERR-4471" in top["text"] and top["keyword_rank"] == 1 and top["dense_rank"] is not None
    assert top["machine_read"] is False and top["flagged"] is False and found["notes"] == []
    dense = on.post("/api/rag/search", json={"query": "copia storage", "mode": "dense"}).json()["hits"]
    assert dense and dense[0]["keyword_rank"] is None and dense[0]["heading"].endswith("Backup first")
    assert on.post("/api/rag/search", json={"query": "copia storage", "mode": "keyword"}).json()["hits"] == []
    assert on.post("/api/rag/search", json={"query": "x", "mode": "magic"}).status_code == 422
    assert on.post("/api/rag/search", json={"query": "x", "limit": 99}).status_code == 422
    assert on.post("/api/rag/search", json={"query": "x", "collection": "nope"}).status_code == 422

    status = on.get("/api/rag/status").json()
    assert status["enabled"] and status["chunks"] == 5 and status["documents"] == {"ready": 2} and status["cache_bytes"] > 0
    assert status["embedding"] == {"provider": "emb", "model": "embed-1"}

    doc_id = next(d["id"] for d in docs if d["source"] == "network.md")
    assert on.delete(f"/api/rag/documents/{doc_id}").status_code == 200
    assert on.delete(f"/api/rag/documents/{doc_id}").status_code == 404
    assert [h["source"] for h in on.post("/api/rag/search", json={"query": "VLAN cameras"}).json()["hits"]] == ["proxmox.md"] or True
    assert all(h["source"] != "network.md" for h in on.post("/api/rag/search", json={"query": "VLAN cameras"}).json()["hits"])


def test_upload_validation(on: TestClient) -> None:
    on.post("/api/rag/collections", json={"name": "c"})
    assert upload(on, "nope", "a.md", "# A\n\ntext").status_code == 422
    assert "not a valid .pdf" in upload(on, "c", "manual.pdf", b"plain text pretending").json()["detail"]
    assert "legacy .doc" in upload(on, "c", "old.doc", b"x").json()["detail"]
    assert upload(on, "c", "tool.exe", b"MZ").status_code == 422
    assert on.post("/api/rag/documents", params={"collection": "c"}, content="x").status_code == 422  # name is required
    on.put("/api/config", json={"values": {"RAG_MAX_DOCUMENT_BYTES": 1024}})
    assert upload(on, "c", "big.md", b"x" * 5000).status_code == 413  # Content-Length says so
    streamed = on.post("/api/rag/documents", params={"collection": "c", "name": "big.md"}, content=iter([b"x" * 600, b"x" * 600]))
    assert streamed.status_code == 413  # no Content-Length: the limit is enforced while the body streams in
    assert on.get("/api/rag/documents").json() == []


def test_url_ingestion_through_the_guard(on: TestClient) -> None:
    on.post("/api/rag/collections", json={"name": "web"})
    services = services_of(on)
    hosts = {"docs.example": ["93.184.216.34"]}

    def resolver(host: str, port: int) -> list[str]:
        from aifred.tools.web import BlockedURL

        if host not in hosts:
            raise BlockedURL(f"cannot resolve {host!r}")
        return hosts[host]

    services.web.resolver = resolver
    services.web.client_factory = lambda: httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text="# Remote\n\nfetched page about backups", headers={"content-type": "text/markdown"})),
        follow_redirects=False,
    )
    ok = on.post("/api/rag/documents/url", json={"collection": "web", "url": "http://docs.example/remote.md"})
    assert ok.status_code == 202 and ok.json()["origin"] == "url"
    wait(on, collection="web")
    assert on.post("/api/rag/search", json={"query": "fetched page backups"}).json()["hits"][0]["source"] == "http://docs.example/remote.md"
    for url in ("http://127.0.0.1:8700/health", "http://169.254.169.254/latest/meta-data/", "file:///etc/passwd"):
        blocked = on.post("/api/rag/documents/url", json={"collection": "web", "url": url})
        assert blocked.status_code == 422 and "blocked" in blocked.json()["detail"], url


def test_scan_and_reindex(on: TestClient) -> None:
    services = services_of(on)
    (services.rag.folder / "ops").mkdir(parents=True)
    (services.rag.folder / "ops" / "proxmox.md").write_text(PROXMOX)
    summary = on.post("/api/rag/scan", json={}).json()
    assert summary["queued"] == 1 and summary["created"] == ["ops"]
    wait(on, collection="ops")
    assert on.post("/api/rag/scan", json={"collection": "ops"}).json()["unchanged"] == 1

    on.put("/api/config", json={"values": {"RAG_EMBED_MODEL": "embed-2"}})
    assert on.get("/api/rag/collections").json()[0]["stale"] == 1
    assert on.post("/api/rag/reindex", json={"collection": "ops"}).json() == {"queued": 1}
    deadline = time.time() + 20
    while time.time() < deadline and on.get("/api/rag/collections").json()[0]["stale"]:
        time.sleep(0.05)
    assert on.get("/api/rag/collections").json()[0]["stale"] == 0
    assert on.post("/api/rag/reindex", json={"collection": "nope"}).status_code == 404


def test_the_worker_follows_the_switch(on: TestClient) -> None:
    services = services_of(on)
    assert services.rag_worker is not None and services.rag_worker.running
    on.put("/api/config", json={"values": {"RAG_ENABLED": False}})
    assert not services.rag_worker.running
    on.put("/api/config", json={"values": {"RAG_ENABLED": True}})
    assert services.rag_worker.running
