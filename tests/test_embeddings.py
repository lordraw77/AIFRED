"""Tests for aifred.embeddings."""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pytest

from aifred.embeddings import EmbeddingClient, EmbeddingError, EmbedTarget, cosine, from_blob, normalise_rows, to_blob
from aifred.llm import Outcome
from tests.fake_llm import FakeLLM, Status


def target(fake: FakeLLM | None, **kw: object) -> EmbedTarget:
    url = (fake.url if fake else "http://127.0.0.1:9/v1") + "/embeddings"
    return EmbedTarget(**({"url": url, "model": "embed-1", "api_key": None, "headers": {}, "connect": 2.0, "total": 5.0} | kw))  # type: ignore[arg-type]


def client(sleeps: list[float] | None = None, attempts: int = 4) -> EmbeddingClient:
    return EmbeddingClient(sleep=(sleeps.append if sleeps is not None else (lambda s: None)), attempts=attempts)


def test_embeds_a_batch_and_normalises(fake_llm: Callable[[], FakeLLM]) -> None:
    fake = fake_llm()
    result = client().embed(target(fake, api_key="sk-embed"), ["fast car", "quick automobile", "disk backup"])
    assert result.vectors.shape == (3, 256) and result.vectors.dtype == np.float32
    assert np.allclose(np.linalg.norm(result.vectors, axis=1), 1.0, atol=1e-5)
    assert cosine(result.vectors[0], result.vectors[1]) > 0.99  # synonyms
    assert cosine(result.vectors[0], result.vectors[2]) < 0.1
    assert result.tokens == 6
    sent = fake.embed_requests[0]
    assert sent.body == {"model": "embed-1", "input": ["fast car", "quick automobile", "disk backup"]}
    assert sent.headers["authorization"] == "Bearer sk-embed"


def test_empty_input_and_blank_texts(fake_llm: Callable[[], FakeLLM]) -> None:
    fake = fake_llm()
    assert client().embed(target(fake), []).vectors.shape[0] == 0
    assert fake.embed_requests == []
    client().embed(target(fake), ["", "  ", "text"])
    assert fake.embed_requests[0].body["input"] == [" ", " ", "text"]  # providers reject empty strings


def test_blob_round_trip() -> None:
    v = normalise_rows(np.array([[3.0, 4.0, 0.0]], dtype=np.float32))[0]
    assert np.allclose(v, [0.6, 0.8, 0.0])
    assert from_blob(to_blob(v)).tolist() == pytest.approx(v.tolist())
    assert len(to_blob(v)) == 12
    assert normalise_rows(np.zeros((1, 3), dtype=np.float32)).tolist() == [[0.0, 0.0, 0.0]]  # a zero vector stays zero
    assert cosine(np.zeros(3), np.ones(3)) == 0.0


def test_transient_failures_are_retried_with_backoff(fake_llm: Callable[[], FakeLLM]) -> None:
    fake = fake_llm()
    fake.embed_steps += [Status(503), Status(429, {"error": {"message": "slow down"}}, {"Retry-After": "3"})]
    sleeps: list[float] = []
    result = client(sleeps).embed(target(fake), ["hello"])
    assert result.vectors.shape == (1, 256) and len(fake.embed_requests) == 3
    assert sleeps == [1.0, 3.0]  # exponential for 503, the provider's Retry-After for 429


def test_retries_give_up_and_report_the_last_error(fake_llm: Callable[[], FakeLLM]) -> None:
    fake = fake_llm()
    fake.embed_steps += [Status(503)] * 5
    with pytest.raises(EmbeddingError) as exc:
        client(attempts=3).embed(target(fake), ["x"])
    assert exc.value.outcome is Outcome.UNAVAILABLE and exc.value.transient
    assert len(fake.embed_requests) == 3


@pytest.mark.parametrize(
    ("step", "outcome", "transient"),
    [
        (Status(401), Outcome.AUTH, False),
        (Status(404, {"error": {"message": "model 'embed-1' not found"}}), Outcome.MODEL_MISSING, False),
        (Status(400, {"error": {"message": "maximum context length is 512 tokens"}}), Outcome.CONTEXT, False),
        (Status(400, {"error": {"message": "bad request"}}), Outcome.FATAL, False),
        (Status(402), Outcome.QUOTA, True),
    ],
)
def test_permanent_failures_are_not_retried(fake_llm: Callable[[], FakeLLM], step: Status, outcome: Outcome, transient: bool) -> None:
    fake = fake_llm()
    fake.embed_steps.append(step)
    with pytest.raises(EmbeddingError) as exc:
        client().embed(target(fake), ["x"])
    assert exc.value.outcome is outcome and exc.value.transient is transient
    assert len(fake.embed_requests) == 1  # quota is transient for the worker (it waits) but not retried here


def test_unreachable_endpoint_is_transient() -> None:
    sleeps: list[float] = []
    with pytest.raises(EmbeddingError) as exc:
        client(sleeps, attempts=2).embed(target(None), ["x"])
    assert exc.value.outcome is Outcome.UNAVAILABLE and exc.value.transient and sleeps == [1.0]


def test_malformed_answers_are_rejected(fake_llm: Callable[[], FakeLLM]) -> None:
    fake = fake_llm()
    fake.embed_steps.append(Status(200, {"data": "nope"}))
    with pytest.raises(EmbeddingError, match="unexpected body"):
        client().embed(target(fake), ["x"])
    fake.embed_steps.append(Status(200, {"data": [{"index": 0, "embedding": [0.1, 0.2]}]}))
    with pytest.raises(EmbeddingError, match="expected 2 vectors"):
        client().embed(target(fake), ["x", "y"])
    fake.embed_steps.append(Status(200, {"data": [{"index": 0, "embedding": [0.1, 1e39]}]}))  # infinite once stored as float32
    with pytest.raises(EmbeddingError, match="NaN or infinite"):
        client().embed(target(fake), ["x"])
    fake.embed_steps.append(Status(200, {"data": [{"index": 0, "embedding": [0.1, 10**400]}]}))  # too large for any float
    with pytest.raises(EmbeddingError, match="unexpected body"):
        client().embed(target(fake), ["x"])
    fake.embed_steps.append(Status(200, {"data": [{"index": 0, "embedding": []}]}))
    with pytest.raises(EmbeddingError):
        client().embed(target(fake), ["x"])


def test_out_of_order_answers_are_sorted_by_index(fake_llm: Callable[[], FakeLLM]) -> None:
    fake = fake_llm()
    fake.embed_steps.append(Status(200, {"data": [{"index": 1, "embedding": [0.0, 1.0]}, {"index": 0, "embedding": [1.0, 0.0]}]}))
    result = client().embed(target(fake), ["a", "b"])
    assert result.vectors.tolist() == [[1.0, 0.0], [0.0, 1.0]]
