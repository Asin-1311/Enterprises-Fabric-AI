"""Agent 4: re-rank and compress.

- `rerank()`: the cross-encoder scores every candidate against the ORIGINAL question and
  keeps the top `rerank_top_n` (5). It also counts how many chunks reach the "good"
  threshold, which drives the single follow-up retrieval (CLAUDE.md section 7.3).
- `compress_evidence()`: MODEL_LIGHT selects the relevant sentences (verbatim) from the
  kept chunks; see app/retrieval/compress.py.
"""

from dataclasses import dataclass

from app.agents.llm import LLMCaller, LLMResult
from app.retrieval.compress import CompressedChunk, compress
from app.retrieval.hybrid import RetrievedChunk
from app.retrieval.rerank import Reranker


@dataclass
class Ranked:
    chunk: RetrievedChunk
    rerank_score: float


def rerank(question: str, candidates: list[RetrievedChunk], reranker: Reranker,
           top_n: int, threshold: float,
           known: dict[str, float] | None = None) -> tuple[list[Ranked], int, dict[str, float]]:
    """Top-n chunks by cross-encoder score, the number of candidates >= threshold, and all
    scores so far. `known` holds scores from an earlier pass (the follow-up only scores
    new chunks; the cross-encoder is the slowest CPU step)."""
    scores = dict(known or {})
    new = [c for c in candidates if c.chunk_id not in scores]
    for c, s in zip(new, reranker.score(question, [c.text for c in new])):
        scores[c.chunk_id] = s
    ranked = sorted((Ranked(c, scores[c.chunk_id]) for c in candidates),
                    key=lambda r: (-r.rerank_score, r.chunk.chunk_id))
    good = sum(1 for r in ranked if r.rerank_score >= threshold)
    return ranked[:top_n], good, scores


def compress_evidence(question: str, ranked: list[Ranked], llm: LLMCaller,
                      threshold: float) -> tuple[list[CompressedChunk], LLMResult | None]:
    """Compress the good chunks (the best one is always kept)."""
    usable = [r for i, r in enumerate(ranked) if i == 0 or r.rerank_score >= threshold]
    if not usable:
        return [], None
    restricted = frozenset(r.chunk.chunk_id for r in usable if r.chunk.classification == "restricted")
    return compress(question, [(r.chunk.chunk_id, r.chunk.text) for r in usable], llm, restricted)
