"""VectorIndex on embedded Qdrant (local files under data/qdrant/; Phase 1 code)."""

from pathlib import Path

from qdrant_client import QdrantClient

from app.retrieval.dense import QdrantStore, open_qdrant


def open_embedded_vector_index(path: Path, collection: str, dim: int,
                               client: QdrantClient | None = None) -> QdrantStore:
    """Embedded Qdrant allows one process at a time (file lock) and its searches are
    serialised per call; see app/retrieval/dense.py."""
    return QdrantStore(client or open_qdrant(path), collection, dim, serialise_search=True)
