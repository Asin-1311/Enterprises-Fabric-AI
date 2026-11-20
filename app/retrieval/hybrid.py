"""Hybrid search: dense (Qdrant) + BM25, fused with RRF, then adjusted.

Order of operations for one request (CLAUDE.md section 7.3):
1. ACL first: build the access scope for this user (app/ports.AccessScope). Each index
   applies it inside its own query (Qdrant payload filter; BM25 candidate set or
   OpenSearch filter), so restricted chunks are never scored by either retriever.
2. For each sub-query: dense top-k and BM25 top-k, run concurrently (M14a).
3. Reciprocal rank fusion of all ranked lists (k = 60).
4. Multiply by the superseded weight (and, for time-sensitive questions, the recency
   boost).
5. Policy check (M13): every candidate is evaluated by the policy engine (OPA in full,
   the same rules in code in lite) in ONE batch call, after retrieval and before
   re-ranking. A chunk the pre-filter let through but the policy denies is a disagreement:
   it is logged as a security error, counted and dropped. If the policy engine cannot
   answer, the search fails closed (PolicyUnavailable).

Modes (for the retrieval ablation in eval/run_retrieval_eval.py):
    dense       dense only, no adjustments
    bm25        BM25 only, no adjustments
    hybrid_raw  dense + BM25 with RRF, no adjustments
    hybrid      dense + BM25 with RRF, superseded weight and recency boost (the system)
"""

import logging
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime

from qdrant_client import QdrantClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db.models import Chunk, Document
from app.ports import AccessScope, KeywordIndex, PolicyEngine, VectorIndex
from app.retrieval.acl import UserContext, allowed_chunk_ids
from app.retrieval.dense import BgeEmbedder, Embedder
from app.retrieval.fusion import reciprocal_rank_fusion
from app.retrieval.recency import multipliers

log = logging.getLogger(__name__)
MODES = ("dense", "bm25", "hybrid_raw", "hybrid")
# Each retriever's weight in the fusion. Plain RRF weighs every ranked list equally (1.0);
# M15a passes weights that depend on the question type (app/agents/plan.py). The weights
# actually used are recorded with every fused result.
RETRIEVER_WEIGHTS = {"dense": 1.0, "keyword": 1.0}


@dataclass
class RetrievedChunk:
    """One search result with what the later agents and the UI need."""

    chunk_id: str
    doc_id: str
    title: str
    source_type: str
    section: str | None
    section_title: str | None
    page_start: int | None
    page_end: int | None
    effective_date: str | None
    superseded: bool
    text: str
    score: float  # final score after adjustments
    rrf_score: float
    dense_rank: int | None
    bm25_rank: int | None
    # Every multiplier applied to rrf_score, by name (score = rrf_score x product).
    multipliers: dict[str, float] = field(default_factory=dict)
    # M13: "restricted" text is read only by the private model (app/agents/model_router.py).
    classification: str = "public_internal"
    access_level: str = "staff"  # M15a: degradation may skip checking only staff-level text


@dataclass(frozen=True)
class _Meta:
    doc_id: str
    title: str
    source_type: str
    access_level: str
    engagement_id: str | None
    effective_date: str | None
    superseded: bool
    section: str | None
    section_title: str | None
    page_start: int | None
    page_end: int | None
    text: str
    classification: str = "public_internal"
    updated_at: datetime | None = None  # when the document's source last changed (M15a)


def _record_ranked(record: list[dict] | None, query: str, method: str,
                   hits: list[tuple[str, float]], source_type: str | None = None) -> None:
    """One retriever's raw output for one sub-query: chunk, raw score, rank (and, for a
    bandit arm, the one source type it searched; M15b)."""
    if record is not None:
        step = {"sub_query": query, "method": method, "scores": [
            {"chunk_id": cid, "score": round(score, 6), "rank": rank}
            for rank, (cid, score) in enumerate(hits, 1)]}
        if source_type:
            step["source_type"] = source_type
        record.append(step)


