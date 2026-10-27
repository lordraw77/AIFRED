"""OpenAI-compatible embedding client (spec 9.4, "Embeddings").

Independent of the provider layer on purpose: it takes a resolved :class:`EmbedTarget`, so both the
knowledge base and the model test can use it. Calls are synchronous (they run in worker threads).
Errors are classified with the same rules as chat calls (:mod:`aifred.llm`); transient ones are
retried with backoff, permanent ones are raised at once.
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx
import numpy as np

from aifred.llm import LLMError, Outcome, classify_exception, classify_http

logger = logging.getLogger(__name__)

TRANSIENT = frozenset({Outcome.UNAVAILABLE, Outcome.TIMEOUT, Outcome.RATE_LIMIT, Outcome.QUOTA})
_MAX_ATTEMPTS = 4
_MAX_WAIT = 30.0


class EmbeddingError(Exception):
    """An embedding request failed.

    Attributes:
        outcome: Failure class (same as for chat calls).
        retry_after: Seconds the provider asked to wait, if any.
    """

    def __init__(self, message: str, outcome: Outcome, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.outcome = outcome
        self.retry_after = retry_after

    @property
    def transient(self) -> bool:
        """Whether trying again later can help (service down, rate limit, quota)."""
        return self.outcome in TRANSIENT


@dataclass(frozen=True)
class EmbedTarget:
    """Where and how to embed.

    Attributes:
        url: Full ``/embeddings`` URL.
        model: Embedding model id.
        api_key: Key for this call, if any.
        headers: Extra request headers.
        verify: TLS verification: ``True``, ``False`` or a CA bundle path.
        connect: Connect timeout (s).
        total: Request timeout (s).
    """

    url: str
    model: str
    api_key: str | None
    headers: dict[str, str]
    verify: bool | str = True
    connect: float = 10.0
    total: float = 60.0


@dataclass
class EmbedResult:
    """Vectors for a list of texts.

    Attributes:
        vectors: ``(n, dim)`` float32 matrix, each row L2-normalised (a zero vector stays zero).
        tokens: Prompt tokens reported by the provider, or a rough estimate.
    """

    vectors: np.ndarray
    tokens: int


ClientFactory = Callable[[EmbedTarget], httpx.Client]


def default_client(target: EmbedTarget) -> httpx.Client:
    """HTTP client honouring the provider's TLS option; redirects are not followed."""
    return httpx.Client(verify=target.verify, timeout=httpx.Timeout(target.total, connect=target.connect), follow_redirects=False)


def normalise_rows(matrix: np.ndarray) -> np.ndarray:
    """L2-normalise each row so cosine similarity becomes a dot product."""
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return np.divide(matrix, norms, out=np.zeros_like(matrix), where=norms > 0)


def to_blob(vector: np.ndarray) -> bytes:
    """Little-endian float32 bytes, as stored in the database."""
    return np.asarray(vector, dtype="<f4").tobytes()


def from_blob(blob: bytes) -> np.ndarray:
    """Inverse of :func:`to_blob`."""
    return np.frombuffer(blob, dtype="<f4")


class EmbeddingClient:
    """Embeds batches of text.

    Args:
        client_factory: Builds the HTTP client (replaceable in tests).
        sleep: Used for backoff (replaceable in tests).
        attempts: Tries per request for transient failures.
    """

    def __init__(self, client_factory: ClientFactory = default_client, sleep: Callable[[float], Any] = time.sleep, attempts: int = _MAX_ATTEMPTS) -> None:
        self._factory = client_factory
        self._sleep = sleep
        self._attempts = max(1, attempts)

    def embed(self, target: EmbedTarget, texts: list[str]) -> EmbedResult:
        """Embed ``texts`` in one request.

        Raises:
            EmbeddingError: On a permanent failure, or a transient one that outlasted the retries.
        """
        if not texts:
            return EmbedResult(np.zeros((0, 0), dtype=np.float32), 0)
        payload = {"model": target.model, "input": [t if t.strip() else " " for t in texts]}
        headers = {"Content-Type": "application/json", "Accept": "application/json", **target.headers}
        if target.api_key:
            headers["Authorization"] = f"Bearer {target.api_key}"
        last: EmbeddingError | None = None
        for attempt in range(self._attempts):
            try:
                return self._once(target, payload, headers, len(texts))
            except EmbeddingError as exc:
                last = exc
                if not exc.transient or exc.outcome is Outcome.QUOTA or attempt == self._attempts - 1:
                    raise
                wait = min(exc.retry_after if exc.retry_after is not None else 2.0**attempt, _MAX_WAIT)
                logger.info("Embedding call failed (%s); retrying in %.1f s", exc, wait)
                self._sleep(wait)
        assert last is not None  # pragma: no cover - the loop returns or raises
        raise last

    def _once(self, target: EmbedTarget, payload: dict[str, Any], headers: dict[str, str], count: int) -> EmbedResult:
        try:
            with self._factory(target) as client:
                response = client.post(target.url, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            err = classify_exception(exc)
            raise EmbeddingError(str(err), err.outcome) from None
        if response.status_code >= 400:
            err = classify_http(response.status_code, response.text, response.headers)
            raise EmbeddingError(str(err), err.outcome, err.retry_after)
        try:
            body = response.json()
            data = body["data"]
            if not isinstance(data, list) or not all(isinstance(r, dict) for r in data):
                raise ValueError("data is not a list of objects")
            rows = sorted(data, key=lambda r: r.get("index", 0))
            matrix = np.asarray([r["embedding"] for r in rows], dtype=np.float32)
            usage = body.get("usage") or {}
        except (ValueError, KeyError, TypeError, AttributeError, OverflowError):
            raise EmbeddingError("the embeddings endpoint answered with an unexpected body", Outcome.FATAL) from None
        if matrix.ndim != 2 or matrix.shape[0] != count or matrix.shape[1] == 0:
            raise EmbeddingError(f"expected {count} vectors, got shape {matrix.shape}", Outcome.FATAL)
        if not np.isfinite(matrix).all():
            raise EmbeddingError("the embedding contains NaN or infinite values", Outcome.FATAL)
        tokens = int(usage.get("prompt_tokens") or usage.get("total_tokens") or 0)
        return EmbedResult(normalise_rows(matrix), tokens or max(1, sum(len(t) for t in payload["input"]) // 4))


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity of two (already normalised or not) vectors."""
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    return 0.0 if denom == 0 or math.isnan(denom) else float(np.dot(a, b) / denom)


__all__ = ["EmbeddingClient", "EmbedResult", "EmbedTarget", "EmbeddingError", "LLMError", "cosine", "from_blob", "to_blob"]
