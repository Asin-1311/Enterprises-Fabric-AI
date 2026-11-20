"""M16c: the admin console's back end: sources (list, pause, resume), recent questions,
routing-policy versions, classification policies and agent performance.

Covers: R-7.10 (API: register and pause a source, inspect a trace, roll back a prompt,
set classification policies, monitor agent performance).
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agents.graph import AgentDeps, AuditAssistant
from app.agents.query import ClientMatcher
from app.api.main import Services, create_app
from app.concurrency import ReadWriteLock
from app.db.models import AuditLog, Document, User
from app.ingest.classification import Rule, rule_problems, strictest
from app.retrieval.acl import UserContext
from app.security.passwords import hash_password
from tests.helpers import make_indexer, make_searcher
from tests.test_agents import FakeLLM, FakeReranker, _settings
from tests.test_clean_clone import _clean_clone

PASSWORD = "a-long-test-password"
EMAILS = {"A01": "kiran.rao@example-associates.test", "S01": "riya.kapoor@example-associates.test",
          "P01": "vikram.sethi@example-associates.test"}


@pytest.fixture(scope="module")
def console(tmp_path_factory):
    root = _clean_clone(tmp_path_factory.mktemp("console"))
    indexer = make_indexer(root, synthetic_fallback=True)
    indexer.sync(actor="test")
    with indexer.sessions() as s:
        clients = ClientMatcher.from_db(s)
        for user_id in EMAILS:
            user = s.get(User, user_id)
            user.password_hash = hash_password(PASSWORD)
            user.email = EMAILS[user_id]
        s.commit()
    settings = _settings().model_copy(update={"auth_rate_per_minute": 1000, "query_rate_per_minute": 1000})
    lock = ReadWriteLock()
    indexer.lock = lock.writer
    assistant = AuditAssistant(AgentDeps(settings=settings, llm=FakeLLM(), searcher=make_searcher(indexer, top_k=20),
                                         reranker=FakeReranker(), sessions=indexer.sessions, clients=clients))
    with TestClient(create_app(Services(settings, assistant, indexer, lock), static_dir=None)) as c:
        c.indexer, c.tokens = indexer, {}
        yield c


def as_user(c, user_id: str) -> dict:
    if user_id not in c.tokens:
        r = c.post("/api/v1/auth/login", json={"email": EMAILS[user_id], "password": PASSWORD})
        assert r.status_code == 200, r.text
        c.tokens[user_id] = r.json()["token"]
    return {"Authorization": f"Bearer {c.tokens[user_id]}"}


def classification(c, doc_id: str) -> tuple[str, str, set[str]]:
    """(documents.classification, base, the classifications in the vector index payloads)."""
    with c.indexer.sessions() as s:
        doc = s.get(Document, doc_id)
        found = (doc.classification, doc.base_classification)
    return (*found, {p.get("classification") for p in c.indexer.store.payloads(doc_id)})


def actions(c) -> list[str]:
    with c.indexer.sessions() as s:
        return [a.action for a in s.scalars(select(AuditLog))]


# ------------------------------------------------------------ classification policies

def test_policy_rules_only_make_documents_stricter() -> None:
    restrict_faq = Rule(1, "source_type", "faq", "restricted")
    engagement = Rule(2, "path", "*/engagement/*", "confidential")
    assert strictest("public_internal", [restrict_faq], "faq", "data/corpus/faq/a.md") == "restricted"
    assert strictest("public_internal", [restrict_faq], "standards", "x") == "public_internal"
    assert strictest("restricted", [engagement], "engagement", "data/corpus/engagement/a.md") == "restricted"
    assert strictest("public_internal", [engagement], "engagement", "data/corpus/engagement/a.md") == "confidential"
    assert rule_problems("source_type", "nonsense", "restricted")
    assert rule_problems("path", "*", "public_internal")  # a rule can never lower a level
    assert rule_problems("source_type", "faq", "restricted") == []


def test_a_classification_policy_reclassifies_documents_and_indexes_and_can_be_undone(console) -> None:
    """Covers: R-7.10 (set classification policies). A rule raises every matching document
    in the metadata store AND both index payloads; documents already stricter stay so;
    switching the rule off restores each document's base classification."""
    admin = as_user(console, "A01")
    assert classification(console, "FAQ-01")[0] == "public_internal"
    r = console.post("/api/v1/admin/classification-policies", headers=admin,
                     json={"match": "source_type", "pattern": "faq", "classification": "restricted"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert "FAQ-01" in body["reclassified"] and all(d.startswith("FAQ") for d in body["reclassified"])
    assert classification(console, "FAQ-01") == ("restricted", "public_internal", {"restricted"})
    staff = console.app.state.services.assistant.deps.searcher.fetch(
        sorted(console.indexer.store.chunk_ids("FAQ-01"))[:1], UserContext("S01", "staff", frozenset()))
    assert staff and staff[0].classification == "restricted"  # what the model router reads

    # A document that is already restricted is never lowered by a weaker rule.
    weaker = console.post("/api/v1/admin/classification-policies", headers=admin,
                          json={"match": "path", "pattern": "*firm_policy*", "classification": "confidential"})
    assert "POL-SEC-01" not in weaker.json()["reclassified"]
    assert classification(console, "POL-SEC-01")[0] == "restricted"

    off = console.post(f"/api/v1/admin/classification-policies/{body['policy']['id']}/deactivate", headers=admin)
    assert off.status_code == 200 and "FAQ-01" in off.json()["reclassified"]
    assert classification(console, "FAQ-01") == ("public_internal", "public_internal", {"public_internal"})
    console.post(f"/api/v1/admin/classification-policies/{weaker.json()['policy']['id']}/deactivate", headers=admin)
    listed = console.get("/api/v1/admin/classification-policies", headers=admin).json()
    assert [p["active"] for p in listed] == [False, False]
    assert {"classification_policy_created", "classification_policy_deactivated",
            "documents_reclassified"} <= set(actions(console))

    bad = console.post("/api/v1/admin/classification-policies", headers=admin,
                       json={"match": "source_type", "pattern": "nonsense", "classification": "restricted"})
    assert bad.status_code == 422
    assert console.post("/api/v1/admin/classification-policies", headers=as_user(console, "S01"),
                        json={"match": "source_type", "pattern": "faq",
                              "classification": "restricted"}).status_code == 403


def test_a_new_document_is_classified_by_the_active_rules_when_it_is_indexed(console) -> None:
    admin = as_user(console, "A01")
    rule = console.post("/api/v1/admin/classification-policies", headers=admin,
                        json={"match": "source_type", "pattern": "calendar", "classification": "confidential"}).json()
    try:
        from app.ingest.manifest import read_manifest

        _, rows = read_manifest(console.indexer.active_manifest()[0])
        row = next(r for r in rows if r.source_type == "calendar")
        with console.indexer.sessions() as s:  # forget it, then index it again from its event
            doc = s.get(Document, row.doc_id)
            doc.classification, doc.base_classification = "public_internal", "public_internal"
            s.commit()
        console.indexer.reindex_document(row.doc_id, actor="test")
        assert classification(console, row.doc_id)[:2] == ("confidential", "public_internal")
    finally:
        console.post(f"/api/v1/admin/classification-policies/{rule['policy']['id']}/deactivate", headers=admin)


# ------------------------------------------------------------------- sources

def test_admins_list_pause_and_resume_sources(console) -> None:
    """Covers: R-7.10 (register and pause sources)."""
    admin = as_user(console, "A01")
    listed = console.get("/api/v1/sources", headers=admin)
    assert listed.status_code == 200 and "fs-faq" in {s["id"] for s in listed.json()}
    paused = console.post("/api/v1/sources/fs-faq/pause", headers=admin)
    assert paused.status_code == 200 and paused.json()["status"] == "paused"
    from app.ingest.events import DocumentChanged

    event = DocumentChanged(source_id="fs-faq", doc_id="FAQ-01", uri="data/corpus/faq/x.md",
                            op="upsert", content_hash="h", metadata={})
    assert console.indexer.apply_events([event]) == [(event.event_id, "paused")]
    resumed = console.post("/api/v1/sources/fs-faq/resume", headers=admin)
    assert resumed.json()["status"] == "active"
    assert {"source_paused", "source_active"} <= set(actions(console))
    assert console.post("/api/v1/sources/fs-faq/pause", headers=as_user(console, "S01")).status_code == 403
    assert console.post("/api/v1/sources/nope/pause", headers=admin).status_code == 404


# ------------------------------------------------------------- questions, traces and policies

def test_recent_questions_show_text_to_partners_only(console) -> None:
    """Covers: R-7.10 (inspect traces): an administrator who is not a partner sees the
    outcome and timing of each step, never question text or document content."""
    console.app.state.services.assistant.ask("Within how many days must the final audit file be assembled?", "S01")
    admin = console.get("/api/v1/admin/queries?limit=5", headers=as_user(console, "A01")).json()
    assert admin and admin[0]["question"] is None and admin[0]["question_hidden"] is True
    assert admin[0]["status"] == "answered"
    trace = console.get(f"/api/v1/traces/{admin[0]['query_id']}", headers=as_user(console, "A01")).json()
    assert trace and all(t["input"] is None and t["output"] is None for t in trace)
    with console.indexer.sessions() as s:
        s.get(User, "P01").is_admin = True
        s.commit()
    try:
        partner = console.get("/api/v1/admin/queries?limit=5", headers=as_user(console, "P01")).json()
        assert partner[0]["question"].startswith("Within how many days")
    finally:
        with console.indexer.sessions() as s:
            s.get(User, "P01").is_admin = False
            s.commit()
    assert console.get("/api/v1/admin/queries", headers=as_user(console, "S01")).status_code == 403
    versions = console.get("/api/v1/admin/routing-policies", headers=as_user(console, "A01")).json()
    assert versions and versions[0]["is_active"] in (True, False) and "replay" in str(versions[-1]["eval_report"])


# ------------------------------------------------------------------- performance

def test_agent_performance_comes_from_the_stored_traces(console) -> None:
    """Covers: R-7.10 (monitor agent performance)."""
    console.app.state.services.assistant.ask("What is our firm's overall materiality benchmark?", "S01")
    admin = as_user(console, "A01")
    perf = console.get("/api/v1/admin/agents/performance", headers=admin)
    assert perf.status_code == 200, perf.text
    body = perf.json()
    assert body["answers"] >= 1 and body["answers_by_status"].get("answered", 0) >= 1
    agents = {a["agent"]: a for a in body["agents"]}
    assert {"query_understanding", "synthesis", "verification"} <= set(agents)
    assert agents["synthesis"]["steps"] >= 1 and agents["synthesis"]["tokens_out"] > 0
    assert agents["synthesis"]["latency_p95_ms"] >= agents["synthesis"]["latency_p50_ms"]
    assert body["verification_failure_rate"] is not None and body["degraded_rate"] == 0
    assert body["estimated_cost_per_answer_usd"] == 0  # fake models have no listed price
    assert "estimate" in body["cost_basis"]
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    later = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    empty = console.get("/api/v1/admin/agents/performance", headers=admin,
                        params={"from": future, "to": later}).json()
    assert empty["answers"] == 0 and empty["agents"] == []
    assert console.get("/api/v1/admin/agents/performance", headers=admin,
                       params={"from": later, "to": future}).status_code == 422
    assert console.get("/api/v1/admin/agents/performance", headers=as_user(console, "S01")).status_code == 403


def test_the_admin_page_is_served_by_the_api_with_the_react_app(console, tmp_path) -> None:
    """/admin is a client-side route: the server answers it with the app's index.html (the
    app then shows the console, or refuses a non-administrator)."""
    (tmp_path / "index.html").write_text("<!doctype html><div id=root>ui</div>", encoding="utf-8")
    app = create_app(console.app.state.services, static_dir=tmp_path)
    with TestClient(app) as c:
        for path in ("/admin", "/admin/anything"):
            r = c.get(path)
            assert r.status_code == 200 and "id=root" in r.text
        assert c.get("/api/v1/health").status_code == 200  # the API is unaffected