class HybridSearcher:
    """ACL-filtered hybrid retrieval over the shared indexes."""

    def __init__(self, *, session_factory: sessionmaker[Session], store: VectorIndex,
                 embedder: Embedder, bm25: KeywordIndex, rrf_k: int = 60, top_k: int = 20,
                 superseded_weight: float = 0.5, recency_weight: float = 0.2,
                 half_life_days: float = 365.0, policy: PolicyEngine | None = None,
                 freshness_weight: float = 0.0, freshness_window_days: float = 30.0) -> None:
        from app.adapters.lite.policy import InCodePolicy

        self.sessions, self.store, self.embedder, self.bm25 = session_factory, store, embedder, bm25
        self.policy: PolicyEngine = policy or InCodePolicy()
        self.policy_disagreements = 0  # pre-filter allowed, policy denied (should stay 0)
        self.rrf_k, self.top_k = rrf_k, top_k
        self.superseded_weight, self.recency_weight = superseded_weight, recency_weight
        self.half_life_days = half_life_days
        self.freshness_weight, self.freshness_window_days = freshness_weight, freshness_window_days

    def search(
        self,
        queries: str | Sequence[str],
        user: UserContext,
        *,
        mode: str = "hybrid",
        source_types: list[str] | None = None,
        time_sensitive: bool = False,
        limit: int = 20,
        as_of: date | None = None,
        record: list[dict] | None = None,
        top_k: int | None = None,
        plan: dict | None = None,
        weights: dict[str, float] | None = None,
        allocation: dict[str, int] | None = None,
    ) -> list[RetrievedChunk]:
        """Search for one query or several sub-queries on behalf of `user`.

        With `record`, appends what each step did (for the `retrievals` table): per
        sub-query and method the raw scores and ranks, then the fused list with every
        weight and multiplier applied. `top_k` overrides the candidates per retriever;
        `plan` (the question's retrieval plan) is recorded with the fused list (M14a).
        `weights` gives each retriever's weight in the fusion (M15a; default 1.0 each).
        `allocation` ("<source_type>|<method>" -> candidates; M15b, the routing bandit)
        replaces one search per retriever by one search per arm: that retriever over that
        one source type, with that many candidates; each arm has its own access scope."""
        weights = {**RETRIEVER_WEIGHTS, **(weights or {})}
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        queries = [queries] if isinstance(queries, str) else list(queries)
        as_of = as_of or date.today()

        # 1. ACL first: both retrievers only ever see permitted chunks.
        arm_scopes: dict[str, AccessScope] = {}
        with self.sessions() as s:
            if allocation:
                allowed = set()
                for st in {arm.split("|", 1)[0] for arm in allocation}:
                    ids = allowed_chunk_ids(s, user, [st])
                    arm_scopes[st] = AccessScope(user, (st,), frozenset(ids))
                    allowed |= ids
            else:
                allowed = allowed_chunk_ids(s, user, source_types)
        scope = AccessScope(user, tuple(source_types) if source_types else None,
                            frozenset(allowed))

        # 2. Ranked lists per sub-query; dense and keyword search run at the same time.
        k = top_k or self.top_k
        rankings: list[list[str]] = []
        ranking_weights: list[float] = []
        dense_rank: dict[str, int] = {}
        bm25_rank: dict[str, int] = {}
        use_dense, use_keyword = mode in ("dense", "hybrid_raw", "hybrid"), mode in ("bm25", "hybrid_raw", "hybrid")
        ranks_of = {"dense": dense_rank, "keyword": bm25_rank}
        with ThreadPoolExecutor(max_workers=2) as pool:
            for q in queries:
                # (method, source type or None, scope, candidates) for every search to run.
                searches = ([(arm.split("|", 1)[1], arm.split("|", 1)[0], arm_scopes[arm.split("|", 1)[0]], n)
                             for arm, n in allocation.items() if n > 0] if allocation else
                            [(m, None, scope, k) for m, on in (("dense", use_dense), ("keyword", use_keyword)) if on])
                vector = self.embedder.embed_query(q) if any(m == "dense" for m, *_ in searches) else None
                jobs = [(method, st, pool.submit(self.store.search_scoped, vector, sc, n) if method == "dense"
                         else pool.submit(self.bm25.search_scoped, q, sc, n))
                        for method, st, sc, n in searches]
                for method, st, job in jobs:
                    hits = job.result()
                    ids = [cid for cid, _ in hits]
                    rankings.append(ids)
                    ranking_weights.append(weights[method])
                    for r, cid in enumerate(ids, 1):
                        ranks_of[method][cid] = min(r, ranks_of[method].get(cid, r))
                    _record_ranked(record, q, method, hits, st)

        # 3. Fuse.
        fused = reciprocal_rank_fusion(rankings, k=self.rrf_k, weights=ranking_weights)
        meta = self._metadata([cid for cid, _ in fused])

        # 4. Policy: one batch decision for every candidate (raises PolicyUnavailable).
        candidates = []
        for cid, rrf_score in fused:
            m = meta.get(cid)
            if m is None:  # index drift: vector without a SQLite row
                log.warning("Chunk %s is in an index but not in SQLite; skipped", cid)
                continue
            if cid not in allowed:  # an index returned a chunk its filter should exclude
                log.error("ACL violation blocked: %s returned for %s", cid, user.user_id)
                continue
            candidates.append((cid, rrf_score, m))
        decisions = self.policy.allowed(user, [
            {"doc_id": m.doc_id, "access_level": m.access_level, "engagement_id": m.engagement_id,
             "source_type": m.source_type, "classification": m.classification}
            for _, _, m in candidates])

        # 5. Adjust the permitted candidates.
        results: list[RetrievedChunk] = []
        for (cid, rrf_score, m), permitted in zip(candidates, decisions, strict=True):
            if not permitted:
                self.policy_disagreements += 1
                log.error("SECURITY: pre-filter and %s policy disagree on %s for %s; dropped",
                          self.policy.name, cid, user.user_id)
                continue
            applied: dict[str, float] = {}
            if mode == "hybrid":
                applied = multipliers(superseded=m.superseded, effective_date=m.effective_date,
                                      time_sensitive=time_sensitive, as_of=as_of,
                                      superseded_weight=self.superseded_weight,
                                      recency_weight=self.recency_weight,
                                      half_life_days=self.half_life_days,
                                      updated_at=m.updated_at,
                                      freshness_weight=self.freshness_weight,
                                      freshness_window_days=self.freshness_window_days)
            factor = 1.0
            for value in applied.values():
                factor *= value
            results.append(RetrievedChunk(
                chunk_id=cid, doc_id=m.doc_id, title=m.title, source_type=m.source_type,
                section=m.section, section_title=m.section_title, page_start=m.page_start,
                page_end=m.page_end, effective_date=m.effective_date, superseded=m.superseded,
                text=m.text, score=rrf_score * factor, rrf_score=rrf_score,
                dense_rank=dense_rank.get(cid), bm25_rank=bm25_rank.get(cid),
                multipliers=applied, classification=m.classification,
                access_level=m.access_level))
        results.sort(key=lambda r: (-r.score, r.chunk_id))
        results = results[:limit]
        if record is not None:
            record.append({"sub_query": " || ".join(queries), "method": "fused", "scores": [
                {"chunk_id": r.chunk_id, "rank": rank, "rrf_score": round(r.rrf_score, 6),
                 "weights": dict(weights),
                 "multipliers": {name: round(v, 6) for name, v in r.multipliers.items()},
                 "final_score": round(r.score, 6), **({"plan": plan} if plan else {})}
                for rank, r in enumerate(results, 1)]})
        return results

    def fetch(self, chunk_ids: list[str], user: UserContext) -> list[RetrievedChunk]:
        """These chunks (in this order), with the same access checks as a search: the
        pre-filter rule, then the policy engine. Chunks the user may not see are left
        out silently. Used by small-to-big expansion (app/retrieval/parent_expand.py)."""
        if not chunk_ids:
            return []
        with self.sessions() as s:
            allowed = allowed_chunk_ids(s, user)
        meta = self._metadata([c for c in chunk_ids if c in allowed])
        candidates = [(cid, meta[cid]) for cid in chunk_ids if cid in meta]
        decisions = self.policy.allowed(user, [
            {"doc_id": m.doc_id, "access_level": m.access_level, "engagement_id": m.engagement_id,
             "source_type": m.source_type, "classification": m.classification}
            for _, m in candidates])
        return [RetrievedChunk(
            chunk_id=cid, doc_id=m.doc_id, title=m.title, source_type=m.source_type,
            section=m.section, section_title=m.section_title, page_start=m.page_start,
            page_end=m.page_end, effective_date=m.effective_date, superseded=m.superseded,
            text=m.text, score=0.0, rrf_score=0.0, dense_rank=None, bm25_rank=None,
            classification=m.classification, access_level=m.access_level)
            for (cid, m), ok in zip(candidates, decisions, strict=True) if ok]

    def _metadata(self, chunk_ids: list[str]) -> dict[str, _Meta]:
        if not chunk_ids:
            return {}
        with self.sessions() as s:
            superseded = {d for d in s.scalars(select(Document.supersedes)) if d}
            rows = s.execute(
                select(Chunk, Document).join(Document, Document.doc_id == Chunk.doc_id)
                .where(Chunk.chunk_id.in_(chunk_ids))).all()
        return {
            c.chunk_id: _Meta(
                doc_id=d.doc_id, title=d.title, source_type=d.source_type,
                access_level=d.access_level, engagement_id=d.engagement_id,
                effective_date=d.effective_date, superseded=d.doc_id in superseded,
                section=c.section, section_title=c.section_title, page_start=c.page_start,
                page_end=c.page_end, text=c.text,
                classification=d.classification or "public_internal", updated_at=d.updated_at)
            for c, d in rows
        }


