"""M12d: change data capture from the synthetic DMS (Debezium messages -> change events ->
worker -> every index), reconciliation, and the scheduled jobs list.

The Debezium messages here are written in the documented Debezium PostgreSQL connector
envelope (JSON converter, schemas disabled, time.precision.mode=connect). The real
Debezium output is checked in tests/integration/test_full_backends.py.

Covers: R-5-ing (the dms connector), R-3-stream (lite half: the event path).
"""

from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.adapters.lite.events import InProcessEventBus
from app.connectors.dms import CdcBridge, DmsTranslator, SkippedChange, debezium_topic
from app.db.models import Chunk, Document
from app.ingest.md_loader import SYNTHETIC_BANNER
from app.ingest.reconcile import find_drift, reconcile
from app.ingest.worker import IngestWorker
from tests.helpers import make_indexer, write_doc, write_manifest, write_people
from tests.test_agents import ENGAGEMENTS, USERS

CONFIG = {"table": "dms.documents", "defaults": {"classification": "public_internal"}}
BODY = f"{SYNTHETIC_BANNER}\n\n# Archiving\n\n## 1. Steps\nLock the file; ocelotword.\n"


def row(**changes) -> dict:
    base = {"doc_id": "DMS-POL-001", "title": "Archiving procedure", "source_type": "firm_policy",
            "access_level": "staff", "engagement_id": None, "effective_date": 20544,  # 2026-04-01
            "version": "1", "owner": None, "classification": None, "body": BODY,
            "updated_at": "2026-11-19T10:00:00.000000Z"}
    return base | changes


def message(op: str, before: dict | None = None, after: dict | None = None) -> dict:
    return {"before": before, "after": after, "op": op, "ts_ms": 1795600000000,
            "source": {"connector": "postgresql", "db": "dms", "schema": "dms",
                       "table": "documents", "ts_ms": 1795599999000}, "transaction": None}


def test_debezium_messages_become_change_events(tmp_path: Path) -> None:
    """Covers: R-5-ing (dms connector)."""
    t = DmsTranslator("dms-main", CONFIG, tmp_path)
    created = t.translate(message("c", after=row()))
    assert (created.op, created.doc_id, created.uri) == ("upsert", "DMS-POL-001",
                                                        "landing:dms-main/DMS-POL-001.md")
    assert created.metadata["effective_date"] == "2026-04-01"  # Debezium DATE: days since 1970
    assert created.metadata["classification"] == "public_internal"  # from the source defaults
    assert (tmp_path / "dms-main" / "DMS-POL-001.md").read_text(encoding="utf-8") == BODY
    assert created.occurred_at.startswith("2026-")
    snap = t.translate(message("r", after=row(access_level="manager")))
    assert snap.op == "upsert" and snap.metadata["access_level"] == "manager"
    gone = t.translate(message("d", before=row(), after=None))
    assert (gone.op, gone.doc_id) == ("delete", "DMS-POL-001")
    for bad in (message("t"), message("u", after=None),
                message("c", after=row(doc_id="../../etc/passwd")), message("c", after=row(doc_id=""))):
        with pytest.raises(SkippedChange):
            t.translate(bad)
    assert debezium_topic(CONFIG) == "dms.dms.documents"


@pytest.fixture()
def world(tmp_path: Path):
    root = tmp_path / "proj"
    (root / "data").mkdir(parents=True)
    write_people(root, USERS, ENGAGEMENTS)
    write_doc(root, "firm_policy/p.md", "# Policy\n\n## 1. Rule\nPolicy text.\n")
    write_manifest(root, [dict(doc_id="POL-1", path="data/corpus/firm_policy/p.md",
                               source_type="firm_policy", title="P", version="1",
                               effective_date="2026-01-01", access_level="staff", synthetic="true")])
    indexer = make_indexer(root)
    indexer.sync(actor="test")
    from datetime import datetime

    from app.db.models import Source
    with indexer.sessions() as s:
        s.add(Source(id="dms-main", type="dms", connector_config=CONFIG, owner="M2",
                     classification="public_internal", status="active", created_at=datetime(2026, 11, 19)))
        s.commit()
    bus = InProcessEventBus()
    return SimpleNamespace(root=root, indexer=indexer, bus=bus, worker=IngestWorker(indexer, bus),
                           bridge=CdcBridge("dms-main", CONFIG, bus, indexer.landing_dir))


