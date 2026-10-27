"""Tests for reranking and for the golden-question evaluation of the knowledge base."""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from aifred.embeddings import EmbeddingClient
from aifred.rag import Hit, RagError
from aifred.rag_eval import evaluate, first_match, matches
from tests.conftest import login
from aifred.instructions import InstructionError
from tests.fake_llm import FakeLLM, Status, Text
from tests.rag_support import KB, NETWORK, PROXMOX


def hit(source: str = "a.md", text: str = "some text", title: str = "T") -> Hit:
    return Hit("c", source, title, "", [0], text, 1.0)


def golden(source: str = "", phrase: str = "") -> dict[str, Any]:
    return {"question": "q", "source": source, "phrase": phrase, "collection": ""}


@pytest.fixture
def ranked(kb: KB) -> KB:
    """A knowledge base with two documents, and a rerank model on the same fake provider."""
    kb.add("proxmox.md", PROXMOX)
    kb.add("network.md", NETWORK)
    kb.index()
    kb.services.config.update({"RAG_RERANK_PROVIDER": "emb", "RAG_RERANK_MODEL": "rerank-1"})
    return kb


def test_matching_rules() -> None:
    assert matches(hit("proxmox.md"), golden(source="proxmox")) and not matches(hit("network.md"), golden(source="proxmox"))
    assert matches(hit(text="Error PVE-ERR-4471 means"), golden(phrase="pve-err-4471"))
    assert matches(hit("proxmox.md", "ring is down"), golden("proxmox", "ring is down"))
    assert not matches(hit("proxmox.md", "other"), golden("proxmox", "ring is down"))       # every field that is set must match
    assert not matches(hit(), golden())                                                      # no criterion matches nothing
    assert first_match([hit("x"), hit("proxmox.md")], golden(source="proxmox")) == 2 and first_match([hit("x")], golden(source="zzz")) is None


def test_rerank_reorders_the_candidates_and_marks_the_hits(ranked: KB) -> None:
    ranked.fake.rerank_fn = lambda query, docs: [1.0 if "VLAN" in d else 0.1 for d in docs]
    result = ranked.rag.search("proxmox network upgrade", limit=2)
    assert result.hits[0].source == "network.md" and result.hits[0].rerank == 1.0
    assert "rerank 1.00" in result.hits[0].render(1)
    sent = ranked.fake.rerank_requests[0].body
    assert sent["model"] == "rerank-1" and sent["query"] == "proxmox network upgrade" and len(sent["documents"]) >= 2
    assert ranked.rag.search("proxmox network upgrade", limit=2, rerank=False).hits[0].rerank is None


def test_no_rerank_model_means_no_rerank_call(kb: KB) -> None:
    kb.add("proxmox.md", PROXMOX)
    kb.index()
    assert not kb.rag.reranking
    assert kb.rag.search("proxmox upgrade").hits and kb.fake.rerank_requests == []


def test_a_failing_rerank_model_keeps_the_fused_order_and_says_so(ranked: KB) -> None:
    ranked.rag.embedder = EmbeddingClient(sleep=lambda s: None, attempts=1)
    ranked.fake.rerank_steps = [Status(500)]
    result = ranked.rag.search("proxmox upgrade")
    assert result.hits and all(h.rerank is None for h in result.hits)
    assert any("reranking skipped" in n for n in result.notes)
    ranked.fake.rerank_steps = [Status(200, {"results": [{"index": 99, "relevance_score": 1}]})]
    assert any("reranking skipped" in n for n in ranked.rag.search("proxmox upgrade").notes)    # an index out of range is refused


def test_candidates_the_model_left_out_still_follow(ranked: KB) -> None:
    ranked.fake.rerank_steps = [Status(200, {"results": [{"index": 0, "relevance_score": 0.9}]})]
    result = ranked.rag.search("proxmox network upgrade", limit=5)
    plain = ranked.rag.search("proxmox network upgrade", limit=5, rerank=False)
    assert result.hits[0].rerank == 0.9 and len(result.hits) == len(plain.hits)      # the others are kept, after the scored one


