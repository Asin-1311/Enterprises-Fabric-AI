"""Index documents into SQLite (text + metadata), Qdrant (vectors) and BM25 (in memory).

Change detection is keyed by a SHA-256 hash of the file content:
- unchanged hash, title and path, same metadata  -> nothing to do;
- unchanged content, but access metadata changed  -> update the SQLite row and the
  Qdrant payload only (no re-embedding);
- changed content (or title/path)                 -> remove the document's old chunks
  from BOTH SQLite and Qdrant, then re-chunk, re-embed and insert;
- document removed from the manifest or deleted   -> remove its chunks everywhere.

After any change, the `superseded` payload flag is recomputed for every document and the
BM25 index is rebuilt from SQLite. Every reindex, metadata update and removal writes one
row to the append-only `audit_log`.

Run a full sync from the project root:
    .venv\\Scripts\\python.exe -m app.ingest.indexer
"""

import argparse
import csv
import hashlib
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from qdrant_client import QdrantClient
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.db.models import Chunk, Document, Engagement, EngagementMember, User
from app.db.session import init_db, make_engine, make_session_factory, write_audit_log
from app.ingest.chunker import ChunkingResult, chunk_icai_chapter, chunk_markdown
from app.ingest.manifest import (
    ManifestRow,
    choose_manifest,
    load_engagement_ids,
    read_manifest,
    validate_manifest,
)
from app.ingest.md_loader import load_markdown
from app.ingest.pdf_loader import load_icai_pdf
from app.retrieval.bm25 import Bm25Index
from app.retrieval.dense import BgeEmbedder, Embedder, QdrantStore, open_qdrant

log = logging.getLogger(__name__)


class ManifestError(RuntimeError):
    """The manifest failed validation, so a full sync was refused."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__(f"manifest has {len(errors)} problem(s): {errors[:3]}")
        self.errors = errors


@dataclass
class SyncReport:
    """What a sync did, by doc_id."""

    indexed: list[str] = field(default_factory=list)
    metadata_updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)


def file_hash(path: Path) -> str:
    """SHA-256 of a file's bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _metadata(row: ManifestRow) -> dict:
    """Document fields taken from a manifest row (everything except hash/indexed_at)."""
    return dict(
        path=row.path, source_type=row.source_type, title=row.title,
        version=row.version or None, effective_date=row.effective_date,
        supersedes=row.supersedes or None, access_level=row.access_level,
        engagement_id=row.engagement_id or None, synthetic=row.is_synthetic,
    )


