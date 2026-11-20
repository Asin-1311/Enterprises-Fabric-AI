"""Index documents into SQLite (text + metadata), Qdrant (vectors) and BM25 (in memory).

Change detection is keyed by a SHA-256 hash of the file content:
- unchanged hash, title and path, same metadata  -> nothing to do;
- unchanged content, but access metadata changed  -> update the SQLite row and the
  Qdrant payload only (no re-embedding);
- changed content (or title/path, or the source type's chunking profile, or the
  embedding model)                                -> re-chunk, then update only the
  chunks that changed (M12b, below);
- document removed from the manifest or deleted   -> remove its chunks everywhere.

Chunk-level updates (M12b; CLAUDE.md section 6; Gap Register R-7.1-b). Chunk ids are
stable (`<doc_id>:<section>:<window>`, app/ingest/chunker.py) and every chunk stores a
SHA-256 of its text. After re-chunking, each new chunk is one of:
- kept      same id, same text, same embedding model: not touched in any index;
- reused    text seen before in this document under another id (a moved section, or a
            Phase 1 id being converted): the stored vector is copied, not recomputed;
- embedded  new or changed text: embedded now;
and every old chunk whose id is gone is deleted from SQLite, Qdrant and the keyword index.
A forced re-index (`reindex_document`, `reindex_source`) embeds every chunk again.
Old Phase 1 ids ("STD-CH06:0004") survive as `legacy_chunk_id` on the chunk with the same
text, so citations in answers written before M12b still resolve.

After any change, the `superseded` payload flag is recomputed for every document and the
BM25 index is rebuilt from SQLite. Every reindex, metadata update and removal writes one
row to the append-only `audit_log`.

Two ways in (M12a):
- `sync`, `reindex_*`, `index_path`: from the manifest (administrative commands);
- `apply_events`: change events from the event bus, called only by the ingestion worker
  (app/ingest/worker.py). Both run through the same `_index_doc`, after enrichment
  (app/ingest/enrich.py), so every document gets the same owner, classification,
  timestamp and access fields, and every index gets the same payload.

Run a full sync from the project root:
    .venv\\Scripts\\python.exe -m app.ingest.indexer
"""

import argparse
import csv
import hashlib
import logging
import threading
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.db.models import Chunk, Document, Engagement, EngagementMember, Source, User
from app.db.session import write_audit_log
from app.ingest.chunker import Chunk as ChunkSpec
from app.ingest.chunker import ChunkingResult, chunk_icai_chapter, chunk_markdown, text_hash
from app.ingest.chunking_profiles import ChunkingProfile, profile_for
from app.ingest.enrich import (
    DocumentSpec,
    EnrichmentError,
    enrich,
    folder_source_id,
    manifest_metadata,
    manifest_source_id,
)
from app.ingest.index_version import VersionedLock
from app.ingest.manifest import (
    SOURCE_TYPES,
    ManifestRow,
    choose_manifest,
    load_engagement_ids,
    read_manifest,
    validate_manifest,
)
from app.ingest.md_loader import load_markdown
from app.ingest.pdf_loader import load_icai_pdf
from app.ports import KeywordIndex, VectorIndex
from app.retrieval.dense import BgeEmbedder, Embedder

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
    # Chunk-level work (M12b): kept, reused, embedded, deleted.
    chunks: Counter = field(default_factory=Counter)


