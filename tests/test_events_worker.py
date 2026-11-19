"""M12a: change events, enrichment and the ingestion worker (no models, no servers).

Covers: R-7.1-a (enrichment on every document and index payload), R-5-ing (change events
and the worker; the connectors follow in M12c).
"""

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.adapters.lite.events import InProcessEventBus
from app.db.models import AuditLog, Chunk, Document, Source
from app.ingest.enrich import EnrichmentError, enrich
from app.ingest.events import TOPIC, BadEvent, DocumentChanged, publish
from app.ingest.worker import IngestWorker
from app.retrieval.acl import UserContext
from tests.helpers import make_indexer, make_searcher, write_doc, write_manifest, write_people
from tests.test_agents import DOCS, ENGAGEMENTS, USERS
from tests.test_clean_clone import _clean_clone

BANNER_BODY = "# Wombat policy\n\nWombatword appears only in this document."


@pytest.fixture()
def world(tmp_path: Path):
    root = tmp_path / "proj"
    (root / "data").mkdir(parents=True)
    write_people(root, USERS, ENGAGEMENTS)
    rows = []
    for doc_id, rel, st, level, eng, eff, sup, body in DOCS:
        write_doc(root, rel, body)
        rows.append(dict(doc_id=doc_id, path=f"data/corpus/{rel}", source_type=st, title=doc_id,
                         version="1", effective_date=eff, supersedes=sup, access_level=level,
                         engagement_id=eng, synthetic="true"))
    write_manifest(root, rows)
    indexer = make_indexer(root)
    indexer.sync(actor="test")
    bus = InProcessEventBus()
    return SimpleNamespace(root=root, indexer=indexer, bus=bus, worker=IngestWorker(indexer, bus))


def _upsert(doc_id: str, rel: str, **meta) -> DocumentChanged:
    metadata = dict(source_type="firm_policy", title=doc_id, access_level="staff",
                    effective_date="2026-01-01", synthetic=True) | meta
    return DocumentChanged(source_id="fs-firm_policy", doc_id=doc_id,
                           uri=f"data/corpus/{rel}", op="upsert", content_hash="x" * 64,
                           metadata=metadata)


def _audit(indexer, action: str) -> list[str]:
    with indexer.sessions() as s:
        return [a.target for a in s.scalars(select(AuditLog).where(AuditLog.action == action))]


# --------------------------------------------------------------------------- events

def test_events_round_trip_and_bad_events_are_refused() -> None:
    event = _upsert("NEW-1", "firm_policy/new.md")
    assert DocumentChanged.from_dict(event.to_dict()) == event
    assert set(event.to_dict()) == {"event_id", "source_id", "doc_id", "uri", "op",
                                    "content_hash", "metadata", "occurred_at"}
    for broken in ({**event.to_dict(), "op": "rename"}, {**event.to_dict(), "doc_id": ""},
                   {**event.to_dict(), "content_hash": None}, {**event.to_dict(), "occurred_at": "x"},
                   "not an object"):
        with pytest.raises(BadEvent):
            DocumentChanged.from_dict(broken)


# --------------------------------------------------------------------------- enrichment

def test_enrichment_fills_owner_and_classification_from_the_source() -> None:
    """Covers: R-7.1-a. The document's own values win; otherwise the source's; otherwise
    internal. Times come from the change."""
    source = Source(id="fs-firm_policy", type="filesystem", connector_config={}, owner="P1",
                    classification="confidential", status="active")
    common = dict(doc_id="D-1", source_id="fs-firm_policy", uri="u", path="p", source=source,
                  engagement_ids={"ENG-A"}, user_ids={"P1", "S1"})
    meta = dict(source_type="firm_policy", title="T", access_level="staff", effective_date="2026-01-01")
    when = datetime(2026, 11, 19, 10, 0)
    spec = enrich(meta, occurred_at=when, **common)
    assert (spec.owner, spec.classification, spec.updated_at) == ("P1", "confidential", when)
    spec = enrich(meta | {"owner": "S1", "classification": "restricted"}, **common)
    assert (spec.owner, spec.classification) == ("S1", "restricted")
    assert enrich(meta, **{**common, "source": None}).classification == "public_internal"
    # A default is never weaker than the content needs: above staff, or an engagement file.
    internal = Source(id="fs-firm_policy", type="filesystem", connector_config={},
                      classification="public_internal", status="active")
    manager = enrich(meta | {"access_level": "manager"}, **{**common, "source": internal})
    assert manager.classification == "confidential"
    explicit = enrich(meta | {"access_level": "manager", "classification": "public_internal"},
                      **{**common, "source": internal})
    assert explicit.classification == "public_internal"  # an explicit choice is respected
    assert spec.payload() == {"doc_id": "D-1", "source_id": "fs-firm_policy",
                              "source_type": "firm_policy", "access_level": "staff",
                              "engagement_id": "", "classification": "restricted",
                              "effective_date": "2026-01-01"}


