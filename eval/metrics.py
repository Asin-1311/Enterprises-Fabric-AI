"""Retrieval metrics, implemented in-house (CLAUDE.md section 11).

All metrics work on a ranked list of DOCUMENT ids (chunks are first collapsed to their
documents, keeping each document's best rank), because the gold labels are documents.
Relevance is binary: a document is relevant if it is in `gold`.
"""

import math
from collections.abc import Iterable, Sequence


def dedupe_docs(doc_ids_in_rank_order: Iterable[str]) -> list[str]:
    """Collapse a chunk ranking to a document ranking (first occurrence wins)."""
    seen: set[str] = set()
    out: list[str] = []
    for d in doc_ids_in_rank_order:
        if d not in seen:
            seen.add(d)
            out.append(d)
    return out


def recall_at_k(ranked: Sequence[str], gold: set[str], k: int) -> float:
    """Fraction of gold documents found in the top k."""
    if not gold:
        raise ValueError("recall is undefined without gold documents")
    return len(set(ranked[:k]) & gold) / len(gold)


def reciprocal_rank(ranked: Sequence[str], gold: set[str]) -> float:
    """1 / rank of the first relevant document (0 if none is retrieved)."""
    for rank, d in enumerate(ranked, start=1):
        if d in gold:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(ranked: Sequence[str], gold: set[str], k: int) -> float:
    """Binary-relevance nDCG@k: DCG = sum rel_i / log2(i + 1), divided by the ideal DCG."""
    if not gold:
        raise ValueError("nDCG is undefined without gold documents")
    dcg = sum(1.0 / math.log2(i + 1) for i, d in enumerate(ranked[:k], start=1) if d in gold)
    ideal = sum(1.0 / math.log2(i + 1) for i in range(1, min(len(gold), k) + 1))
    return dcg / ideal


def superseded_order_ok(ranked: Sequence[str], pairs: Sequence[tuple[str, str]]) -> bool:
    """True if, for every (current, superseded) pair, the current version is retrieved and
    ranks above the superseded one (a superseded version that is absent is fine)."""
    pos = {d: i for i, d in enumerate(ranked)}
    for current, old in pairs:
        if current not in pos:
            return False
        if old in pos and pos[old] < pos[current]:
            return False
    return True


def mean(values: Sequence[float]) -> float | None:
    """Arithmetic mean, or None for an empty list (reported as null, never as 0)."""
    return sum(values) / len(values) if values else None
