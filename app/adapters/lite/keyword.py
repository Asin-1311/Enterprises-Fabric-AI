"""KeywordIndex on rank_bm25, in memory (Phase 1 code).

The in-memory index is rebuilt from the metadata store's chunk text after each batch of
changes (`refresh`), as in Phase 1, so the per-chunk write methods have nothing to do.
Access control uses the candidate set computed in the metadata store: forbidden chunks
are never scored.
"""

from collections.abc import Sequence

from sqlalchemy.orm import Session, sessionmaker

from app.ports import AccessScope
from app.retrieval.bm25 import Bm25Index


class RankBm25Index(Bm25Index):
    """The Phase 1 BM25 index with the KeywordIndex port's methods."""

    def upsert_chunks(self, chunks: Sequence[dict]) -> None:
        """Nothing to do: `refresh` rebuilds from the metadata store."""

    def delete_document(self, doc_id: str) -> None:
        """Nothing to do: `refresh` rebuilds from the metadata store."""

    def delete_chunks(self, chunk_ids: Sequence[str]) -> None:
        """Nothing to do: `refresh` rebuilds from the metadata store."""

    def set_document_payload(self, doc_id: str, payload: dict) -> None:
        """Nothing to do: access is decided from the metadata store at search time."""

    def refresh(self, sessions: sessionmaker[Session]) -> None:
        with sessions() as s:
            self.rebuild_from_db(s)

    def search_scoped(self, query: str, scope: AccessScope,
                      limit: int) -> list[tuple[str, float]]:
        return self.search(query, limit, set(scope.allowed_chunk_ids))