def build_searcher(settings: Settings, embedder: Embedder | None = None,
                   qdrant_client: QdrantClient | None = None) -> HybridSearcher:
    """Searcher over the real indexes of the active profile (lite: SQLite file,
    data/qdrant, in-memory BM25; full: PostgreSQL, Qdrant server, OpenSearch)."""
    from app.adapters import (
        open_keyword_index,
        open_metadata_store,
        open_policy_engine,
        open_vector_index,
    )

    metadata = open_metadata_store(settings)
    metadata.initialise()  # idempotent: creates or upgrades the schema
    embedder = embedder or BgeEmbedder(settings.embedding_model, settings.embed_batch_size)
    keyword = open_keyword_index(settings)
    keyword.refresh(metadata.sessions)  # lite: load BM25 from the metadata store
    return HybridSearcher(
        session_factory=metadata.sessions,
        store=open_vector_index(settings, embedder.dim, embedded_client=qdrant_client),
        embedder=embedder, bm25=keyword, rrf_k=settings.rrf_k, top_k=settings.retrieval_top_k,
        superseded_weight=settings.superseded_weight, recency_weight=settings.recency_weight,
        half_life_days=settings.recency_half_life_days, policy=open_policy_engine(settings),
        freshness_weight=settings.source_freshness_weight,
        freshness_window_days=settings.source_freshness_window_days)
