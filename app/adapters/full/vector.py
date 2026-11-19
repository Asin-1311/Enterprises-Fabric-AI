"""VectorIndex on a Qdrant server (full profile).

Same store code as the lite profile (app/retrieval/dense.py): the Qdrant client has one
API for embedded and server mode. A server handles simultaneous searches, so there is no
per-call search lock here.
"""

from app.retrieval.dense import QdrantStore


def open_server_vector_index(url: str, collection: str, dim: int) -> QdrantStore:
    from qdrant_client import QdrantClient

    return QdrantStore(QdrantClient(url=url), collection, dim, serialise_search=False)
