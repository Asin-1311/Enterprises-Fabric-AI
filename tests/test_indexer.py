"""Incremental indexing tests (CLAUDE.md section 13): edits and deletes must be reflected
in BOTH indexes (Qdrant and BM25, plus the SQLite chunk store).

Uses a real in-memory local Qdrant, a real SQLite file and real BM25; only the embedding
model is replaced by a deterministic fake so the tests are fast and offline.
"""

import time
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.db.models import AuditLog, Chunk, Document
from app.ingest.indexer import Indexer, ManifestError
from app.ingest.watcher import DebouncedCorpusHandler, start_observer
from tests.helpers import HashingEmbedder, make_indexer, write_people
from tests.helpers import write_doc as _write_doc
from tests.helpers import write_manifest as _write_manifest


POLICY_V1 = {"doc_id": "POL-1", "path": "data/corpus/firm_policy/p1.md",
             "source_type": "firm_policy", "title": "File Policy", "version": "1.0",
             "effective_date": "2022-04-01", "access_level": "staff", "synthetic": "true"}
POLICY_V2 = {**POLICY_V1, "doc_id": "POL-2", "path": "data/corpus/firm_policy/p2.md",
             "version": "2.0", "effective_date": "2025-04-01", "supersedes": "POL-1"}
ENG_DOC = {"doc_id": "ENG-1", "path": "data/corpus/engagement/a/plan.md",
           "source_type": "engagement", "title": "Plan", "version": "1.0",
           "effective_date": "2026-01-01", "access_level": "staff",
           "engagement_id": "ENG-A", "synthetic": "true"}


@pytest.fixture
def env(tmp_path: Path):
    """A tiny project: two policy versions and one engagement file, fully indexed."""
    (tmp_path / "data").mkdir()
    write_people(tmp_path,
                 "user_id,name,role,synthetic\nS1,Test Staff,staff,true\nP1,Test Partner,partner,true\n",
                 "engagement_id,client_name,industry,fy,partner_id,member_ids\n"
                 "ENG-A,Alpha Fictional Ltd,manufacturing,2025-26,P1,S1\n")
    _write_doc(tmp_path, "firm_policy/p1.md", "# File Policy\n\n## 1. Assembly\nAssemble within "
               "sixty days zebrawordone.\n\n## 2. Retention\nKeep seven years.")
    _write_doc(tmp_path, "firm_policy/p2.md", "# File Policy\n\n## 1. Assembly\nAssemble within "
               "forty-five days.\n\n## 2. Retention\nKeep seven years.")
    _write_doc(tmp_path, "engagement/a/plan.md", "# Plan\n\n## 1. Risks\nInventory risk.")
    _write_manifest(tmp_path, [POLICY_V1, POLICY_V2, ENG_DOC])

    embedder = HashingEmbedder()
    indexer = make_indexer(tmp_path, embedder)
    indexer.sync(actor="test")
    return tmp_path, indexer, embedder


def _sqlite_ids(indexer: Indexer, doc_id: str) -> set[str]:
    with indexer.sessions() as s:
        return set(s.scalars(select(Chunk.chunk_id).where(Chunk.doc_id == doc_id)))


def _bm25_ids(indexer: Indexer, doc_id: str) -> set[str]:
    return {cid for cid in indexer.bm25.chunk_ids if cid.startswith(f"{doc_id}:")}


def _audit(indexer: Indexer) -> list[tuple[str, str]]:
    with indexer.sessions() as s:
        return [(a.action, a.target or "") for a in s.scalars(select(AuditLog).order_by(AuditLog.id))]


def assert_in_step(indexer: Indexer, doc_id: str) -> set[str]:
    """SQLite, Qdrant and BM25 hold exactly the same chunk ids for the document."""
    ids = _sqlite_ids(indexer, doc_id)
    assert indexer.store.chunk_ids(doc_id) == ids
    assert _bm25_ids(indexer, doc_id) == ids
    return ids