class Indexer:
    """Keeps SQLite, Qdrant and BM25 in step with the corpus and the manifest."""

    def __init__(
        self,
        *,
        project_root: Path,
        manifest_path: Path,
        users_path: Path,
        engagements_path: Path,
        tyk_dir: Path,
        session_factory: sessionmaker[Session],
        store: QdrantStore,
        embedder: Embedder,
        bm25: Bm25Index,
        max_tokens: int = 400,
        overlap_tokens: int = 50,
        synthetic_manifest_path: Path | None = None,
    ) -> None:
        self.project_root = project_root
        self.corpus_dir = project_root / "data" / "corpus"
        self.manifest_path, self.users_path = manifest_path, users_path
        # Used instead of manifest_path when the licensed ICAI PDFs are absent (clean clone).
        self.synthetic_manifest_path = synthetic_manifest_path
        self._fallback_warned = False
        self.engagements_path, self.tyk_dir = engagements_path, tyk_dir
        self.sessions, self.store, self.embedder, self.bm25 = (
            session_factory, store, embedder, bm25)
        self.max_tokens, self.overlap_tokens = max_tokens, overlap_tokens
        # The watcher calls us from its own thread; one change is processed at a time.
        self.lock = threading.RLock()

    def active_manifest(self) -> tuple[Path, bool]:
        """(manifest to index from, True if it is the synthetic-only fallback)."""
        path, fallback = choose_manifest(self.manifest_path, self.synthetic_manifest_path,
                                         self.corpus_dir)
        if fallback and not self._fallback_warned:
            log.warning("ICAI study material not found in %s (it is licensed and not in git). "
                        "Indexing the synthetic documents only, from %s.",
                        self.corpus_dir / "standards", path.name)
            self._fallback_warned = True
        return path, fallback

    # ------------------------------------------------------------ reference data

    def load_reference_data(self) -> None:
        """Load users.csv and engagements.csv into SQLite (insert or update).

        Only the CSV's columns are copied, so a user's password hash (set at sign-in
        time, never in the CSV) survives a reload. The email column is optional."""
        with self.sessions() as s, self.users_path.open(newline="", encoding="utf-8") as fu, \
                self.engagements_path.open(newline="", encoding="utf-8") as fe:
            for u in csv.DictReader(fu):
                s.merge(User(user_id=u["user_id"], name=u["name"], role=u["role"],
                             synthetic=u["synthetic"] == "true",
                             email=(u.get("email") or "").strip().lower() or None))
            s.flush()
            for e in csv.DictReader(fe):
                s.merge(Engagement(engagement_id=e["engagement_id"], client_name=e["client_name"],
                                   industry=e["industry"], fy=e["fy"], partner_id=e["partner_id"]))
                s.flush()
                s.execute(delete(EngagementMember)
                          .where(EngagementMember.engagement_id == e["engagement_id"]))
                for uid in filter(None, e["member_ids"].split(";")):
                    s.add(EngagementMember(engagement_id=e["engagement_id"], user_id=uid.strip()))
            s.commit()

    # ------------------------------------------------------------------- public

    def sync(self, actor: str = "system") -> SyncReport:
        """Bring all indexes in line with the manifest. Refuses an invalid manifest."""
        with self.lock:
            manifest, fallback = self.active_manifest()
            header, rows = read_manifest(manifest)
            errors = validate_manifest(
                rows, project_root=self.project_root, corpus_dir=self.corpus_dir,
                engagement_ids=load_engagement_ids(self.engagements_path), header=header,
                skip_orphans_in=("standards",) if fallback else ())
            if errors:
                with self.sessions() as s:
                    write_audit_log(s, actor, "sync_rejected", f"{len(errors)} manifest problem(s)")
                raise ManifestError(errors)

            self.load_reference_data()
            report = SyncReport()
            with self.sessions() as s:
                for row in rows:
                    status = self._index_row(s, row, actor)
                    {"indexed": report.indexed, "metadata": report.metadata_updated,
                     "unchanged": report.unchanged}[status].append(row.doc_id)
                wanted = {r.doc_id for r in rows}
                for doc_id in s.scalars(select(Document.doc_id)).all():
                    if doc_id not in wanted:
                        self._remove(s, doc_id, actor)
                        report.removed.append(doc_id)
                self._refresh_superseded(s)
            self.rebuild_bm25()
            return report

    def index_path(self, path: Path, actor: str = "watcher") -> str | None:
        """(Re)index the document stored at `path`. Returns its doc_id, or None if the
        file has no manifest row (it is then left unindexed and a warning is logged)."""
        with self.lock:
            rel = self._relative(path)
            _, rows = read_manifest(self.active_manifest()[0])
            row = next((r for r in rows if r.path == rel), None)
            if row is None:
                log.warning("File %s is not in the manifest; not indexed.", rel)
                return None
            self.load_reference_data()
            with self.sessions() as s:
                self._index_row(s, row, actor)
                self._refresh_superseded(s)
            self.rebuild_bm25()
            return row.doc_id

    def remove_path(self, path: Path, actor: str = "watcher") -> str | None:
        """Remove the document stored at `path` from every index. Returns its doc_id."""
        with self.lock:
            rel = self._relative(path)
            with self.sessions() as s:
                doc_id = s.scalar(select(Document.doc_id).where(Document.path == rel))
                if doc_id is None:
                    return None
                self._remove(s, doc_id, actor)
                self._refresh_superseded(s)
            self.rebuild_bm25()
            return doc_id

    def rebuild_bm25(self) -> None:
        """Rebuild the in-memory BM25 index from SQLite chunk text."""
        with self.sessions() as s:
            self.bm25.rebuild_from_db(s)

    # ------------------------------------------------------------------ internal

    def _relative(self, path: Path) -> str:
        return path.resolve().relative_to(self.project_root.resolve()).as_posix()

    def _chunk(self, row: ManifestRow, file: Path) -> ChunkingResult:
        if file.suffix.lower() == ".pdf":
            chapter = load_icai_pdf(file, row.title)
            return chunk_icai_chapter(
                doc_id=row.doc_id, doc_title=row.title, chapter=chapter.chapter,
                pages=[(p.page_number, p.text) for p in chapter.pages],
                count_tokens=self.embedder.count_tokens,
                max_tokens=self.max_tokens, overlap_tokens=self.overlap_tokens)
        md = load_markdown(file)
        return chunk_markdown(
            doc_id=row.doc_id, doc_title=row.title, source_type=row.source_type,
            sections=md.sections, count_tokens=self.embedder.count_tokens,
            max_tokens=self.max_tokens, overlap_tokens=self.overlap_tokens)

    def _payload_fields(self, row: ManifestRow) -> dict:
        return dict(doc_id=row.doc_id, source_type=row.source_type,
                    access_level=row.access_level, engagement_id=row.engagement_id,
                    effective_date=row.effective_date)

    def _index_row(self, s: Session, row: ManifestRow, actor: str) -> str:
        """Index one manifest row. Returns 'unchanged', 'metadata' or 'indexed'."""
        file = self.project_root / row.path
        content_hash = file_hash(file)
        meta = _metadata(row)
        doc = s.get(Document, row.doc_id)

        if doc is not None and doc.content_hash == content_hash \
                and doc.title == row.title and doc.path == row.path:
            if all(getattr(doc, k) == v for k, v in meta.items()):
                return "unchanged"
            for k, v in meta.items():
                setattr(doc, k, v)
            self.store.set_document_payload(row.doc_id, self._payload_fields(row))
            write_audit_log(s, actor, "metadata_update", row.doc_id)
            return "metadata"

        result = self._chunk(row, file)
        try:
            # Remove the old version from BOTH stores before inserting the new one.
            s.execute(delete(Chunk).where(Chunk.doc_id == row.doc_id))
            self.store.delete_document(row.doc_id)

            s.merge(Document(doc_id=row.doc_id, content_hash=content_hash,
                             indexed_at=datetime.now(timezone.utc), **meta))
            s.flush()
            for c in result.chunks:
                s.add(Chunk(chunk_id=c.chunk_id, doc_id=c.doc_id, parent_id=c.parent_id,
                            text=c.text, token_count=c.token_count, section=c.section,
                            section_title=c.section_title, chapter=c.chapter,
                            page_start=c.page_start, page_end=c.page_end,
                            sa_refs=",".join(c.sa_refs)))
            vectors = self.embedder.embed_documents([c.text for c in result.chunks])
            base = self._payload_fields(row) | {"superseded": False}  # fixed by _refresh
            self.store.upsert([base | {"chunk_id": c.chunk_id} for c in result.chunks], vectors)
            write_audit_log(s, actor, "reindex", f"{row.doc_id} ({len(result.chunks)} chunks)")
        except Exception:
            s.rollback()
            # Qdrant may already have lost the old points: clear the stored hash so the
            # next sync re-indexes this document instead of treating it as unchanged.
            old = s.get(Document, row.doc_id)
            if old is not None:
                old.content_hash = None
                s.commit()
            raise
        self._write_tyk(row.doc_id, result.excluded_test_your_knowledge)
        log.info("indexed %s (%d chunks)", row.doc_id, len(result.chunks))
        return "indexed"

    def _remove(self, s: Session, doc_id: str, actor: str) -> None:
        s.execute(delete(Chunk).where(Chunk.doc_id == doc_id))
        s.execute(delete(Document).where(Document.doc_id == doc_id))
        self.store.delete_document(doc_id)
        write_audit_log(s, actor, "remove", doc_id)
        (self.tyk_dir / f"{doc_id}.txt").unlink(missing_ok=True)

    def _refresh_superseded(self, s: Session) -> None:
        """Set the `superseded` payload flag on every document from SQLite links."""
        docs = s.execute(select(Document.doc_id, Document.supersedes)).all()
        ids = {d for d, _ in docs}
        superseded = {old for _, old in docs if old and old in ids}
        for doc_id in ids:
            self.store.set_document_payload(doc_id, {"superseded": doc_id in superseded})

    def _write_tyk(self, doc_id: str, text: str) -> None:
        if text:
            self.tyk_dir.mkdir(parents=True, exist_ok=True)
            (self.tyk_dir / f"{doc_id}.txt").write_text(text, encoding="utf-8")