def file_hash(path: Path) -> str:
    """SHA-256 of a file's bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


# Seed classification of each corpus folder's source (M11). Engagement files are client
# confidential; everything else is internal. Admins can change a source's classification;
# the per-document classification and the private-model routing come in M12/M13.
FOLDER_CLASSIFICATION = {"engagement": "confidential"}

# Phase 1 stored no embedding_ref: every chunk it indexed was embedded with this model
# (the only one it ever used, CLAUDE.md section 3).
PHASE1_EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"


def _is_phase1_id(chunk_id: str) -> bool:
    """A Phase 1 id is "<doc_id>:0004"; a stable id has a section part as well."""
    return chunk_id.count(":") == 1


@dataclass
class _ChunkPlan:
    """What happens to each chunk of one re-chunked document (see the module doc)."""

    kept: list[ChunkSpec] = field(default_factory=list)
    reused: list[tuple[ChunkSpec, str]] = field(default_factory=list)  # (new, old chunk id)
    embedded: list[ChunkSpec] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    legacy: dict[str, str | None] = field(default_factory=dict)  # new chunk id -> Phase 1 id


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
        store: VectorIndex,
        embedder: Embedder,
        bm25: KeywordIndex,
        max_tokens: int = 400,
        overlap_tokens: int = 50,
        synthetic_manifest_path: Path | None = None,
        landing_dir: Path | None = None,
        embedding_ref: str | None = None,
    ) -> None:
        self.project_root = project_root
        self.corpus_dir = project_root / "data" / "corpus"
        # Content received by push connectors (the webhook) waits here for the worker.
        self.landing_dir = landing_dir or project_root / "data" / "landing"
        self.manifest_path, self.users_path = manifest_path, users_path
        # Used instead of manifest_path when the licensed ICAI PDFs are absent (clean clone).
        self.synthetic_manifest_path = synthetic_manifest_path
        self._fallback_warned = False
        self.engagements_path, self.tyk_dir = engagements_path, tyk_dir
        self.sessions, self.store, self.embedder, self.bm25 = (
            session_factory, store, embedder, bm25)
        self.max_tokens, self.overlap_tokens = max_tokens, overlap_tokens
        # Recorded on every chunk, so a change of embedding model is detected (M12b).
        self.embedding_ref = (embedding_ref or getattr(embedder, "model_name", None)
                              or PHASE1_EMBEDDING_MODEL)
        # Running totals of chunk-level work (kept, reused, embedded, deleted).
        self.chunk_stats: Counter = Counter()
        # The watcher calls us from its own thread; one change is processed at a time.
        # Ending a write block raises the index version (M16a, the response cache).
        self.lock = threading.RLock()

    @property
    def lock(self) -> VersionedLock:
        """The write lock. Whatever lock is assigned (the API assigns its readers-writer
        lock's write side) is wrapped so that each write raises the index version."""
        return self._lock

    @lock.setter
    def lock(self, inner) -> None:
        self._lock = VersionedLock(inner, self.sessions)

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
                             email=(u.get("email") or "").strip().lower() or None,
                             is_admin=(u.get("is_admin") or "").strip() == "true"))
            s.flush()
            for e in csv.DictReader(fe):
                s.merge(Engagement(engagement_id=e["engagement_id"], client_name=e["client_name"],
                                   industry=e["industry"], fy=e["fy"], partner_id=e["partner_id"]))
                s.flush()
                s.execute(delete(EngagementMember)
                          .where(EngagementMember.engagement_id == e["engagement_id"]))
                for uid in filter(None, e["member_ids"].split(";")):
                    s.add(EngagementMember(engagement_id=e["engagement_id"], user_id=uid.strip()))
            self._ensure_folder_sources(s)
            s.commit()

    def _ensure_folder_sources(self, s: Session) -> None:
        """Register one filesystem source per corpus folder, and link documents indexed
        before sources existed (M11). Never overwrites what an admin has changed."""
        now = datetime.now(timezone.utc)
        for source_type in SOURCE_TYPES:
            sid = folder_source_id(source_type)
            if s.get(Source, sid) is None:
                s.add(Source(id=sid, type="filesystem",
                             connector_config={"path": f"data/corpus/{source_type}"},
                             classification=FOLDER_CLASSIFICATION.get(source_type, "public_internal"),
                             status="active", created_at=now))
        s.flush()
        for doc in s.scalars(select(Document).where(Document.source_id.is_(None))):
            doc.source_id, doc.uri = folder_source_id(doc.source_type), doc.uri or doc.path
            doc.updated_at = doc.updated_at or doc.indexed_at
        s.flush()
        for sid, last in s.execute(select(Document.source_id, func.max(Document.indexed_at))
                                   .group_by(Document.source_id)).all():
            source = s.get(Source, sid)
            if source is not None and source.last_event_at is None:
                source.last_event_at = last

    def _touch_source(self, s: Session, source_id: str | None) -> None:
        """A document of this source changed: record when, for freshness lag (M11)."""
        source = s.get(Source, source_id) if source_id else None
        if source is not None:
            source.last_event_at = datetime.now(timezone.utc)

    # ------------------------------------------------------------------- public

    def _valid_rows(self, actor: str) -> list[ManifestRow]:
        """The active manifest's rows; refuses (and logs) an invalid manifest."""
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
        return rows

    def reindex_document(self, doc_id: str, actor: str = "system") -> SyncReport:
        """Re-chunk and re-embed ONE document now, even if its file is unchanged (M11).
        Raises KeyError if the manifest has no such document."""
        with self.lock:
            rows = [r for r in self._valid_rows(actor) if r.doc_id == doc_id]
            if not rows:
                raise KeyError(doc_id)
            return self._force(rows, actor)

    def reindex_source(self, source_id: str, actor: str = "system") -> SyncReport:
        """Re-chunk and re-embed every document of ONE source now (M11). A registered
        source with no documents yet (connectors arrive in M12) gives an empty report."""
        with self.lock:
            rows = [r for r in self._valid_rows(actor)
                    if folder_source_id(r.source_type) == source_id]
            return self._force(rows, actor)

    def repair_document(self, doc_id: str, actor: str = "reconcile") -> None:
        """Rebuild ONE document in both indexes from its stored record and file (M12d):
        its entries are removed from the vector and keyword index, then it is re-chunked
        and re-embedded. Works for every source (manifest, connectors, webhook), because
        the stored record already holds the enriched metadata."""
        with self.lock, self.sessions() as s:
            doc = s.get(Document, doc_id)
            if doc is None:
                raise KeyError(doc_id)
            spec = DocumentSpec(
                doc_id=doc.doc_id, source_id=doc.source_id or folder_source_id(doc.source_type),
                uri=doc.uri or doc.path, path=doc.path, source_type=doc.source_type,
                title=doc.title, version=doc.version, effective_date=doc.effective_date,
                supersedes=doc.supersedes, access_level=doc.access_level,
                engagement_id=doc.engagement_id, owner=doc.owner,
                classification=doc.classification or "confidential", synthetic=doc.synthetic,
                updated_at=doc.updated_at)
            self.store.delete_document(doc_id)
            self.bm25.delete_document(doc_id)
            self._index_doc(s, spec, self._resolve(spec.uri), actor, force=True)
            self._refresh_superseded(s)
            s.commit()
        self.rebuild_bm25()

    def _force(self, rows: list[ManifestRow], actor: str) -> SyncReport:
        """Index exactly these rows from scratch (re-chunk and re-embed every chunk);
        every other document is left alone."""
        self.load_reference_data()
        report, before = SyncReport(), self.chunk_stats.copy()
        with self.sessions() as s:
            for row in rows:
                self._index_row(s, row, actor, force=True)
                report.indexed.append(row.doc_id)
            self._refresh_superseded(s)
        self.rebuild_bm25()
        report.chunks = self.chunk_stats - before
        return report

    def sync(self, actor: str = "system") -> SyncReport:
        """Bring all indexes in line with the manifest. Refuses an invalid manifest."""
        with self.lock:
            rows = self._valid_rows(actor)
            self.load_reference_data()
            report, before = SyncReport(), self.chunk_stats.copy()
            with self.sessions() as s:
                for row in rows:
                    status = self._index_row(s, row, actor)
                    {"indexed": report.indexed, "metadata": report.metadata_updated,
                     "unchanged": report.unchanged}[status].append(row.doc_id)
                # The manifest lists the corpus folders only: documents that came from
                # other sources (webhook, connectors) are never removed by a sync.
                wanted = {r.doc_id for r in rows}
                folders = set(s.scalars(select(Source.id).where(Source.type == "filesystem")))
                for doc_id, source_id in s.execute(select(Document.doc_id, Document.source_id)).all():
                    if doc_id not in wanted and (source_id is None or source_id in folders):
                        self._remove(s, doc_id, actor)
                        report.removed.append(doc_id)
                self._refresh_superseded(s)
            self.rebuild_bm25()
            report.chunks = self.chunk_stats - before
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

    def manifest_change_events(self, actor: str = "watcher") -> list:
        """Change events that bring the indexes in line with the manifest: an upsert for
        every listed document (unchanged ones are skipped by the worker), a delete for every
        folder-source document no longer listed. Refuses an invalid manifest."""
        from app.ingest.events import deletion, manifest_upsert

        with self.lock:
            rows = self._valid_rows(actor)
            self.load_reference_data()
            events = [manifest_upsert(r, file_hash(self.project_root / r.path)) for r in rows]
            wanted = {r.doc_id for r in rows}
            with self.sessions() as s:
                folders = set(s.scalars(select(Source.id).where(Source.type == "filesystem")))
                for doc_id, source_id, uri in s.execute(
                        select(Document.doc_id, Document.source_id, Document.path)).all():
                    if doc_id not in wanted and (source_id is None or source_id in folders):
                        events.append(deletion(source_id or "", doc_id, uri))
            return events

    def path_change_event(self, path: Path, deleted: bool):
        """The change event for one corpus file, or None if the file is not in the manifest
        (an upsert) or not indexed (a delete)."""
        from app.ingest.events import deletion, manifest_upsert

        rel = self._relative(path)
        if deleted:
            with self.sessions() as s:
                doc = s.scalars(select(Document).where(Document.path == rel)).first()
            return deletion(doc.source_id or folder_source_id(doc.source_type), doc.doc_id, rel) \
                if doc is not None else None
        _, rows = read_manifest(self.active_manifest()[0])
        row = next((r for r in rows if r.path == rel), None)
        if row is None:
            log.warning("File %s is not in the manifest; not indexed.", rel)
            return None
        return manifest_upsert(row, file_hash(path))

    def apply_events(self, events: list, actor: str = "worker") -> list[tuple[str, str]]:
        """Apply change events (app/ingest/events.py) to every index; called only by the
        ingestion worker. Returns (event_id, outcome) per event, where outcome is one of
        indexed | metadata | unchanged | removed | missing | paused | rejected.

        A change that fails enrichment is refused and written to audit_log; any other
        error propagates, so the worker does not commit and the events are delivered again."""
        with self.lock:
            self.load_reference_data()
            outcomes: list[tuple[str, str]] = []
            with self.sessions() as s:
                engagement_ids = set(s.scalars(select(Engagement.engagement_id)))
                user_ids = set(s.scalars(select(User.user_id)))
                for event in events:
                    try:
                        outcome = self._apply(s, event, engagement_ids, user_ids, actor)
                    except EnrichmentError as exc:
                        s.rollback()
                        log.warning("Change refused: %s", exc)
                        write_audit_log(s, actor, "event_rejected", str(exc)[:500])
                        outcome = "rejected"
                    outcomes.append((event.event_id, outcome))
                self._refresh_superseded(s)
                s.commit()
            self.rebuild_bm25()
            return outcomes

    def _apply(self, s: Session, event, engagement_ids: set[str], user_ids: set[str],
               actor: str) -> str:
        source = s.get(Source, event.source_id)
        if source is None:
            raise EnrichmentError(f"{event.doc_id}: source '{event.source_id}' is not registered")
        existing = s.get(Document, event.doc_id)
        if existing is not None and existing.source_id not in (None, event.source_id):
            raise EnrichmentError(f"{event.doc_id}: the document belongs to source "
                                  f"'{existing.source_id}', not '{event.source_id}'")
        if source.status != "active":
            log.info("Source %s is %s: change to %s skipped", source.id, source.status, event.doc_id)
            return "paused"
        if event.op == "delete":
            if existing is None:
                return "missing"
            self._remove(s, event.doc_id, actor)
            if event.uri.startswith("landing:"):
                self._resolve(event.uri, must_exist=False).unlink(missing_ok=True)
            return "removed"
        file = self._resolve(event.uri)
        # `path` is the event's uri: for a corpus file that is its relative path (as before);
        # for pushed content ("landing:...") it stays valid wherever the landing folder is.
        spec = enrich(event.metadata, doc_id=event.doc_id, source_id=event.source_id,
                      uri=event.uri, path=event.uri, source=source,
                      engagement_ids=engagement_ids, user_ids=user_ids,
                      occurred_at=event.occurred)
        return self._index_doc(s, spec, file, actor)

    def _resolve(self, uri: str, must_exist: bool = True) -> Path:
        """The file behind an event's uri, which must lie inside the corpus folder or the
        landing folder: an event can never make the indexer read any other file."""
        if uri.startswith("landing:"):
            base, file = self.landing_dir, self.landing_dir / uri.removeprefix("landing:")
        else:
            base, file = self.corpus_dir, self.project_root / uri
        file = file.resolve()
        if base.resolve() not in file.parents:
            raise EnrichmentError(f"'{uri}' is outside the corpus and landing folders")
        if must_exist and not file.is_file():
            raise EnrichmentError(f"'{uri}' does not exist")
        return file

    def rebuild_bm25(self) -> None:
        """Make the keyword index current: lite rebuilds BM25 from the metadata store's
        chunk text; OpenSearch (full) makes the writes searchable."""
        self.bm25.refresh(self.sessions)

    # ------------------------------------------------------------------ internal

    def _relative(self, path: Path) -> str:
        return path.resolve().relative_to(self.project_root.resolve()).as_posix()

    def profile(self, source_type: str) -> ChunkingProfile:
        """The chunking profile of a source type, with the configured window sizes."""
        return profile_for(source_type, self.max_tokens, self.overlap_tokens)

    def _chunk(self, row: DocumentSpec, file: Path, profile: ChunkingProfile) -> ChunkingResult:
        if file.suffix.lower() == ".pdf":
            chapter = load_icai_pdf(file, row.title)
            return chunk_icai_chapter(
                doc_id=row.doc_id, doc_title=row.title, chapter=chapter.chapter,
                pages=[(p.page_number, p.text) for p in chapter.pages],
                count_tokens=self.embedder.count_tokens, profile=profile)
        md = load_markdown(file)
        return chunk_markdown(
            doc_id=row.doc_id, doc_title=row.title, source_type=row.source_type,
            sections=md.sections, count_tokens=self.embedder.count_tokens, profile=profile)

    def _model_of(self, chunk: Chunk) -> str:
        return chunk.embedding_ref or PHASE1_EMBEDDING_MODEL

    def _plan(self, old: list[Chunk], new: list[ChunkSpec], force: bool) -> _ChunkPlan:
        """Decide, per chunk, what a re-chunked document needs (see the module doc).

        Each old chunk is matched to at most one new chunk with the same text: first one
        with the same id, then any. A match passes on its Phase 1 id; its vector is reused
        only if it was made by the current embedding model and this is not a forced run."""
        plan = _ChunkPlan()
        new_ids = {c.chunk_id for c in new}
        plan.deleted = [o.chunk_id for o in old if o.chunk_id not in new_ids]
        old_by_id = {o.chunk_id: o for o in old}
        hash_of = {o.chunk_id: o.content_hash or text_hash(o.text) for o in old}

        match: dict[str, Chunk] = {}
        for c in new:
            o = old_by_id.get(c.chunk_id)
            if o is not None and hash_of[o.chunk_id] == c.content_hash:
                match[c.chunk_id] = o
        taken = {o.chunk_id for o in match.values()}
        pool: dict[str, list[Chunk]] = defaultdict(list)
        for o in old:
            if o.chunk_id not in taken:
                pool[hash_of[o.chunk_id]].append(o)
        for c in new:
            if c.chunk_id not in match and pool.get(c.content_hash):
                match[c.chunk_id] = pool[c.content_hash].pop(0)

        for c in new:
            o = match.get(c.chunk_id)
            # Changed text gets no Phase 1 id: an old citation must not move to new text.
            plan.legacy[c.chunk_id] = None if o is None else (
                o.legacy_chunk_id or (o.chunk_id if _is_phase1_id(o.chunk_id) else None))
            if o is None or force or self._model_of(o) != self.embedding_ref:
                plan.embedded.append(c)
            elif o.chunk_id == c.chunk_id:
                plan.kept.append(c)
            else:
                plan.reused.append((c, o.chunk_id))
        return plan

    def _spec_for_row(self, s: Session, row: ManifestRow) -> DocumentSpec:
        """Enrich a manifest row like any other change (same defaults, same checks)."""
        source_id = manifest_source_id(row)
        source = s.get(Source, source_id)
        if source is None:
            raise ManifestError([f"{row.doc_id}: source '{source_id}' is not registered"])
        try:
            return enrich(manifest_metadata(row), doc_id=row.doc_id, source_id=source_id,
                          uri=row.path, path=row.path, source=source,
                          engagement_ids=set(s.scalars(select(Engagement.engagement_id))),
                          user_ids=set(s.scalars(select(User.user_id))))
        except EnrichmentError as exc:
            raise ManifestError([str(exc)]) from exc

    def _index_row(self, s: Session, row: ManifestRow, actor: str, force: bool = False) -> str:
        """Index one manifest row. Returns 'unchanged', 'metadata' or 'indexed'."""
        return self._index_doc(s, self._spec_for_row(s, row), self.project_root / row.path,
                               actor, force=force)

    def _index_doc(self, s: Session, row: DocumentSpec, file: Path, actor: str,
                   force: bool = False) -> str:
        """Index one enriched document from `file`. Returns 'unchanged', 'metadata' or
        'indexed'. Idempotent: the same content and metadata again change nothing.
        `force` re-chunks and re-embeds every chunk even if nothing changed."""
        content_hash = file_hash(file)
        meta = row.document_fields()
        doc = s.get(Document, row.doc_id)
        now = datetime.now(timezone.utc)
        profile = self.profile(row.source_type)
        old = list(s.scalars(select(Chunk).where(Chunk.doc_id == row.doc_id)))
        # Chunks made by another profile or embedding model must be redone (M12b).
        current = all(c.chunking_profile == profile.ref and self._model_of(c) == self.embedding_ref
                      for c in old)

        if not force and current and doc is not None and doc.content_hash == content_hash \
                and doc.title == row.title and doc.path == row.path:
            if all(getattr(doc, k) == v for k, v in meta.items()):
                return "unchanged"
            for k, v in meta.items():
                setattr(doc, k, v)
            doc.updated_at = row.updated_at or now
            self.store.set_document_payload(row.doc_id, row.payload())
            self.bm25.set_document_payload(row.doc_id, row.payload())
            self._touch_source(s, row.source_id)
            write_audit_log(s, actor, "metadata_update", row.doc_id)
            return "metadata"

        result = self._chunk(row, file, profile)
        meta_changed = doc is None or any(getattr(doc, k) != v for k, v in meta.items())
        try:
            plan = self._plan(old, result.chunks, force)
            # Vectors first (an old point may be deleted below); a missing one is embedded.
            stored = self.store.vectors([old_id for _, old_id in plan.reused])
            for c, old_id in list(plan.reused):
                if old_id not in stored:
                    plan.reused.remove((c, old_id))
                    plan.embedded.append(c)
            written = [*plan.embedded, *(c for c, _ in plan.reused)]
            vectors = [*self.embedder.embed_documents([c.text for c in plan.embedded]),
                       *(stored[old_id] for _, old_id in plan.reused)]

            # Metadata store: drop vanished chunks, update kept ones, add the rest.
            if plan.deleted:
                s.execute(delete(Chunk).where(Chunk.chunk_id.in_(plan.deleted)))
            s.merge(Document(doc_id=row.doc_id, content_hash=content_hash,
                             indexed_at=now, updated_at=row.updated_at or now, **meta))
            self._touch_source(s, row.source_id)
            s.flush()
            for c in result.chunks:
                s.merge(Chunk(chunk_id=c.chunk_id, doc_id=c.doc_id, parent_id=c.parent_id,
                              text=c.text, token_count=c.token_count, section=c.section,
                              section_title=c.section_title, chapter=c.chapter,
                              page_start=c.page_start, page_end=c.page_end,
                              sa_refs=",".join(c.sa_refs), content_hash=c.content_hash,
                              chunking_profile=c.chunking_profile,
                              embedding_ref=self.embedding_ref,
                              legacy_chunk_id=plan.legacy.get(c.chunk_id)))

            # Both indexes, from the same plan: new points first, then the deletions.
            base = row.payload() | {"superseded": False}  # fixed by _refresh
            self.store.upsert([base | {"chunk_id": c.chunk_id} for c in written], vectors)
            self.bm25.upsert_chunks([base | {"chunk_id": c.chunk_id, "text": c.text}
                                     for c in written])
            self.store.delete_chunks(plan.deleted)
            self.bm25.delete_chunks(plan.deleted)
            if plan.kept and meta_changed:  # kept points still carry the old access fields
                self.store.set_document_payload(row.doc_id, row.payload())
                self.bm25.set_document_payload(row.doc_id, row.payload())

            counts = {"kept": len(plan.kept), "reused": len(plan.reused),
                      "embedded": len(plan.embedded), "deleted": len(plan.deleted)}
            self.chunk_stats.update(counts)
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
        log.info("indexed %s (%d chunks: %s)", row.doc_id, len(result.chunks), counts)
        return "indexed"

    def _remove(self, s: Session, doc_id: str, actor: str) -> None:
        doc = s.get(Document, doc_id)
        if doc is not None:
            self._touch_source(s, doc.source_id or folder_source_id(doc.source_type))
        s.execute(delete(Chunk).where(Chunk.doc_id == doc_id))
        s.execute(delete(Document).where(Document.doc_id == doc_id))
        self.store.delete_document(doc_id)
        self.bm25.delete_document(doc_id)
        write_audit_log(s, actor, "remove", doc_id)
        (self.tyk_dir / f"{doc_id}.txt").unlink(missing_ok=True)

    def _refresh_superseded(self, s: Session) -> None:
        """Make the `superseded` payload flag match the SQLite links, changing only the
        documents whose flag is wrong (M12c). Rewriting every document after each change
        made one small batch take minutes on the embedded Qdrant, which writes every point
        it touches to disk. Both indexes are written from the same decisions, so the
        documents found wrong in Qdrant are the ones updated in the keyword index too."""
        docs = s.execute(select(Document.doc_id, Document.supersedes)).all()
        ids = {d for d, _ in docs}
        superseded = {old for _, old in docs if old and old in ids}
        # A document is wrong if any of its points disagrees (new points start as False).
        wrong = ((self.store.doc_ids_with("superseded", False) & superseded)
                 | (self.store.doc_ids_with("superseded", True) - superseded)) & ids
        for doc_id in sorted(wrong):
            self.store.set_document_payload(doc_id, {"superseded": doc_id in superseded})
            self.bm25.set_document_payload(doc_id, {"superseded": doc_id in superseded})

    def _write_tyk(self, doc_id: str, text: str) -> None:
        if text:
            self.tyk_dir.mkdir(parents=True, exist_ok=True)
            (self.tyk_dir / f"{doc_id}.txt").write_text(text, encoding="utf-8")