# --------------------------------------------------------------------- full sync

def test_full_sync_populates_all_three_stores(env) -> None:
    _, indexer, _ = env
    for doc_id in ("POL-1", "POL-2", "ENG-1"):
        assert assert_in_step(indexer, doc_id)
    reindexed = [t for a, t in _audit(indexer) if a == "reindex"]
    assert len(reindexed) == 3


def test_payload_carries_acl_fields(env) -> None:
    _, indexer, _ = env
    payload = indexer.store.payloads("ENG-1")[0]
    assert payload["engagement_id"] == "ENG-A"
    assert payload["access_level"] == "staff"
    assert payload["source_type"] == "engagement"
    assert payload["effective_date"] == "2026-01-01"


def test_resync_without_changes_does_nothing(env) -> None:
    _, indexer, embedder = env
    calls, audit_before = embedder.calls, len(_audit(indexer))
    report = indexer.sync(actor="test")
    assert sorted(report.unchanged) == ["ENG-1", "POL-1", "POL-2"]
    assert embedder.calls == calls and len(_audit(indexer)) == audit_before


# ------------------------------------------------------------- edit and delete

def test_edit_is_reflected_in_both_indexes(env) -> None:
    root, indexer, _ = env
    assert indexer.bm25.search("zebrawordone")  # old text is searchable
    assert len(assert_in_step(indexer, "POL-1")) == 2  # sections 1 and 2
    path = _write_doc(root, "firm_policy/p1.md",
                      "# File Policy\n\n## 1. Assembly\nAssemble within ninety days quokkaword.")
    assert indexer.index_path(path, actor="test") == "POL-1"

    ids = assert_in_step(indexer, "POL-1")
    assert ids == {"POL-1:1:1"}  # section 2 was removed: its old chunk must be gone
    assert indexer.bm25.search("zebrawordone") == []          # old text gone from BM25
    hits = [cid for cid, _ in indexer.bm25.search("quokkaword")]
    assert hits and all(h.startswith("POL-1:") for h in hits)  # new text found
    with indexer.sessions() as s:
        texts = " ".join(s.scalars(select(Chunk.text).where(Chunk.doc_id == "POL-1")))
    assert "quokkaword" in texts and "zebrawordone" not in texts
    assert indexer.store.count("POL-1") == len(ids)  # no stale vectors left in Qdrant
    assert _audit(indexer)[-1] == ("reindex", "POL-1 (1 chunks)")


def test_delete_is_reflected_in_both_indexes(env) -> None:
    root, indexer, _ = env
    path = root / "data" / "corpus" / "firm_policy" / "p1.md"
    path.unlink()
    assert indexer.remove_path(path, actor="test") == "POL-1"
    assert _sqlite_ids(indexer, "POL-1") == set()
    assert indexer.store.count("POL-1") == 0
    assert _bm25_ids(indexer, "POL-1") == set()
    assert indexer.bm25.search("zebrawordone") == []
    with indexer.sessions() as s:
        assert s.get(Document, "POL-1") is None
    assert _audit(indexer)[-1] == ("remove", "POL-1")
    assert_in_step(indexer, "POL-2")  # other documents untouched


def test_metadata_change_updates_payload_without_reembedding(env) -> None:
    root, indexer, embedder = env
    calls = embedder.calls
    _write_manifest(root, [POLICY_V1, {**POLICY_V2, "access_level": "manager"}, ENG_DOC])
    report = indexer.sync(actor="test")
    assert report.metadata_updated == ["POL-2"]
    assert embedder.calls == calls
    assert {p["access_level"] for p in indexer.store.payloads("POL-2")} == {"manager"}
    assert ("metadata_update", "POL-2") in _audit(indexer)


