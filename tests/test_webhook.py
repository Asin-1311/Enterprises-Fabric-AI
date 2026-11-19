"""The webhook connector: POST /api/v1/webhooks/documents (M12a).

Covers: R-5-ing (webhook listener), R-7.1-c (webhook part; the other connectors are in
tests/test_connectors.py and tests/test_connector_contracts.py).
"""

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.adapters.lite.events import InProcessEventBus
from app.api.main import Services, create_app
from app.api.webhooks import signature
from app.concurrency import ReadWriteLock
from app.db.models import AuditLog, Document, Source
from app.ingest.worker import IngestWorker
from app.retrieval.acl import UserContext
from tests.helpers import make_indexer, make_searcher, write_doc, write_manifest, write_people
from tests.test_agents import DOCS, ENGAGEMENTS, USERS, _settings

SECRET = "test-webhook-secret-123"
SOURCE = "webhook-intranet"


@pytest.fixture()
def hook(tmp_path: Path, monkeypatch):
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
    with indexer.sessions() as s:
        s.add(Source(id=SOURCE, type="webhook", connector_config={"secret": "env:TEST_WEBHOOK_SECRET"},
                     classification="public_internal", status="active", created_at=datetime(2026, 1, 1)))
        s.commit()
    monkeypatch.setenv("TEST_WEBHOOK_SECRET", SECRET)
    bus = InProcessEventBus()
    settings = _settings().model_copy(update={"landing_dir": indexer.landing_dir})
    assistant = SimpleNamespace(deps=SimpleNamespace(sessions=indexer.sessions))
    services = Services(settings, assistant, indexer, ReadWriteLock(), bus=bus)  # type: ignore[arg-type]
    with TestClient(create_app(services, static_dir=None)) as client:
        yield SimpleNamespace(client=client, indexer=indexer, worker=IngestWorker(indexer, bus))


def send(client, body: dict, *, source: str = SOURCE, secret: str = SECRET, sig: str | None = None):
    raw = json.dumps(body).encode()
    return client.post("/api/v1/webhooks/documents", content=raw, headers={
        "Content-Type": "application/json", "X-Source-Id": source,
        "X-Signature-256": sig if sig is not None else signature(secret, raw)})


CIRCULAR = {"doc_id": "INTRANET-CIRC-7", "title": "Intranet circular 7", "source_type": "circulars",
            "access_level": "staff", "effective_date": "2026-11-01",
            "content": "# Circular 7\n\nQuokkaword: the new archiving rule for final files."}


def test_a_signed_document_is_queued_then_indexed_with_its_source(hook) -> None:
    """Covers: R-5-ing, R-7.1-c."""
    r = send(hook.client, CIRCULAR)
    assert r.status_code == 202 and r.json()["status"] == "accepted"
    assert hook.worker.drain() == [(r.json()["event_id"], "indexed")]
    with hook.indexer.sessions() as s:
        doc = s.get(Document, "INTRANET-CIRC-7")
        received = [a.target for a in s.scalars(select(AuditLog).where(AuditLog.action == "webhook_received"))]
    assert doc.source_id == SOURCE and doc.uri == f"landing:{SOURCE}/INTRANET-CIRC-7.md"
    assert doc.classification == "public_internal" and received == ["upsert INTRANET-CIRC-7"]
    staff = UserContext("S1", "staff", frozenset())
    hits = make_searcher(hook.indexer).search("quokkaword archiving", staff, mode="bm25")
    assert hits and hits[0].doc_id == "INTRANET-CIRC-7"

    gone = send(hook.client, {"doc_id": "INTRANET-CIRC-7", "op": "delete"})
    assert gone.status_code == 202 and hook.worker.drain()[0][1] == "removed"
    assert not (hook.indexer.landing_dir / SOURCE / "INTRANET-CIRC-7.md").exists()


def test_wrong_signatures_and_sources_get_the_same_answer(hook) -> None:
    for kwargs in ({"secret": "wrong-secret"}, {"sig": ""}, {"source": "nope"},
                   {"source": "fs-firm_policy"}):  # a filesystem source cannot be pushed to
        r = send(hook.client, CIRCULAR, **kwargs)
        assert r.status_code == 401 and r.json()["detail"] == "Invalid source or signature."
    assert hook.worker.drain() == []  # nothing was queued
    with hook.indexer.sessions() as s:
        assert len(list(s.scalars(select(AuditLog).where(AuditLog.action == "webhook_rejected")))) == 4


def test_tampering_with_a_signed_body_breaks_the_signature(hook) -> None:
    raw = json.dumps(CIRCULAR).encode()
    tampered = raw.replace(b'"staff"', b'"partner"')
    r = hook.client.post("/api/v1/webhooks/documents", content=tampered, headers={
        "Content-Type": "application/json", "X-Source-Id": SOURCE,
        "X-Signature-256": signature(SECRET, raw)})
    assert r.status_code == 401


def test_other_refusals(hook, monkeypatch) -> None:
    assert send(hook.client, {**CIRCULAR, "doc_id": "POL-NEW"}).status_code == 409  # another source's
    assert send(hook.client, {k: v for k, v in CIRCULAR.items() if k != "content"}).status_code == 422
    assert send(hook.client, {**CIRCULAR, "doc_id": "../../escape"}).status_code == 422
    assert send(hook.client, {**CIRCULAR, "role": "partner"}).status_code == 422  # unknown field
    with hook.indexer.sessions() as s:
        s.get(Source, SOURCE).status = "paused"
        s.commit()
    assert send(hook.client, CIRCULAR).status_code == 409
    monkeypatch.delenv("TEST_WEBHOOK_SECRET")
    assert send(hook.client, CIRCULAR).status_code == 503  # the secret is not configured


def test_bad_metadata_is_refused_by_enrichment_not_guessed(hook) -> None:
    r = send(hook.client, {**CIRCULAR, "access_level": "everyone"})
    assert r.status_code == 202  # the signature was fine, so the change is queued ...
    assert hook.worker.drain()[0][1] == "rejected"  # ... and enrichment refuses it
    with hook.indexer.sessions() as s:
        assert s.get(Document, "INTRANET-CIRC-7") is None
