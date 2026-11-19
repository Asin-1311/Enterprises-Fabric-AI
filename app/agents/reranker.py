"""Agent 4: re-rank and compress.

- `rerank_groups()` (M14a): the cross-encoder scores each sub-query's candidates against
  THAT sub-query, keeps the best of each, and merges the lists round-robin by rank, so
  every side of a comparison survives (audit finding: re-ranking the merged list against
  the whole question could drop one side). It also counts how many distinct chunks reach
  the "good" threshold, which drives the single follow-up retrieval (CLAUDE.md 7.3).
- `rerank()`: the Phase 1 single-list form (one group: the question and its candidates).
- `compress_evidence()`: MODEL_LIGHT selects the relevant sentences (verbatim) from the
  kept chunks, each judged against its own sub-query; see app/retrieval/compress.py.
"""

import math
from dataclasses import dataclass

from app.agents.llm import LLMCaller, LLMResult
from app.retrieval.compress import CompressedChunk, compress
from app.retrieval.hybrid import RetrievedChunk
from app.retrieval.rerank import Reranker


@dataclass
class Ranked:
    chunk: RetrievedChunk
    rerank_score: float
    query: str = ""  # the sub-query this chunk was ranked for (M14a)


@dataclass
class Group:
    """One sub-query (or follow-up query) and the candidates its search returned."""

    query: str
    candidates: list[RetrievedChunk]


def rerank_groups(groups: list[Group], reranker: Reranker, top_n: int, threshold: float,
                  known: dict[str, float] | None = None
                  ) -> tuple[list[Ranked], int, dict[str, float]]:
    """Re-rank each group against its own query and merge (see the module docstring).

    Each group keeps max(2, ceil(top_n / groups)) chunks; the merged list takes the 1st of
    every group, then the 2nd of every group, ..., without duplicates. Returns the merged
    list, the number of distinct candidates scoring >= threshold in their group, and all
    scores so far, keyed "<query>\\x1f<chunk_id>" (scores are reused: the cross-encoder is
    the slowest CPU step)."""
    scores = dict(known or {})
    per_group: list[list[Ranked]] = []
    good_ids: set[str] = set()
    for g in groups:
        new = [c for c in g.candidates if f"{g.query}\x1f{c.chunk_id}" not in scores]
        for c, s in zip(new, reranker.score(g.query, [c.text for c in new])):
            scores[f"{g.query}\x1f{c.chunk_id}"] = s
        ranked = sorted((Ranked(c, scores[f"{g.query}\x1f{c.chunk_id}"], g.query) for c in g.candidates),
                        key=lambda r: (-r.rerank_score, r.chunk.chunk_id))
        good_ids |= {r.chunk.chunk_id for r in ranked if r.rerank_score >= threshold}
        per_group.append(ranked)
    keep = max(2, math.ceil(top_n / max(len(groups), 1))) if len(groups) > 1 else top_n
    merged: list[Ranked] = []
    seen: set[str] = set()
    for rank in range(keep):
        for ranked in per_group:
            if rank < len(ranked) and ranked[rank].chunk.chunk_id not in seen:
                merged.append(ranked[rank])
                seen.add(ranked[rank].chunk.chunk_id)
    return merged, len(good_ids), scores


def rerank(question: str, candidates: list[RetrievedChunk], reranker: Reranker,
           top_n: int, threshold: float,
           known: dict[str, float] | None = None) -> tuple[list[Ranked], int, dict[str, float]]:
    """Top-n chunks for one query (the Phase 1 form; one group)."""
    return rerank_groups([Group(question, candidates)], reranker, top_n, threshold, known)


def compress_evidence(question: str, ranked: list[Ranked], llm: LLMCaller,
                      threshold: float) -> tuple[list[CompressedChunk], LLMResult | None]:
    """Compress the good chunks (the best one is always kept); each chunk is judged
    against the sub-query it was ranked for."""
    usable = [r for i, r in enumerate(ranked) if i == 0 or r.rerank_score >= threshold]
    if not usable:
        return [], None
    restricted = frozenset(r.chunk.chunk_id for r in usable if r.chunk.classification == "restricted")
    sub_queries = {r.chunk.chunk_id: r.query for r in usable if r.query and r.query != question}
    return compress(question, [(r.chunk.chunk_id, r.chunk.text) for r in usable], llm, restricted,
                    sub_queries)


def whole_chunks(ranked: list[Ranked], threshold: float) -> list[CompressedChunk]:
    """The light path's evidence: the good chunks as they are, without compression."""
    from app.retrieval.compress import split_sentences

    out = []
    for i, r in enumerate(ranked):
        if i == 0 or r.rerank_score >= threshold:
            heading, _, body = r.chunk.text.partition("\n")
            out.append(CompressedChunk(r.chunk.chunk_id, heading, split_sentences(body)))
    return out