def test_document_dropped_from_manifest_is_removed(env) -> None:
    root, indexer, _ = env
    _write_manifest(root, [POLICY_V1, POLICY_V2])
    (root / "data" / "corpus" / "engagement" / "a" / "plan.md").unlink()
    report = indexer.sync(actor="test")
    assert report.removed == ["ENG-1"]
    assert indexer.store.count("ENG-1") == 0 and _sqlite_ids(indexer, "ENG-1") == set()


# ------------------------------------------------------------------ superseded

def test_superseded_flag_follows_the_links(env) -> None:
    root, indexer, _ = env
    assert {p["superseded"] for p in indexer.store.payloads("POL-1")} == {True}
    assert {p["superseded"] for p in indexer.store.payloads("POL-2")} == {False}
    path = root / "data" / "corpus" / "firm_policy" / "p2.md"
    path.unlink()
    indexer.remove_path(path, actor="test")
    assert {p["superseded"] for p in indexer.store.payloads("POL-1")} == {False}


# -------------------------------------------------------------- safety checks

def test_invalid_manifest_is_refused_and_logged(env) -> None:
    root, indexer, _ = env
    _write_manifest(root, [POLICY_V1, {**POLICY_V2, "effective_date": "not-a-date"}, ENG_DOC])
    with pytest.raises(ManifestError):
        indexer.sync(actor="test")
    assert _audit(indexer)[-1][0] == "sync_rejected"
    assert_in_step(indexer, "POL-2")  # indexes were left untouched


def test_file_not_in_manifest_is_not_indexed(env) -> None:
    root, indexer, _ = env
    path = _write_doc(root, "firm_policy/stray.md", "# Stray\n\nNot in manifest.")
    assert indexer.index_path(path, actor="test") is None
    with indexer.sessions() as s:
        assert s.scalar(select(func.count()).select_from(Document)) == 3


# --------------------------------------------------------------------- watcher

def test_watcher_handler_batches_events(env) -> None:
    root, indexer, _ = env
    handler = DebouncedCorpusHandler(indexer, root / "data" / "corpus",
                                     root / "data" / "manifest.csv", debounce_seconds=60)
    p1 = _write_doc(root, "firm_policy/p1.md",
                    "# File Policy\n\n## 1. Assembly\nWithin thirty days wombatword.")
    p2 = root / "data" / "corpus" / "firm_policy" / "p2.md"
    p2.unlink()

    class Event:  # minimal stand-in for watchdog events
        def __init__(self, kind: str, path: Path) -> None:
            self.event_type, self.src_path, self.is_directory = kind, str(path), False

    for _ in range(3):  # an editor often fires several events per save
        handler.on_any_event(Event("modified", p1))
    handler.on_any_event(Event("deleted", p2))
    handler.flush()  # what the debounce timer would call

    assert any(cid.startswith("POL-1:") for cid, _ in indexer.bm25.search("wombatword"))
    assert indexer.store.count("POL-2") == 0 and _bm25_ids(indexer, "POL-2") == set()
    reindexes = [t for a, t in _audit(indexer) if a == "reindex" and t.startswith("POL-1")]
    assert len(reindexes) == 2  # initial sync + exactly one for the three events


def test_real_observer_picks_up_a_file_edit(env) -> None:
    root, indexer, _ = env
    handler = DebouncedCorpusHandler(indexer, root / "data" / "corpus",
                                     root / "data" / "manifest.csv", debounce_seconds=0.3)
    observer = start_observer(handler)
    try:
        time.sleep(0.5)
        _write_doc(root, "engagement/a/plan.md", "# Plan\n\n## 1. Risks\nRevenue cut-off narwhalword.")
        deadline = time.time() + 15
        while time.time() < deadline and not indexer.bm25.search("narwhalword"):
            time.sleep(0.2)
    finally:
        observer.stop()
        observer.join()
    assert [cid.split(":")[0] for cid, _ in indexer.bm25.search("narwhalword")] == ["ENG-1"]
    assert_in_step(indexer, "ENG-1")
