"""An approximate vector index for very large collections (spec 9.4, "Approximate index").

Exact search (a matrix product over every vector) is the default and stays so: it is reproducible and needs no
maintenance. Beyond about ``RAG_ANN_MIN_CHUNKS`` chunks it costs too much time or memory, so a collection may get
an **inverted-file (IVF) index**: k-means groups the vectors into cells, each chunk is stored with its cell, and a
query reads only the chunks of the ``nprobe`` cells whose centre is nearest, plus the chunks added since the
index was built (an exact scan of that small remainder). Only ``numpy`` is needed and the cell table lives in the
same SQLite file, so nothing is held in memory but the centres.
"""

from __future__ import annotations

import math

import numpy as np

SAMPLE_PER_CELL = 64
KMEANS_ITERATIONS = 8
MIN_CELLS = 16
MAX_CELLS = 4096


def cell_count(members: int) -> int:
    """Number of cells for a collection of this size: about the square root, within sane bounds."""
    return max(MIN_CELLS, min(MAX_CELLS, int(math.sqrt(max(members, 1)))))


def _unit(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms


def kmeans(sample: np.ndarray, cells: int, seed: int = 7) -> np.ndarray:
    """Spherical k-means (cosine) on a sample; returns ``cells`` unit-length centres.

    Args:
        sample: ``(n, dim)`` float32 vectors.
        cells: Wanted number of centres (lowered to ``n`` when the sample is smaller).
        seed: Random seed, so the same data gives the same index.
    """
    rng = np.random.default_rng(seed)
    data = _unit(sample.astype(np.float32, copy=False))
    cells = max(1, min(cells, len(data)))
    centres = data[rng.choice(len(data), size=cells, replace=False)].copy()
    for _ in range(KMEANS_ITERATIONS):
        labels = assign(data, centres)
        for k in range(cells):
            members = data[labels == k]
            if len(members):
                centres[k] = members.mean(axis=0)
            else:
                centres[k] = data[rng.integers(len(data))]      # an empty cell takes a random point instead of dying
        centres = _unit(centres)
    return centres.astype(np.float32)


def assign(vectors: np.ndarray, centres: np.ndarray, block: int = 8192) -> np.ndarray:
    """Index of the nearest centre (largest dot product) of every vector, computed in blocks."""
    out = np.empty(len(vectors), dtype=np.int32)
    for start in range(0, len(vectors), block):
        out[start : start + block] = np.argmax(vectors[start : start + block] @ centres.T, axis=1)
    return out


def nearest_cells(qvec: np.ndarray, centres: np.ndarray, probe: int) -> list[int]:
    """The ``probe`` cells whose centre is most similar to the query."""
    scores = centres @ qvec
    probe = max(1, min(probe, len(scores)))
    best = np.argpartition(-scores, probe - 1)[:probe]
    return [int(i) for i in best[np.argsort(-scores[best])]]