def test_evaluation_scores_each_variant_and_stores_the_run(ranked: KB) -> None:
    store = ranked.services.store
    store.rag_golden_add("How do I check corosync quorum?", "", "proxmox.md", "pvecm status")
    store.rag_golden_add("Which VLAN has the cameras?", "", "network.md", "VLAN 20")
    store.rag_golden_add("What is the airspeed of a swallow?", "", "birds.md", "")           # not in the documents: a miss
    results = evaluate(ranked.rag, 3, "baseline")
    by = {r["variant"]: r for r in results}
    assert set(by) == {"hybrid", "dense", "keyword", "hybrid+rerank"}
    assert by["hybrid"]["total"] == 3 and 0.6 <= by["hybrid"]["recall"] <= 0.7 and by["hybrid"]["misses"] == ["What is the airspeed of a swallow?"]
    assert 0 < by["keyword"]["mrr"] <= by["keyword"]["recall"] <= 1
    store.rag_golden_add("proxmox network upgrade", "", "proxmox.md", "")               # several candidates: worth re-ordering
    before = len(store.rag_eval_list())
    ranked.fake.rerank_requests.clear()
    evaluate(ranked.rag, 3, variants=["hybrid", "dense", "keyword"])
    assert ranked.fake.rerank_requests == []                                            # plain variants never call the rerank model
    evaluate(ranked.rag, 3, variants=["hybrid+rerank"])
    assert ranked.fake.rerank_requests != []
    stored = store.rag_eval_list()
    assert before == 4 and len(stored) == 8 and stored[4]["label"] == "baseline" and stored[0]["misses"] is not None


def test_recall_and_mrr_are_computed_from_the_rank_of_the_first_match(ranked: KB, monkeypatch: pytest.MonkeyPatch) -> None:
    from aifred.rag import SearchResult

    store = ranked.services.store
    for source in ("first.md", "second.md", "missing.md"):
        store.rag_golden_add(f"question about {source}", "", source, "")
    answers = {"first.md": [hit("first.md")], "second.md": [hit("other.md"), hit("second.md")], "missing.md": [hit("other.md")]}
    monkeypatch.setattr(ranked.rag, "search", lambda q, c=None, limit=None, mode="hybrid", rerank=None: SearchResult(hits=answers[q.split()[-1]]))
    [row] = evaluate(ranked.rag, 5, variants=["hybrid"])
    assert row["recall"] == pytest.approx(2 / 3) and row["mrr"] == pytest.approx((1 + 0.5 + 0) / 3)


def test_evaluation_needs_questions_and_a_known_variant(ranked: KB) -> None:
    with pytest.raises(RagError, match="no golden questions"):
        evaluate(ranked.rag, 5)
    ranked.services.store.rag_golden_add("q?", "", "proxmox.md", "")
    with pytest.raises(RagError, match="unknown variant"):
        evaluate(ranked.rag, 5, variants=["magic"])
    ranked.services.config.update({"RAG_RERANK_PROVIDER": None, "RAG_RERANK_MODEL": None})
    assert {r["variant"] for r in evaluate(ranked.rag, 5)} == {"hybrid", "dense", "keyword"}   # no rerank variant without a model


@pytest.fixture
def api(client: TestClient) -> TestClient:
    client.headers["X-CSRF-Token"] = login(client)
    return client


def test_golden_api_round_trip(api: TestClient) -> None:
    assert api.post("/api/rag/golden", json={"question": "Where is the DNS?"}).status_code == 422          # no source nor phrase
    created = api.post("/api/rag/golden", json={"question": "Where is the DNS?", "source": "network.md"})
    assert created.status_code == 200
    [item] = api.get("/api/rag/golden").json()
    assert item["question"] == "Where is the DNS?" and item["source"] == "network.md"
    assert api.delete(f"/api/rag/golden/{item['id']}").status_code == 200
    assert api.delete(f"/api/rag/golden/{item['id']}").status_code == 404
    assert TestClient(api.app).get("/api/rag/golden").status_code == 401


def test_evaluate_api_needs_the_knowledge_base_on_and_questions(api: TestClient) -> None:
    assert api.post("/api/rag/evaluate", json={}).status_code == 409
    assert api.post("/api/rag/evaluate", json={"k": 99}).status_code == 422
    assert api.get("/api/rag/evals").json() == []


@pytest.mark.anyio
async def test_questions_are_written_from_sampled_passages(ranked: KB, llm_ready: FakeLLM) -> None:
    chunks = ranked.services.store.rag_sample_chunks(2)
    assert chunks and all({"text", "source", "collection"} <= set(c) for c in chunks)
    llm_ready.queue(Text(json.dumps([{"n": 1, "question": "How do I upgrade a node?"}, {"n": 9, "question": "out of range"}, {"n": 2, "question": ""}])))
    questions = await ranked.services.assist.golden_questions(chunks)
    assert questions == [{"question": "How do I upgrade a node?", "collection": chunks[0]["collection"], "source": chunks[0]["source"], "phrase": ""}]
    assert "<passage" in llm_ready.requests[0].body["messages"][1]["content"]
    llm_ready.queue(Text("no json here"))
    with pytest.raises(InstructionError, match="usable questions"):
        await ranked.services.assist.golden_questions(chunks)
    with pytest.raises(InstructionError, match="no indexed passages"):
        await ranked.services.assist.golden_questions([])
