"""Reciprocal rank fusion (RRF).

    RRF(d) = sum over ranked lists L that contain d of  1 / (k + rank_L(d))

with rank starting at 1 and k = 60 (Cormack et al., 2009). RRF uses only ranks, not raw
scores, so a cosine similarity (0..1) and a BM25 score (0..30+) can be combined without
normalising either.

Weighted RRF (M15a): each list L may carry a weight w_L, so a contribution is
w_L / (k + rank_L(d)). The weights depend on the question type (keyword search counts
more for factual and procedural questions, dense search for comparisons and analysis);
they come from config and are recorded with every fused result.
"""

from collections.abc import Sequence


def reciprocal_rank_fusion(rankings: Sequence[Sequence[str]], k: int = 60,
                           weights: Sequence[float] | None = None) -> list[tuple[str, float]]:
    """Fuse ranked lists of ids, list i weighted by weights[i] (default 1.0 each).
    Returns (id, score) sorted by score, then id."""
    scores: dict[str, float] = {}
    for i, ranking in enumerate(rankings):
        w = 1.0 if weights is None else weights[i]
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + w / (k + rank)
    return sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))