# ------------------------------------------------------------------- construction


def build_indexer(settings: Settings, embedder: Embedder | None = None,
                  qdrant_client: QdrantClient | None = None,
                  bm25: Bm25Index | None = None) -> Indexer:
    """Create an Indexer wired to the real SQLite file, local Qdrant and BGE model.

    Pass the searcher's embedder, Qdrant client and BM25 index to share them (the API does
    this, so a re-index is immediately visible to searches in the same process)."""
    from app.config import PROJECT_ROOT

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    engine = make_engine(settings.sqlite_url)
    init_db(engine)
    embedder = embedder or BgeEmbedder(settings.embedding_model, settings.embed_batch_size)
    client = qdrant_client or open_qdrant(settings.qdrant_path)
    return Indexer(
        project_root=PROJECT_ROOT,
        manifest_path=settings.manifest_path,
        users_path=settings.users_path,
        engagements_path=settings.engagements_path,
        tyk_dir=settings.tyk_dir,
        session_factory=make_session_factory(engine),
        store=QdrantStore(client, settings.qdrant_collection, embedder.dim),
        embedder=embedder,
        bm25=bm25 if bm25 is not None else Bm25Index(),
        max_tokens=settings.chunk_max_tokens,
        overlap_tokens=settings.chunk_overlap_tokens,
        synthetic_manifest_path=settings.synthetic_manifest_path,
    )


def main(argv: list[str] | None = None) -> int:
    """CLI: full sync of the corpus into all indexes."""
    parser = argparse.ArgumentParser(description="Sync the corpus into SQLite, Qdrant and BM25.")
    parser.add_argument("--actor", default="cli", help="name recorded in audit_log")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    from app.retrieval.dense import QdrantInUseError

    try:
        indexer = build_indexer(get_settings())
    except QdrantInUseError as exc:
        print(exc)
        return 1
    try:
        report = indexer.sync(actor=args.actor)
    except ManifestError as exc:
        for e in exc.errors:
            print(f"MANIFEST ERROR: {e}")
        return 1
    with indexer.sessions() as s:
        n_chunks = s.scalar(select(func.count()).select_from(Chunk))
    print(f"indexed={len(report.indexed)} metadata_updated={len(report.metadata_updated)} "
          f"unchanged={len(report.unchanged)} removed={len(report.removed)}")
    print(f"SQLite chunks={n_chunks}  Qdrant points={indexer.store.count()}  "
          f"BM25 docs={len(indexer.bm25)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
