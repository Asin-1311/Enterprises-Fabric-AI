"""Hybrid search: dense (Qdrant) + BM25, fused with RRF, then adjusted.

Order of operations for one request (CLAUDE.md section 7.3):
1. ACL first: build the Qdrant payload filter and the BM25 candidate set for this user.
   Restricted chunks are never scored by either retriever.
2. For each sub-query: dense top-k and BM25 top-k.
3. Reciprocal rank fusion of all ranked lists (k = 60).
4. Multiply by the superseded weight (and, for time-sensitive questions, the recency
   boost).
5. Defence in depth: drop any result that fails `can_see()` (should never happen; it is
   logged as an error if it does).

Modes (for the retrieval ablation in eval/run_retrieval_eval.py):
    dense       dense only, no adjustments
    bm25        BM25 only, no adjustments
    hybrid_raw  dense + BM25 with RRF, no adjustments
    hybrid      dense + BM25 with RRF, superseded weight and recency boost (the system)
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

from qdrant_client import QdrantClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db.models import Chunk, Document
from app.db.session import init_db, make_engine, make_session_factory
from app.retrieval.acl import UserContext, allowed_chunk_ids, can_see, qdrant_filter
from app.retrieval.bm25 import Bm25Index
from app.retrieval.dense import BgeEmbedder, Embedder, QdrantStore, open_qdrant
from app.retrieval.fusion import reciprocal_rank_fusion
from app.retrieval.recency import multipliers

log = logging.getLogger(__name__)
MODES = ("dense", "bm25", "hybrid_raw", "hybrid")


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


class HybridSearcher:
    """ACL-filtered hybrid retrieval over the shared indexes."""

    def __init__(self, *, session_factory: sessionmaker[Session], store: QdrantStore,
                 embedder: Embedder, bm25: Bm25Index, rrf_k: int = 60, top_k: int = 20,
                 superseded_weight: float = 0.5, recency_weight: float = 0.2,
                 half_life_days: float = 365.0) -> None:
        self.sessions, self.store, self.embedder, self.bm25 = session_factory, store, embedder, bm25
        self.rrf_k, self.top_k = rrf_k, top_k
        self.superseded_weight, self.recency_weight = superseded_weight, recency_weight
        self.half_life_days = half_life_days

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
    ) -> list[RetrievedChunk]:
        """Search for one query or several sub-queries on behalf of `user`."""
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        queries = [queries] if isinstance(queries, str) else list(queries)
        as_of = as_of or date.today()

        # 1. ACL first: both retrievers only ever see permitted chunks.
        with self.sessions() as s:
            allowed = allowed_chunk_ids(s, user, source_types)
        flt = qdrant_filter(user, source_types)

        # 2. Ranked lists per sub-query.
        rankings: list[list[str]] = []
        dense_rank: dict[str, int] = {}
        bm25_rank: dict[str, int] = {}
        for q in queries:
            if mode in ("dense", "hybrid_raw", "hybrid"):
                ids = [cid for cid, _ in self.store.search(self.embedder.embed_query(q), flt, self.top_k)]
                rankings.append(ids)
                for r, cid in enumerate(ids, 1):
                    dense_rank[cid] = min(r, dense_rank.get(cid, r))
            if mode in ("bm25", "hybrid_raw", "hybrid"):
                ids = [cid for cid, _ in self.bm25.search(q, self.top_k, allowed)]
                rankings.append(ids)
                for r, cid in enumerate(ids, 1):
                    bm25_rank[cid] = min(r, bm25_rank.get(cid, r))

        # 3. Fuse.
        fused = reciprocal_rank_fusion(rankings, k=self.rrf_k)
        meta = self._metadata([cid for cid, _ in fused])

        # 4-5. Adjust, and re-check access on every result.
        results: list[RetrievedChunk] = []
        for cid, rrf_score in fused:
            m = meta.get(cid)
            if m is None:  # index drift: vector without a SQLite row
                log.warning("Chunk %s is in an index but not in SQLite; skipped", cid)
                continue
            if cid not in allowed or not can_see(user, m.access_level, m.engagement_id):
                log.error("ACL violation blocked: %s returned for %s", cid, user.user_id)
                continue
            applied: dict[str, float] = {}
            if mode == "hybrid":
                applied = multipliers(superseded=m.superseded, effective_date=m.effective_date,
                                      time_sensitive=time_sensitive, as_of=as_of,
                                      superseded_weight=self.superseded_weight,
                                      recency_weight=self.recency_weight,
                                      half_life_days=self.half_life_days)
            factor = 1.0
            for value in applied.values():
                factor *= value
            results.append(RetrievedChunk(
                chunk_id=cid, doc_id=m.doc_id, title=m.title, source_type=m.source_type,
                section=m.section, section_title=m.section_title, page_start=m.page_start,
                page_end=m.page_end, effective_date=m.effective_date, superseded=m.superseded,
                text=m.text, score=rrf_score * factor, rrf_score=rrf_score,
                dense_rank=dense_rank.get(cid), bm25_rank=bm25_rank.get(cid),
                multipliers=applied))
        results.sort(key=lambda r: (-r.score, r.chunk_id))
        return results[:limit]

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
                page_end=c.page_end, text=c.text)
            for c, d in rows
        }


def build_searcher(settings: Settings, embedder: Embedder | None = None,
                   qdrant_client: QdrantClient | None = None) -> HybridSearcher:
    """Searcher over the real local indexes (SQLite file, data/qdrant, BGE model)."""
    engine = make_engine(settings.sqlite_url)
    init_db(engine)  # idempotent; also adds columns introduced after the DB was created
    sessions = make_session_factory(engine)
    embedder = embedder or BgeEmbedder(settings.embedding_model, settings.embed_batch_size)
    client = qdrant_client or open_qdrant(settings.qdrant_path)
    bm25 = Bm25Index()
    with sessions() as s:
        bm25.rebuild_from_db(s)
    return HybridSearcher(
        session_factory=sessions,
        store=QdrantStore(client, settings.qdrant_collection, embedder.dim),
        embedder=embedder, bm25=bm25, rrf_k=settings.rrf_k, top_k=settings.retrieval_top_k,
        superseded_weight=settings.superseded_weight, recency_weight=settings.recency_weight,
        half_life_days=settings.recency_half_life_days)
