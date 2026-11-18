"""Dense retrieval side: the embedding model and the Qdrant vector store.

Qdrant runs in local embedded mode (`QdrantClient(path="data/qdrant")`), no server.
Every point carries the payload fields the ACL filter needs (CLAUDE.md section 8), so the
role and engagement checks can run INSIDE Qdrant before any scoring (M3).

Note: local mode allows only one QdrantClient per storage folder at a time (it takes a
file lock), so the watcher and the API must share one client in one process.
"""

import threading
import uuid
from collections.abc import Sequence
from typing import Protocol

from qdrant_client import QdrantClient, models

# Payload keys stored on every Qdrant point.
PAYLOAD_KEYS = ("doc_id", "chunk_id", "source_type", "access_level", "engagement_id",
                "effective_date", "superseded")


class Embedder(Protocol):
    """What the indexer needs from an embedding model (lets tests use a fake)."""

    dim: int

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...

    def count_tokens(self, text: str) -> int: ...


class BgeEmbedder:
    """BAAI/bge-small-en-v1.5 via sentence-transformers, on CPU, normalised vectors."""

    # Recommended BGE prefix for short queries searching passages (not for passages).
    QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "

    def __init__(self, model_name: str, batch_size: int = 32) -> None:
        from sentence_transformers import SentenceTransformer  # heavy import, load lazily

        self.model = SentenceTransformer(model_name, device="cpu")
        self.batch_size = batch_size
        self.dim = int(self.model.get_embedding_dimension())

    def count_tokens(self, text: str) -> int:
        """Number of model tokens in `text` (without special tokens)."""
        return len(self.model.tokenizer(text, add_special_tokens=False)["input_ids"])

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed passages (chunks)."""
        vectors = self.model.encode(list(texts), batch_size=self.batch_size,
                                    normalize_embeddings=True, show_progress_bar=False)
        return vectors.tolist()

    def embed_query(self, text: str) -> list[float]:
        """Embed a search query (with the BGE query instruction)."""
        vector = self.model.encode(self.QUERY_INSTRUCTION + text, normalize_embeddings=True)
        return vector.tolist()


class QdrantInUseError(RuntimeError):
    """The local Qdrant folder is already open in another program."""


def open_qdrant(path) -> QdrantClient:  # noqa: ANN001 - str or Path
    """Open local Qdrant, with a clear message if another program holds the folder lock
    (local mode allows one program at a time: the indexer, the watcher/API, scripts/ask.py
    or an evaluation run)."""
    try:
        return QdrantClient(path=str(path))
    except RuntimeError as exc:
        if "already accessed by another instance" not in str(exc):
            raise
        raise QdrantInUseError(
            f"The search index ({path}) is already in use by another program of this project: "
            "the indexer, the API/demo (run_demo.bat), scripts/ask.py or an evaluation run. "
            "Only one can use it at a time. Wait for it to finish, or close it, then try again."
        ) from None


def point_id(chunk_id: str) -> str:
    """Stable Qdrant point id (a UUID) derived from the chunk id."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"audit-fabric/{chunk_id}"))


def _doc_filter(doc_id: str) -> models.Filter:
    return models.Filter(must=[models.FieldCondition(key="doc_id",
                                                     match=models.MatchValue(value=doc_id))])


class QdrantStore:
    """Thin wrapper over one Qdrant collection of chunk vectors."""

    def __init__(self, client: QdrantClient, collection: str, dim: int) -> None:
        self.client, self.collection, self.dim = client, collection, dim
        # Embedded (local-mode) Qdrant is not documented as safe for simultaneous searches
        # from several threads, so each search call is serialised. It lasts milliseconds,
        # unlike the old whole-question lock. A Qdrant server (full profile) needs no lock.
        self._search_lock = threading.Lock()
        if not self.client.collection_exists(collection):
            self.client.create_collection(
                collection,
                vectors_config=models.VectorParams(size=dim, distance=models.Distance.COSINE),
            )

    def upsert(self, payloads: Sequence[dict], vectors: Sequence[Sequence[float]]) -> None:
        """Insert or replace points; each payload must contain PAYLOAD_KEYS."""
        points = [
            models.PointStruct(id=point_id(p["chunk_id"]), vector=list(v), payload=p)
            for p, v in zip(payloads, vectors, strict=True)
        ]
        if points:
            self.client.upsert(self.collection, points=points, wait=True)

    def delete_document(self, doc_id: str) -> None:
        """Remove every point of a document."""
        self.client.delete(self.collection,
                           points_selector=models.FilterSelector(filter=_doc_filter(doc_id)),
                           wait=True)

    def set_document_payload(self, doc_id: str, payload: dict) -> None:
        """Update payload fields (e.g. access_level, superseded) on all points of a document."""
        self.client.set_payload(self.collection, payload=payload,
                                points=_doc_filter(doc_id), wait=True)

    def search(self, vector: Sequence[float], flt: models.Filter,
               limit: int) -> list[tuple[str, float]]:
        """Top-`limit` (chunk_id, cosine score). `flt` is applied INSIDE Qdrant, so
        points that fail the access filter are never scored."""
        with self._search_lock:
            result = self.client.query_points(self.collection, query=list(vector),
                                              query_filter=flt, limit=limit,
                                              with_payload=["chunk_id"])
        return [(p.payload["chunk_id"], float(p.score)) for p in result.points if p.payload]

    def count(self, doc_id: str | None = None) -> int:
        """Number of points, optionally for one document."""
        flt = _doc_filter(doc_id) if doc_id else None
        return self.client.count(self.collection, count_filter=flt, exact=True).count

    def chunk_ids(self, doc_id: str) -> set[str]:
        """Chunk ids stored for a document (used by tests and checks)."""
        points, _ = self.client.scroll(self.collection, scroll_filter=_doc_filter(doc_id),
                                       limit=10_000, with_payload=True, with_vectors=False)
        return {p.payload["chunk_id"] for p in points if p.payload}

    def payloads(self, doc_id: str) -> list[dict]:
        """Payloads of a document's points."""
        points, _ = self.client.scroll(self.collection, scroll_filter=_doc_filter(doc_id),
                                       limit=10_000, with_payload=True, with_vectors=False)
        return [dict(p.payload or {}) for p in points]