# ------------------------------------------------------------------- construction


def build_indexer(settings: Settings, embedder: Embedder | None = None,
                  store: VectorIndex | None = None, bm25: KeywordIndex | None = None,
                  sessions: sessionmaker[Session] | None = None) -> Indexer:
    """Create an Indexer on the active profile's backends (lite: SQLite file, embedded
    Qdrant, in-memory BM25; full: PostgreSQL, Qdrant server, OpenSearch).

    Pass the searcher's embedder, indexes and session factory to share them (the API does
    this, so a re-index is immediately visible to searches in the same process)."""
    from app.adapters import open_keyword_index, open_metadata_store, open_vector_index
    from app.config import PROJECT_ROOT

    if sessions is None:
        metadata = open_metadata_store(settings)
        metadata.initialise()
        sessions = metadata.sessions
    embedder = embedder or BgeEmbedder(settings.embedding_model, settings.embed_batch_size)
    return Indexer(
        project_root=PROJECT_ROOT,
        manifest_path=settings.manifest_path,
        users_path=settings.users_path,
        engagements_path=settings.engagements_path,
        tyk_dir=settings.tyk_dir,
        session_factory=sessions,
        store=store if store is not None else open_vector_index(settings, embedder.dim),
        embedder=embedder,
        bm25=bm25 if bm25 is not None else open_keyword_index(settings),
        max_tokens=settings.chunk_max_tokens,
        overlap_tokens=settings.chunk_overlap_tokens,
        synthetic_manifest_path=settings.synthetic_manifest_path,
        landing_dir=settings.landing_dir,
        embedding_ref=settings.embedding_model,
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
    print("chunks: " + ", ".join(f"{k}={report.chunks.get(k, 0)}"
                                 for k in ("kept", "reused", "embedded", "deleted")))
    print(f"SQLite chunks={n_chunks}  Qdrant points={indexer.store.count()}  "
          f"BM25 docs={len(indexer.bm25)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
