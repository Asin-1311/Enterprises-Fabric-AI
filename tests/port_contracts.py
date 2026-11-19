"""Behaviour every adapter of a port must show (CLAUDE.md section 2: "the same test suite
runs against lite always, and against full in the integration job").

Each `check_*` function takes an adapter and asserts the contract. tests/test_ports_lite.py
calls them with the lite adapters; tests/integration/test_full_backends.py with the full
ones. Access control is part of the contract: an index must never return a chunk the
scope forbids.
"""

import time
import uuid
from datetime import datetime

from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Chunk, Document, Engagement, User
from app.ports import AccessScope, Cache, EventBus, KeywordIndex, VectorIndex
from app.retrieval.acl import UserContext, allowed_chunk_ids

# Three tiny documents, all containing the word "materiality".
DOCS = [  # doc_id, source_type, access_level, engagement_id
    ("POL-1", "firm_policy", "staff", ""),
    ("MGR-1", "firm_policy", "manager", ""),
    ("ENG-A-1", "engagement", "staff", "ENG-A"),
]
STAFF_B = UserContext("S2", "staff", frozenset({"ENG-B"}))      # not on ENG-A
STAFF_A = UserContext("S1", "staff", frozenset({"ENG-A"}))
PARTNER = UserContext("P1", "partner", frozenset())


def chunk_payloads() -> list[dict]:
    return [{"chunk_id": f"{doc}:0001", "doc_id": doc, "source_type": st, "access_level": lvl,
             "engagement_id": eng, "effective_date": "2026-01-01", "superseded": False,
             "text": f"{doc} materiality benchmark text"} for doc, st, lvl, eng in DOCS]


def seed_metadata(sessions: sessionmaker[Session]) -> None:
    """The same three documents in the metadata store (the source of the access rule)."""
    with sessions() as s:
        s.add_all([User(user_id=u, name=u, role=r, synthetic=True)
                   for u, r in (("S1", "staff"), ("S2", "staff"), ("P1", "partner"))])
        s.flush()
        s.add_all([Engagement(engagement_id=e, client_name=e, industry="x", fy="FY26",
                              partner_id="P1") for e in ("ENG-A", "ENG-B")])
        s.flush()
        for c in chunk_payloads():
            s.add(Document(doc_id=c["doc_id"], path=f"{c['doc_id']}.md", source_type=c["source_type"],
                           title=c["doc_id"], access_level=c["access_level"],
                           engagement_id=c["engagement_id"] or None, synthetic=True,
                           indexed_at=datetime(2026, 1, 1)))
            s.flush()
            s.add(Chunk(chunk_id=c["chunk_id"], doc_id=c["doc_id"], text=c["text"], token_count=4))
        s.commit()


def scope(sessions: sessionmaker[Session], user: UserContext,
          source_types: list[str] | None = None) -> AccessScope:
    with sessions() as s:
        allowed = allowed_chunk_ids(s, user, source_types)
    return AccessScope(user, tuple(source_types) if source_types else None, frozenset(allowed))


def _docs(hits: list[tuple[str, float]]) -> set[str]:
    return {cid.split(":")[0] for cid, _ in hits}


def check_keyword_index(index: KeywordIndex, sessions: sessionmaker[Session]) -> None:
    """Covers: R-6-stack (keyword index contract, ACL inside the index)."""
    index.upsert_chunks(chunk_payloads())
    index.refresh(sessions)
    assert _docs(index.search_scoped("materiality", scope(sessions, STAFF_B), 10)) == {"POL-1"}
    assert _docs(index.search_scoped("materiality", scope(sessions, STAFF_A), 10)) == {"POL-1", "ENG-A-1"}
    assert _docs(index.search_scoped("materiality", scope(sessions, PARTNER), 10)) == {
        "POL-1", "MGR-1", "ENG-A-1"}
    assert _docs(index.search_scoped("materiality", scope(sessions, PARTNER, ["engagement"]), 10)) == {
        "ENG-A-1"}
    assert index.search_scoped("zebra", scope(sessions, PARTNER), 10) == []

    # Raising a document's level: metadata store and index change together (as the indexer does).
    with sessions() as s:
        s.get(Document, "POL-1").access_level = "manager"
        s.commit()
    index.set_document_payload("POL-1", {"access_level": "manager"})
    index.refresh(sessions)
    assert index.search_scoped("materiality", scope(sessions, STAFF_B), 10) == []

    with sessions() as s:
        s.query(Chunk).filter(Chunk.doc_id == "MGR-1").delete()
        s.commit()
    index.delete_document("MGR-1")
    index.refresh(sessions)
    assert _docs(index.search_scoped("materiality", scope(sessions, PARTNER), 10)) == {"POL-1", "ENG-A-1"}

    # M12b: one chunk removed, the rest of its document's index entries stay.
    with sessions() as s:
        s.query(Chunk).filter(Chunk.chunk_id == "ENG-A-1:0001").delete()
        s.commit()
    index.delete_chunks(["ENG-A-1:0001", "NO-SUCH:0001"])
    index.refresh(sessions)
    assert _docs(index.search_scoped("materiality", scope(sessions, PARTNER), 10)) == {"POL-1"}
    assert index.index_chunk_ids() == {"POL-1": {"POL-1:0001"}}  # M12d: reconciliation


