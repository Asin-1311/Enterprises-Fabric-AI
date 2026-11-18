"""Shared helpers for indexing and retrieval tests (offline, no model download)."""

import csv
import hashlib
import math
import re
from collections.abc import Sequence
from pathlib import Path

from qdrant_client import QdrantClient

from app.db.session import init_db, make_engine, make_session_factory
from app.ingest.indexer import Indexer
from app.ingest.md_loader import SYNTHETIC_BANNER
from app.retrieval.bm25 import Bm25Index
from app.retrieval.dense import QdrantStore
from app.retrieval.hybrid import HybridSearcher

MANIFEST_HEADER = ["doc_id", "path", "source_type", "title", "version", "effective_date",
                   "supersedes", "access_level", "engagement_id", "synthetic"]


class HashingEmbedder:
    """Deterministic bag-of-words vectors: texts sharing words get similar vectors.

    Stands in for the BGE model in tests; counts embed calls."""

    dim = 64

    def __init__(self) -> None:
        self.calls = 0

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for word in re.findall(r"[a-z0-9]+", text.lower()):
            v[int(hashlib.md5(word.encode()).hexdigest(), 16) % self.dim] += 1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)

    def count_tokens(self, text: str) -> int:
        return len(text.split())


def write_doc(root: Path, rel: str, body: str) -> Path:
    """Write a synthetic Markdown document under <root>/data/corpus/<rel>."""
    path = root / "data" / "corpus" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{SYNTHETIC_BANNER}\n\n{body}\n", encoding="utf-8")
    return path


def write_manifest(root: Path, rows: list[dict]) -> None:
    with (root / "data" / "manifest.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=MANIFEST_HEADER)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in MANIFEST_HEADER})


def write_people(root: Path, users: str, engagements: str) -> None:
    (root / "data" / "users.csv").write_text(users, encoding="utf-8")
    (root / "data" / "engagements.csv").write_text(engagements, encoding="utf-8")


def make_indexer(root: Path, embedder: HashingEmbedder | None = None,
                 synthetic_fallback: bool = False) -> Indexer:
    """Indexer over <root>/data with SQLite + in-memory Qdrant + BM25.

    With `synthetic_fallback`, the indexer may fall back to data/manifest.synthetic.csv
    when the licensed ICAI PDFs are absent (as in a clean clone)."""
    data = root / "data"
    engine = make_engine(f"sqlite:///{(data / 'test.db').as_posix()}")
    init_db(engine)
    embedder = embedder or HashingEmbedder()
    return Indexer(
        project_root=root, manifest_path=data / "manifest.csv",
        users_path=data / "users.csv", engagements_path=data / "engagements.csv",
        tyk_dir=data / "tyk", session_factory=make_session_factory(engine),
        store=QdrantStore(QdrantClient(":memory:"), "chunks", embedder.dim),
        embedder=embedder, bm25=Bm25Index(), max_tokens=400, overlap_tokens=50,
        synthetic_manifest_path=(data / "manifest.synthetic.csv") if synthetic_fallback else None)


def make_searcher(indexer: Indexer, **kwargs) -> HybridSearcher:
    """Searcher sharing the indexer's stores."""
    return HybridSearcher(session_factory=indexer.sessions, store=indexer.store,
                          embedder=indexer.embedder, bm25=indexer.bm25, **kwargs)
