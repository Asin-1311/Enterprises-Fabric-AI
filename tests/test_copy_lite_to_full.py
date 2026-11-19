"""scripts/copy_lite_to_full.py: everything is copied, nothing re-embedded, targets must be empty.

The real PostgreSQL / Qdrant server / OpenSearch run is in tests/integration/. Here the
target is a second SQLite database, an in-memory Qdrant and a recording keyword index, so
the copy logic is tested in the normal suite.
"""

import pytest
from qdrant_client import QdrantClient
from sqlalchemy import func, select

from app.adapters.lite.metadata import SqliteMetadataStore
from app.db.models import AuditLog, Base, Chunk
from app.retrieval.dense import QdrantStore
from scripts.copy_lite_to_full import TargetNotEmpty, copy_lite_to_full
from tests.helpers import HashingEmbedder, make_indexer
from tests.test_clean_clone import _clean_clone


class RecordingKeywordIndex:
    def __init__(self) -> None:
        self.chunks: dict[str, dict] = {}

    def upsert_chunks(self, chunks) -> None:
        self.chunks.update({c["chunk_id"]: c for c in chunks})

    def refresh(self, sessions=None) -> None:
        pass

    def count(self, doc_id=None) -> int:
        return len(self.chunks)


@pytest.fixture()
def lite_index(tmp_path):
    root = _clean_clone(tmp_path)
    embedder = HashingEmbedder()
    indexer = make_indexer(root, embedder, synthetic_fallback=True)
    indexer.sync(actor="test")
    return indexer, embedder


def test_copy_moves_every_row_vector_and_chunk_without_re_embedding(tmp_path, lite_index) -> None:
    indexer, embedder = lite_index
    calls_before = embedder.calls
    target = SqliteMetadataStore(f"sqlite:///{(tmp_path / 'target.db').as_posix()}")
    target.initialise()
    target_store = QdrantStore(QdrantClient(":memory:"), "chunks", embedder.dim)
    keyword = RecordingKeywordIndex()

    report = copy_lite_to_full(indexer.sessions, indexer.store, target.sessions, target_store, keyword)

    assert embedder.calls == calls_before  # no new embeddings
    with indexer.sessions() as src, target.sessions() as dst:
        for table in Base.metadata.sorted_tables:
            if table.name == "audit_log":
                continue  # the target gains one row recording the copy
            n = src.scalar(select(func.count()).select_from(table))
            assert dst.scalar(select(func.count()).select_from(table)) == n == report.rows[table.name]
        assert dst.scalars(select(AuditLog.action)).all()[-1] == "copied_from_lite"
        n_chunks = src.scalar(select(func.count()).select_from(Chunk))
    assert report.vectors == target_store.count() == indexer.store.count() == n_chunks
    assert keyword.count() == n_chunks
    some_doc = next(iter(keyword.chunks.values()))["doc_id"]
    assert target_store.payloads(some_doc) == indexer.store.payloads(some_doc)
    sample = next(iter(keyword.chunks.values()))
    assert {"text", "access_level", "engagement_id", "superseded"} <= set(sample)


def test_copy_refuses_a_target_that_already_holds_data(tmp_path, lite_index) -> None:
    indexer, embedder = lite_index
    target_store = QdrantStore(QdrantClient(":memory:"), "chunks", embedder.dim)
    with pytest.raises(TargetNotEmpty, match="already has"):
        # The source itself is not empty: copying into it must be refused.
        copy_lite_to_full(indexer.sessions, indexer.store, indexer.sessions, target_store,
                          RecordingKeywordIndex())