@pytest.mark.parametrize("bad", [
    {"access_level": "intern"}, {"source_type": "gossip"}, {"title": ""},
    {"effective_date": "19/11/2026"}, {"engagement_id": "ENG-ZZ"},
    {"engagement_id": "ENG-A"},  # a firm policy may not belong to an engagement
    {"source_type": "engagement"},  # an engagement document must name its engagement
    {"owner": "NOBODY"}, {"classification": "top-secret"},
])
def test_enrichment_refuses_rather_than_guesses(bad) -> None:
    meta = dict(source_type="firm_policy", title="T", access_level="staff",
                effective_date="2026-01-01") | bad
    with pytest.raises(EnrichmentError):
        enrich(meta, doc_id="D-1", source_id="fs-firm_policy", uri="u", path="p", source=None,
               engagement_ids={"ENG-A"}, user_ids={"P1"})


def test_every_document_and_index_payload_carries_the_enriched_fields(tmp_path) -> None:
    """Covers: R-7.1-a. After indexing the synthetic corpus, every document has source,
    owner, classification, uri and timestamp, and every vector carries source_id and
    classification beside its access fields."""
    indexer = make_indexer(_clean_clone(tmp_path), synthetic_fallback=True)
    indexer.sync(actor="test")
    with indexer.sessions() as s:
        docs = list(s.scalars(select(Document)))
    assert len(docs) == 40
    for d in docs:
        assert d.source_id == f"fs-{d.source_type}" and d.uri == d.path and d.updated_at
        assert d.classification in ("public_internal", "confidential") and d.owner, d.doc_id
        assert (d.classification == "confidential") == (
            d.source_type == "engagement" or d.access_level != "staff"), d.doc_id
        for payload in indexer.store.payloads(d.doc_id):
            assert payload["source_id"] == d.source_id
            assert payload["classification"] == d.classification
            assert payload["access_level"] == d.access_level


# --------------------------------------------------------------------------- the worker

def test_the_worker_indexes_a_new_document_into_every_index_once(world) -> None:
    """Covers: R-5-ing. One event updates metadata, vectors and keywords together;
    the same event again changes nothing (idempotent)."""
    write_doc(world.root, "firm_policy/wombat.md", BANNER_BODY)
    event = _upsert("WOMBAT-1", "firm_policy/wombat.md", owner="P1")
    publish(world.bus, event)
    assert world.worker.drain() == [(event.event_id, "indexed")]
    with world.indexer.sessions() as s:
        doc = s.get(Document, "WOMBAT-1")
        n_chunks = s.query(Chunk).filter(Chunk.doc_id == "WOMBAT-1").count()
    assert doc.owner == "P1" and doc.source_id == "fs-firm_policy" and n_chunks >= 1
    assert world.indexer.store.count("WOMBAT-1") == n_chunks
    staff = UserContext("S1", "staff", frozenset({"ENG-A"}))
    assert any(h.doc_id == "WOMBAT-1"
               for h in make_searcher(world.indexer).search("wombatword", staff, mode="bm25"))

    calls = world.indexer.embedder.calls
    publish(world.bus, event)  # delivered twice (at-least-once): no second embedding
    assert world.worker.drain() == [(event.event_id, "unchanged")]
    assert world.indexer.embedder.calls == calls

    gone = DocumentChanged(source_id="fs-firm_policy", doc_id="WOMBAT-1",
                           uri="data/corpus/firm_policy/wombat.md", op="delete")
    publish(world.bus, gone)
    assert world.worker.drain() == [(gone.event_id, "removed")]
    assert world.indexer.store.count("WOMBAT-1") == 0


