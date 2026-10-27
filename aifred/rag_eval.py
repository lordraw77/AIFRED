"""Measuring retrieval: golden questions and the metrics that tell whether a change made search better or worse.

A golden question says where its answer lives: a document source (name or part of it), a phrase that the passage must
contain, or both. A search *hits* a question when one of its first ``k`` results matches. The run reports recall@k (the
share of questions hit) and the mean reciprocal rank (how near the top the first match sits) for each way of searching,
and each result is stored so a later run can be compared with it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from aifred.rag import Hit, RagError

if TYPE_CHECKING:
    from aifred.rag import RagService

VARIANTS = ("hybrid", "dense", "keyword", "hybrid+rerank")


def matches(hit: Hit, golden: dict[str, Any]) -> bool:
    """Whether a result is where the golden question says its answer is (every field that is set must match)."""
    source, phrase = golden["source"].strip().lower(), golden["phrase"].strip().lower()
    if not source and not phrase:
        return False
    return (not source or source in hit.source.lower() or source in hit.title.lower()) and (not phrase or phrase in hit.text.lower())


def first_match(hits: list[Hit], golden: dict[str, Any]) -> int | None:
    """The 1-based rank of the first matching result, if any."""
    return next((i for i, h in enumerate(hits, start=1) if matches(h, golden)), None)


def evaluate(service: RagService, k: int, label: str = "", variants: list[str] | None = None) -> list[dict[str, Any]]:
    """Run every golden question through each search variant, store the results and return them.

    ``hybrid+rerank`` is only run when a rerank model is configured; ``hybrid`` is always plain fusion so the two compare.

    Raises:
        RagError: If there are no golden questions or a variant is unknown.
    """
    store = service._store
    golden = store.rag_golden_list()
    if not golden:
        raise RagError("there are no golden questions yet: add a few, or let the model write them from your documents")
    k = max(1, min(int(k), 10))
    chosen = variants or [v for v in VARIANTS if v != "hybrid+rerank" or service.reranking]
    out = []
    for variant in chosen:
        if variant not in VARIANTS:
            raise RagError(f"unknown variant {variant!r}")
        mode, rerank = ("hybrid", True) if variant == "hybrid+rerank" else (variant, False)
        found, reciprocal, misses = 0, 0.0, []
        for g in golden:
            hits = service.search(g["question"], [g["collection"]] if g["collection"] else None, k, mode, rerank=rerank).hits
            rank = first_match(hits, g)
            if rank is None:
                misses.append(g["question"])
            else:
                found += 1
                reciprocal += 1 / rank
        row = {"variant": variant, "k": k, "total": len(golden), "recall": found / len(golden), "mrr": reciprocal / len(golden), "misses": misses, "label": label}
        store.rag_eval_add(label, variant, k, len(golden), row["recall"], row["mrr"], misses)
        out.append(row)
    return out
