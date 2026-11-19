"""Copy the existing lite index into the full-profile servers, without re-embedding.

    .venv\\Scripts\\python.exe -m scripts.copy_lite_to_full

Reads   data/fabric.db (SQLite) and data/qdrant (embedded Qdrant): the index built so far.
Writes  PostgreSQL (DATABASE_URL), the Qdrant server (QDRANT_URL) and OpenSearch
        (OPENSEARCH_URL), whatever PROFILE says.

- Every metadata row is copied as is (documents, chunks, users, queries, traces, audit log,
  ...), table by table in foreign-key order, keeping ids.
- Vectors are copied point by point with their payloads (same ids), so the 15,705 chunks
  need no new embeddings.
- OpenSearch is filled from the copied chunk text and document metadata.
- The targets must be EMPTY: the audit log is append-only, so a half-copied database cannot
  be cleaned up row by row. To start again, erase the full profile's volumes
  (`docker compose ... down -v`), which is a deliberate, visible step.
- At the end the counts are compared and one audit_log row records the copy.

Stop the API first: embedded Qdrant allows one program at a time.
"""

import argparse
from dataclasses import dataclass

from qdrant_client import models
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Base, Chunk, Document
from app.db.session import write_audit_log
from app.ports import KeywordIndex
from app.retrieval.dense import QdrantStore, open_qdrant

BATCH = 500


class TargetNotEmpty(SystemExit):
    """Refuse to copy into a store that already holds data."""


@dataclass
class CopyReport:
    rows: dict[str, int]
    vectors: int
    keyword_chunks: int


def _check_empty(target_sessions: sessionmaker[Session], target_store: QdrantStore,
                 keyword) -> None:
    with target_sessions() as s:
        documents = s.scalar(select(func.count()).select_from(Document))
    problems = []
    if documents:
        problems.append(f"PostgreSQL already has {documents} documents")
    if target_store.count():
        problems.append(f"the Qdrant collection already has {target_store.count()} points")
    if keyword.count():
        problems.append(f"the OpenSearch index already has {keyword.count()} chunks")
    if problems:
        raise TargetNotEmpty("Refusing to copy: " + "; ".join(problems) + ". Erase the full "
                             "profile's data first (docker compose ... down -v).")


def _copy_rows(source: sessionmaker[Session], target: sessionmaker[Session]) -> dict[str, int]:
    counts = {}
    with source() as src, target() as dst:
        for table in Base.metadata.sorted_tables:  # parents before children
            rows = [dict(r) for r in src.execute(select(table)).mappings()]
            for i in range(0, len(rows), BATCH):
                dst.execute(table.insert(), rows[i:i + BATCH])
            counts[table.name] = len(rows)
        if dst.bind.dialect.name == "postgresql":
            # Ids were copied explicitly: move each id sequence past the copied rows.
            for table in Base.metadata.sorted_tables:
                if "id" in table.c and table.c.id.autoincrement is not False \
                        and str(table.c.id.type) == "INTEGER":
                    dst.execute(text(
                        f"SELECT setval(pg_get_serial_sequence('{table.name}', 'id'), "
                        f"COALESCE(MAX(id), 1), MAX(id) IS NOT NULL) FROM {table.name}"))
        dst.commit()
    return counts


def _copy_vectors(source_store: QdrantStore, target_store: QdrantStore) -> int:
    copied, offset = 0, None
    while True:
        points, offset = source_store.client.scroll(
            source_store.collection, limit=256, offset=offset, with_vectors=True,
            with_payload=True)
        if points:
            target_store.client.upsert(target_store.collection, wait=True, points=[
                models.PointStruct(id=p.id, vector=p.vector, payload=p.payload) for p in points])
            copied += len(points)
        if offset is None:
            return copied


def _fill_keyword_index(target_sessions: sessionmaker[Session], keyword: KeywordIndex) -> int:
    with target_sessions() as s:
        superseded = {d for d in s.scalars(select(Document.supersedes)) if d}
        rows = s.execute(select(Chunk, Document).join(Document, Document.doc_id == Chunk.doc_id)).all()
    chunks = [{"chunk_id": c.chunk_id, "doc_id": d.doc_id, "source_type": d.source_type,
               "access_level": d.access_level, "engagement_id": d.engagement_id or "",
               "effective_date": d.effective_date or "", "superseded": d.doc_id in superseded,
               "text": c.text} for c, d in rows]
    for i in range(0, len(chunks), BATCH):
        keyword.upsert_chunks(chunks[i:i + BATCH])
    keyword.refresh(target_sessions)
    return len(chunks)


def copy_lite_to_full(source_sessions: sessionmaker[Session], source_store: QdrantStore,
                      target_sessions: sessionmaker[Session], target_store: QdrantStore,
                      keyword, actor: str = "copy_lite_to_full") -> CopyReport:
    """Copy everything; the targets must be empty. Returns what was copied."""
    _check_empty(target_sessions, target_store, keyword)
    rows = _copy_rows(source_sessions, target_sessions)
    vectors = _copy_vectors(source_store, target_store)
    keyword_chunks = _fill_keyword_index(target_sessions, keyword)
    if not (rows["chunks"] == vectors == keyword_chunks == target_store.count() == keyword.count()):
        raise RuntimeError(f"Counts differ after the copy: {rows['chunks']} chunks, "
                           f"{vectors} vectors, {keyword_chunks} keyword entries")
    with target_sessions() as s:
        write_audit_log(s, actor, "copied_from_lite",
                        f"{rows['documents']} documents, {rows['chunks']} chunks")
    return CopyReport(rows, vectors, keyword_chunks)


def main() -> int:
    from app.adapters.full.keyword import OpenSearchKeywordIndex
    from app.adapters.full.metadata import PostgresMetadataStore
    from app.adapters.full.vector import open_server_vector_index
    from app.adapters.lite.metadata import SqliteMetadataStore
    from app.adapters.lite.vector import open_embedded_vector_index
    from app.config import get_settings

    argparse.ArgumentParser(description=__doc__.splitlines()[0]).parse_args()
    settings = get_settings()
    for name in ("database_url", "qdrant_url", "opensearch_url"):
        if not getattr(settings, name):
            raise SystemExit(f"Set {name.upper()} in .env first (see .env.example).")

    source = SqliteMetadataStore(settings.sqlite_url)
    source.initialise()
    client = open_qdrant(settings.qdrant_path)
    if not client.collection_exists(settings.qdrant_collection):
        raise SystemExit("No lite index found in data/qdrant: index the corpus first.")
    dim = client.get_collection(settings.qdrant_collection).config.params.vectors.size
    source_store = open_embedded_vector_index(settings.qdrant_path, settings.qdrant_collection,
                                              dim, client=client)

    target = PostgresMetadataStore(settings.database_url)
    target.initialise()
    target_store = open_server_vector_index(settings.qdrant_url, settings.qdrant_collection, dim)
    keyword = OpenSearchKeywordIndex(settings.opensearch_url, settings.opensearch_index)

    report = copy_lite_to_full(source.sessions, source_store, target.sessions, target_store, keyword)
    print(f"Copied {report.rows['documents']} documents, {report.rows['chunks']} chunks, "
          f"{report.vectors} vectors and {report.keyword_chunks} keyword entries "
          f"({sum(report.rows.values())} metadata rows in {len(report.rows)} tables).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