def test_bad_changes_are_refused_logged_and_do_not_block_the_queue(world) -> None:
    write_doc(world.root, "firm_policy/wombat.md", BANNER_BODY)
    with world.indexer.sessions() as s:
        s.add(Source(id="webhook-x", type="webhook", connector_config={}, classification="public_internal",
                     status="active", created_at=datetime(2026, 1, 1)))
        s.add(Source(id="fs-paused", type="filesystem", connector_config={}, classification="public_internal",
                     status="paused", created_at=datetime(2026, 1, 1)))
        s.commit()
    unknown = DocumentChanged(**{**_upsert("W-2", "firm_policy/wombat.md").__dict__, "source_id": "nope"})
    stolen = DocumentChanged(**{**_upsert("POL-NEW", "firm_policy/wombat.md").__dict__,
                                "source_id": "webhook-x"})  # POL-NEW belongs to fs-firm_policy
    outside = _upsert("W-3", "../../../secrets.md")
    invalid = _upsert("W-4", "firm_policy/wombat.md", access_level="everyone")
    paused = DocumentChanged(**{**_upsert("W-5", "firm_policy/wombat.md").__dict__,
                                "source_id": "fs-paused"})
    for e in (unknown, stolen, outside, invalid, paused):
        publish(world.bus, e)
    world.bus.publish(TOPIC, "junk", {"op": "explode"})
    outcomes = dict(world.worker.drain())
    assert [outcomes[e.event_id] for e in (unknown, stolen, outside, invalid, paused)] == [
        "rejected", "rejected", "rejected", "rejected", "paused"]
    assert "malformed" in outcomes.values()
    assert world.worker.drain() == []  # all committed: nothing comes back
    rejected = " | ".join(_audit(world.indexer, "event_rejected"))
    assert "not registered" in rejected and "belongs to source" in rejected and "outside" in rejected
    assert _audit(world.indexer, "event_malformed")
    with world.indexer.sessions() as s:
        assert s.get(Document, "POL-NEW").source_id == "fs-firm_policy"  # not taken over


def test_a_failure_is_not_committed_so_the_events_come_again(world, monkeypatch) -> None:
    """At-least-once: an unexpected error leaves the batch uncommitted."""
    write_doc(world.root, "firm_policy/wombat.md", BANNER_BODY)
    event = _upsert("WOMBAT-1", "firm_policy/wombat.md")
    publish(world.bus, event)
    original = world.indexer._index_doc

    def broken(*args, **kwargs):
        raise RuntimeError("embedding service down")

    monkeypatch.setattr(world.indexer, "_index_doc", broken)
    with pytest.raises(RuntimeError):
        world.worker.drain()
    monkeypatch.setattr(world.indexer, "_index_doc", original)
    world.worker.close()
    again = IngestWorker(world.indexer, world.bus)  # e.g. after a restart
    assert again.drain() == [(event.event_id, "indexed")]


def test_a_manifest_sync_never_removes_documents_from_other_sources(world) -> None:
    """Documents that came from a webhook or connector are not in the manifest; a full
    sync must leave them alone."""
    landing = world.indexer.landing_dir / "webhook-x" / "W-9.md"
    landing.parent.mkdir(parents=True)
    landing.write_text(BANNER_BODY, encoding="utf-8")
    with world.indexer.sessions() as s:
        s.add(Source(id="webhook-x", type="webhook", connector_config={}, classification="public_internal",
                     status="active", created_at=datetime(2026, 1, 1)))
        s.commit()
    event = DocumentChanged(source_id="webhook-x", doc_id="W-9", uri="landing:webhook-x/W-9.md",
                            op="upsert", content_hash="x" * 64,
                            metadata=dict(source_type="circulars", title="Pushed circular",
                                          access_level="staff", effective_date="2026-02-01"))
    publish(world.bus, event)
    assert world.worker.drain() == [(event.event_id, "indexed")]
    report = world.indexer.sync(actor="test")
    assert "W-9" not in report.removed
    with world.indexer.sessions() as s:
        assert s.get(Document, "W-9").path == "landing:webhook-x/W-9.md"


def test_pushed_content_works_with_a_landing_folder_outside_the_project(world, tmp_path) -> None:
    """Regression (found by the M12a live check): the landing folder is a setting and may
    live anywhere; a pushed document must still index."""
    world.indexer.landing_dir = tmp_path / "elsewhere" / "landing"
    file = world.indexer.landing_dir / "webhook-x" / "W-10.md"
    file.parent.mkdir(parents=True)
    file.write_text(BANNER_BODY, encoding="utf-8")
    with world.indexer.sessions() as s:
        s.add(Source(id="webhook-x", type="webhook", connector_config={}, classification="public_internal",
                     status="active", created_at=datetime(2026, 1, 1)))
        s.commit()
    event = DocumentChanged(source_id="webhook-x", doc_id="W-10", uri="landing:webhook-x/W-10.md",
                            op="upsert", content_hash="x" * 64,
                            metadata=dict(source_type="faq", title="Pushed", access_level="staff",
                                          effective_date="2026-02-01"))
    publish(world.bus, event)
    assert world.worker.drain() == [(event.event_id, "indexed")]


# ---------------------------------------------------- retries and dead letters (M12d)