def check_vector_index(index: VectorIndex, sessions: sessionmaker[Session], embed) -> None:
    """Covers: R-6-stack (vector index contract, ACL inside the index)."""
    payloads = [{k: v for k, v in c.items() if k != "text"} for c in chunk_payloads()]
    index.upsert(payloads, [embed(c["text"]) for c in chunk_payloads()])
    query = embed("materiality benchmark")
    assert _docs(index.search_scoped(query, scope(sessions, STAFF_B), 10)) == {"POL-1"}
    assert _docs(index.search_scoped(query, scope(sessions, PARTNER), 10)) == {"POL-1", "MGR-1", "ENG-A-1"}
    assert index.count() == 3 and index.chunk_ids("POL-1") == {"POL-1:0001"}
    index.set_document_payload("POL-1", {"access_level": "manager"})
    assert index.search_scoped(query, scope(sessions, STAFF_B), 10) == []
    assert index.doc_ids_with("access_level", "manager") == {"POL-1", "MGR-1"}  # M12c
    assert index.doc_ids_with("superseded", True) == set()
    assert index.payloads("POL-1")[0]["access_level"] == "manager"
    index.delete_document("MGR-1")
    assert index.count("MGR-1") == 0 and index.count() == 2

    # M12b: stored vectors can be read back (reused for unchanged text), and single
    # chunks deleted.
    stored = index.vectors(["POL-1:0001", "NO-SUCH:0001"])
    assert set(stored) == {"POL-1:0001"} and len(stored["POL-1:0001"]) == index.dim
    index.delete_chunks(["ENG-A-1:0001", "NO-SUCH:0001"])
    assert index.count() == 1 and index.chunk_ids("POL-1") == {"POL-1:0001"}
    assert index.index_chunk_ids() == {"POL-1": {"POL-1:0001"}}  # M12d: reconciliation


def check_cache(cache: Cache) -> None:
    """Covers: R-6-stack (cache contract)."""
    key = f"contract-{uuid.uuid4().hex}"
    assert cache.get(key) is None
    cache.set(key, b"value", ttl_seconds=60)
    assert cache.get(key) == b"value"
    cache.delete(key)
    assert cache.get(key) is None
    cache.set(key, b"short", ttl_seconds=0.3)
    time.sleep(0.6)
    assert cache.get(key) is None  # expired


def check_event_bus(bus: EventBus, poll_seconds: float = 1.0) -> None:
    """Covers: R-6-stack (event bus contract: ordered, per-group, at-least-once)."""
    topic = f"contract-{uuid.uuid4().hex[:12]}"
    events = [{"n": i, "doc_id": f"D{i}"} for i in range(3)]
    first = bus.subscribe(topic, "group-a")
    for e in events:
        bus.publish(topic, e["doc_id"], e)

    assert first.poll(poll_seconds) == events[0]
    first.commit()  # event 0 is done
    assert first.poll(poll_seconds) == events[1]
    first.close()  # event 1 polled but NOT committed

    again = bus.subscribe(topic, "group-a")
    assert again.poll(poll_seconds) == events[1]  # redelivered: at-least-once
    assert again.poll(poll_seconds) == events[2]
    again.rewind()  # M12d: a failed batch is retried in the same subscription
    assert again.poll(poll_seconds) == events[1]
    assert again.poll(poll_seconds) == events[2]
    again.commit()
    again.rewind()  # nothing uncommitted: rewinding changes nothing
    assert again.poll(0.5) is None
    again.close()

    other = bus.subscribe(topic, "group-b")  # another group reads from the start
    assert other.poll(poll_seconds) == events[0]
    other.close()