def test_dms_row_changes_flow_through_the_bridge_into_every_index(world) -> None:
    """Covers: R-3-stream (event path, lite), R-5-ing: insert, update and delete of DMS rows
    reach SQLite, Qdrant and BM25; the bridge commits only what it published."""
    topic = debezium_topic(CONFIG)
    world.bus.publish(topic, "DMS-POL-001", message("c", after=row()))
    world.bus.publish(topic, "BAD", message("c", after=row(doc_id="../x")))  # skipped, logged
    assert world.bridge.drain() == 1 and world.bridge.skipped == 1
    assert [o for _, o in world.worker.drain()] == ["indexed"]
    with world.indexer.sessions() as s:
        doc = s.get(Document, "DMS-POL-001")
        ids = set(s.scalars(select(Chunk.chunk_id).where(Chunk.doc_id == "DMS-POL-001")))
    assert (doc.source_id, doc.owner, doc.effective_date) == ("dms-main", "M2", "2026-04-01")
    assert ids and world.indexer.store.chunk_ids("DMS-POL-001") == ids
    assert [c for c, _ in world.indexer.bm25.search("ocelotword")][0].startswith("DMS-POL-001:")

    edited = row(body=BODY.replace("ocelotword", "lynxword"), version="2")
    world.bus.publish(topic, "DMS-POL-001", message("u", before=row(), after=edited))
    assert world.bridge.drain() == 1
    assert [o for _, o in world.worker.drain()] == ["indexed"]
    assert world.indexer.bm25.search("ocelotword") == []
    assert world.indexer.bm25.search("lynxword")

    world.bus.publish(topic, "DMS-POL-001", message("d", before=edited))
    assert world.bridge.drain() == 1
    assert [o for _, o in world.worker.drain()] == ["removed"]
    assert world.indexer.store.count("DMS-POL-001") == 0
    assert world.bridge.drain() == 0  # committed: nothing comes back


def test_a_failed_publish_is_retried_not_lost(world) -> None:
    topic = debezium_topic(CONFIG)
    world.bus.publish(topic, "DMS-POL-001", message("c", after=row()))
    real = world.bus.publish
    world.bus.publish = lambda t, k, e: (_ for _ in ()).throw(RuntimeError("kafka down")) \
        if t == "doc-changes" else real(t, k, e)
    with pytest.raises(RuntimeError):
        world.bridge.drain()
    world.bus.publish = real
    assert world.bridge.drain() == 1  # rewound: the same change came again


# --------------------------------------------------------------- reconciliation

def test_reconciliation_finds_and_repairs_index_drift(world) -> None:
    """Drift is a document whose chunk ids differ between the metadata store and an index,
    or index entries of an unknown document; the nightly job repairs both."""
    indexer = world.indexer
    assert find_drift(indexer)[:2] == ([], [])
    lost = sorted(indexer.store.chunk_ids("POL-1"))[0]
    indexer.store.delete_chunks([lost])                   # e.g. a crash between two writes
    indexer.store.upsert([{"chunk_id": "GHOST:1:1", "doc_id": "GHOST", "source_type": "faq",
                           "access_level": "staff", "engagement_id": "", "superseded": False}],
                         [[0.1] * indexer.store.dim])      # e.g. a restored old backup
    assert find_drift(indexer)[:2] == (["POL-1"], ["GHOST"])

    check = reconcile(indexer, repair=False)
    assert check.drifted == ["POL-1"] and indexer.store.count("GHOST") == 1  # nothing changed
    report = reconcile(indexer)
    assert report.drifted == ["POL-1"] and report.orphans_removed == ["GHOST"]
    assert not report.repair_failed and report.documents_checked == 1
    assert find_drift(indexer)[:2] == ([], [])
    assert lost in indexer.store.chunk_ids("POL-1")


def test_scheduled_jobs_name_real_commands() -> None:
    """flows/jobs.py (no Prefect import) lists what flows/serve.py schedules; every job must
    be a module of this project and a valid cron line."""
    import importlib.util

    from flows.jobs import JOBS

    assert set(JOBS) == {"nightly-reconciliation", "nightly-retrieval-eval"}
    for job in JOBS.values():
        assert len(job.cron.split()) == 5
        assert job.argv[:1] == ["-m"] and importlib.util.find_spec(job.argv[1]) is not None