def _fail_for(world, monkeypatch, doc_ids: set[str], times: int | None = None):
    """Make apply_events raise while a batch contains one of `doc_ids` (`times` calls, or
    always). Returns the list of failures, for counting."""
    real, failures = world.indexer.apply_events, []

    def flaky(events, actor="worker"):
        if any(e.doc_id in doc_ids for e in events) and (times is None or len(failures) < times):
            failures.append([e.doc_id for e in events])
            raise RuntimeError("embedding service down")
        return real(events, actor)

    monkeypatch.setattr(world.indexer, "apply_events", flaky)
    return failures


def test_a_failed_batch_is_retried_by_the_same_worker_and_not_lost(world, monkeypatch) -> None:
    """Covers: R-5-ing. Before M12d a failed batch was skipped by the next poll and lost as
    soon as a later batch was committed; now the subscription is rewound."""
    write_doc(world.root, "firm_policy/wombat.md", BANNER_BODY)
    write_doc(world.root, "firm_policy/later.md", "# Later\n\nLaterword.")
    first = _upsert("WOMBAT-1", "firm_policy/wombat.md")
    publish(world.bus, first)
    _fail_for(world, monkeypatch, {"WOMBAT-1"}, times=1)
    with pytest.raises(RuntimeError):
        world.worker.drain()
    assert world.worker.attempts == 1 and world.worker.retry_pause() == 5.0
    later = _upsert("LATER-1", "firm_policy/later.md")
    publish(world.bus, later)
    assert dict(world.worker.drain()) == {first.event_id: "indexed", later.event_id: "indexed"}
    assert world.worker.attempts == 0


def test_an_event_that_keeps_failing_is_dead_lettered_and_the_rest_go_on(world, monkeypatch) -> None:
    """Covers: R-5-ing. After max_attempts the batch is applied event by event; the event
    that still fails goes to doc-changes-dead with its error and an audit_log row; the
    others are indexed; nothing comes back; a replay re-sends it once the cause is fixed."""
    from app.ingest.worker import DEAD_TOPIC, replay_dead_letters

    worker = IngestWorker(world.indexer, world.bus, group="dl-test", max_attempts=3)
    for name in ("good-a", "bad", "good-b"):
        write_doc(world.root, f"firm_policy/{name}.md", f"# {name}\n\n{name} text.")
    events = [_upsert(n.upper(), f"firm_policy/{n}.md") for n in ("good-a", "bad", "good-b")]
    for e in events:
        publish(world.bus, e)
    failures = _fail_for(world, monkeypatch, {"BAD"})
    for attempt in (1, 2):
        with pytest.raises(RuntimeError):
            worker.drain()
        assert worker.attempts == attempt
    assert worker.retry_pause() == 10.0
    outcomes = dict(worker.drain())
    assert outcomes == {events[0].event_id: "indexed", events[1].event_id: "dead_lettered",
                        events[2].event_id: "indexed"}
    assert len(failures) == 3 + 1  # three whole batches, then BAD alone
    assert worker.drain() == []  # committed: the queue moves on

    letters = world.bus.subscribe(DEAD_TOPIC, "inspect")
    letter = letters.poll(0)
    assert letter["event"]["doc_id"] == "BAD" and "embedding service down" in letter["error"]
    assert letter["attempts"] == 3 and letters.poll(0) is None
    assert any("BAD" in t for t in _audit(world.indexer, "event_dead_lettered"))

    monkeypatch.undo()  # the cause is fixed
    assert replay_dead_letters(world.bus, wait_seconds=0) == 1
    assert [o for _, o in worker.drain()] == ["indexed"]
    assert replay_dead_letters(world.bus, wait_seconds=0) == 0  # replayed only once


def test_nothing_is_dead_lettered_while_the_database_is_down(world, monkeypatch) -> None:
    """If even the audit log cannot be written, the failure is not the event's fault: the
    batch is kept and retried, never dead-lettered."""
    import app.ingest.worker as worker_module
    from app.ingest.worker import DEAD_TOPIC

    worker = IngestWorker(world.indexer, world.bus, group="db-down", max_attempts=2)
    write_doc(world.root, "firm_policy/wombat.md", BANNER_BODY)
    event = _upsert("WOMBAT-1", "firm_policy/wombat.md")
    publish(world.bus, event)
    _fail_for(world, monkeypatch, {"WOMBAT-1"})

    def database_down(*args, **kwargs):
        raise RuntimeError("database is down")

    monkeypatch.setattr(worker_module, "write_audit_log", database_down)
    for _ in range(3):
        with pytest.raises(RuntimeError):
            worker.drain()
    assert world.bus.subscribe(DEAD_TOPIC, "inspect-2").poll(0) is None
    monkeypatch.undo()
    assert worker.drain() == [(event.event_id, "indexed")]
