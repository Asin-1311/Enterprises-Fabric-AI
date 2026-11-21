"""M16a: rate limiting of /query and the access-safe response cache (lite profile; the
Redis versions are in tests/integration/test_full_backends.py).

Covers: R-5-serve (429 after the limit; the cache never crosses access fingerprints).
"""

import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.adapters.lite.cache import MemoryCache
from app.agents.graph import AgentDeps, AuditAssistant
from app.agents.query import ClientMatcher
from app.api.cache import ResponseCache, access_fingerprint, normalise_question
from app.api.main import Services, create_app
from app.concurrency import ReadWriteLock
from app.db.models import Answer, Citation, Query, Trace
from app.ingest import index_version
from app.retrieval.acl import UserContext
from tests.helpers import make_indexer, make_searcher, write_doc, write_manifest, write_people
from tests.test_agents import DOCS, FakeLLM, FakeReranker, _settings
from tests.test_api import PASSWORD, parse_sse, with_emails

# S3 is a second staff member on the Alpha team only: the same access fingerprint as S1.
# M3 is a second manager without engagements: the same fingerprint as M2.
USERS = ("user_id,name,role,synthetic\nS1,Staff A,staff,true\nS2,Staff B,staff,true\n"
         "S3,Staff C,staff,true\nM2,Manager none,manager,true\nM3,Manager two,manager,true\n"
         "P1,Partner,partner,true\n")
ENGAGEMENTS = ("engagement_id,client_name,industry,fy,partner_id,member_ids\n"
               "ENG-A,Alpha Fictional Ltd,manufacturing,2025-26,P1,S1;S3\n"
               "ENG-B,Beta Fictional Bank,banking,2025-26,P1,S2\n")
RISKS = "Alpha Fictional Ltd planning memo significant risks"  # engagement document
BENCHMARK = "What is our materiality benchmark managerword?"     # manager-level document
FILE_DAYS = "Within how many days must the final audit file be assembled?"


class CountingLLM(FakeLLM):
    def questions(self) -> int:
        return self.purposes().count("query_understanding")


@pytest.fixture()
def world(tmp_path: Path):
    root = tmp_path / "serving"
    (root / "data").mkdir(parents=True)
    write_people(root, with_emails(USERS), ENGAGEMENTS)
    rows = []
    for doc_id, rel, st, level, eng, eff, sup, body in DOCS:
        write_doc(root, rel, body)
        rows.append(dict(doc_id=doc_id, path=f"data/corpus/{rel}", source_type=st, title=f"{doc_id} title",
                         version="1", effective_date=eff, supersedes=sup, access_level=level,
                         engagement_id=eng, synthetic="true"))
    write_manifest(root, rows)
    indexer = make_indexer(root)
    indexer.sync(actor="test")
    with indexer.sessions() as s:
        clients = ClientMatcher.from_db(s)
    llm = CountingLLM()
    settings = _settings().model_copy(update={"demo_password": PASSWORD, "auth_rate_per_minute": 1000,
                                              "query_rate_per_minute": 1000})
    assistant = AuditAssistant(AgentDeps(settings=settings, llm=llm, searcher=make_searcher(indexer, top_k=20),
                                         reranker=FakeReranker(), sessions=indexer.sessions, clients=clients))
    cache = ResponseCache(MemoryCache(), indexer.sessions, 3600)
    services = Services(settings, assistant, indexer, ReadWriteLock(), response_cache=cache)
    with TestClient(create_app(services, static_dir=None)) as c:
        c.llm, c.indexer, c.cache, c.tokens = llm, indexer, cache, {}
        yield c


def headers(c, user_id: str) -> dict:
    if user_id not in c.tokens:
        r = c.post("/api/v1/auth/login", json={"email": f"{user_id.lower()}@test.example", "password": PASSWORD})
        assert r.status_code == 200, r.text
        c.tokens[user_id] = r.json()["token"]
    return {"Authorization": f"Bearer {c.tokens[user_id]}"}


def ask(c, text: str, user_id: str) -> tuple[list[str], dict]:
    r = c.post("/api/v1/query", json={"text": text}, headers=headers(c, user_id))
    assert r.status_code == 200, r.text
    events = parse_sse(r.text)
    steps = [d.get("step") for k, d in events if k == "progress"]
    return steps, events[-1][1]


def cited_docs(final: dict) -> set[str]:
    return {c["doc_id"] for c in final["citations"]}


# ------------------------------------------------------------------ rate limiting

def test_a_user_over_the_query_limit_gets_429_with_retry_after(world) -> None:
    """Covers: R-5-serve (rate limiting). The limit is per user: another user is unaffected."""
    world.app.state.query_limiter = None
    world.app.state.services.settings.query_rate_per_minute = 2
    try:
        for _ in range(2):
            assert world.post("/api/v1/query", json={"text": FILE_DAYS}, headers=headers(world, "S1")).status_code == 200
        r = world.post("/api/v1/query", json={"text": FILE_DAYS}, headers=headers(world, "S1"))
        assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1
        assert "Too many questions" in r.json()["detail"]
        assert world.post("/api/v1/query", json={"text": FILE_DAYS}, headers=headers(world, "S2")).status_code == 200
    finally:
        world.app.state.services.settings.query_rate_per_minute = 1000
        world.app.state.query_limiter = None


# ------------------------------------------------------------------ the response cache

def test_the_key_parts() -> None:
    assert normalise_question("  Within how MANY days?? ") == normalise_question("within how many days")
    a = UserContext("S1", "staff", frozenset({"ENG-B", "ENG-A"}))
    assert access_fingerprint(a) == "staff|ENG-A,ENG-B"
    assert access_fingerprint(a) != access_fingerprint(UserContext("S9", "manager", frozenset({"ENG-A", "ENG-B"})))


def test_the_cache_never_crosses_access_fingerprints(world) -> None:
    """Covers: R-5-serve (the cache never crosses access fingerprints).
    Engagement boundary: S1 and S3 (staff, Alpha team) share an answer citing Alpha's memo;
    S2 (Beta team) and P1 (partner) never receive it. Role boundary: M2 and M3 (managers)
    share an answer citing a manager-level policy; staff never receive it and their own
    answer does not cite it. A hit is the asker's own answer: own rows, history, feedback."""
    steps, first = ask(world, RISKS, "S1")
    assert "cache_hit" not in steps and first["status"] == "answered" and "ENG-A-1" in cited_docs(first)
    asked = world.llm.questions()
    steps, same = ask(world, RISKS, "S3")
    assert "cache_hit" in steps and world.llm.questions() == asked  # no model call
    assert same["text"] == first["text"] and cited_docs(same) == cited_docs(first)
    assert same["query_id"] != first["query_id"] and same["answer_id"] != first["answer_id"]
    for other in ("S2", "P1"):
        steps, theirs = ask(world, RISKS, other)
        assert "cache_hit" not in steps, other
    assert theirs["status"] == "answered"  # the partner's own, fresh answer
    _, s2 = ask(world, RISKS, "S2")
    assert s2["status"] == "denied" and s2["citations"] == []

    steps, manager = ask(world, BENCHMARK, "M2")
    assert "cache_hit" not in steps and "MGR-1" in cited_docs(manager)
    steps, manager2 = ask(world, BENCHMARK, "M3")
    assert "cache_hit" in steps and cited_docs(manager2) == cited_docs(manager)
    for staff in ("S1", "S2", "S3"):
        steps, theirs = ask(world, BENCHMARK, staff)
        assert "cache_hit" not in steps and "MGR-1" not in cited_docs(theirs), staff

    with world.indexer.sessions() as s:
        q = s.get(Query, same["query_id"])
        trace = s.scalars(select(Trace).where(Trace.query_id == q.id)).one()
        cites = s.scalars(select(Citation).where(Citation.answer_id == same["answer_id"])).all()
    assert q.user_id == "S3" and trace.agent == "response_cache"
    assert f'"source_answer_id": {first["answer_id"]}' in trace.output and cites
    history = world.get("/api/v1/history", headers=headers(world, "S3")).json()
    assert same["query_id"] in [h["query_id"] for h in history]
    assert first["query_id"] not in [h["query_id"] for h in history]
    assert world.get(f"/api/v1/history/{same['query_id']}", headers=headers(world, "S1")).status_code == 404
    rated = world.post("/api/v1/feedback", headers=headers(world, "S3"),
                       json={"answer_id": same["answer_id"], "rating": "helpful"})
    assert rated.status_code == 200


def test_an_index_change_or_a_new_prompt_version_makes_the_cache_miss(world) -> None:
    ask(world, FILE_DAYS, "S1")
    steps, _ = ask(world, FILE_DAYS, "S1")
    assert "cache_hit" in steps
    before = index_version.current(world.indexer.sessions)
    world.indexer.reindex_document("POL-NEW", actor="test")
    assert index_version.current(world.indexer.sessions) > before
    steps, _ = ask(world, FILE_DAYS, "S1")
    assert "cache_hit" not in steps
    steps, _ = ask(world, FILE_DAYS, "S1")
    assert "cache_hit" in steps

    from app.agents.versions import PROMPTS
    from app.db.models import PromptVersion
    from app.learning.prompt_registry import activate, create_version

    with world.indexer.sessions() as s:
        v = create_version(s, "synthesis", PROMPTS["synthesis"] + "\nBe brief.", "test")
        v.eval_report = {"replay": {"passed": True}}
        s.commit()
        activate(s, v.id, "test")
        s.commit()
    steps, _ = ask(world, FILE_DAYS, "S1")
    assert "cache_hit" not in steps
    with world.indexer.sessions() as s:  # put the seed back for the other tests
        seed = s.scalars(select(PromptVersion).where(PromptVersion.agent == "synthesis")
                         .order_by(PromptVersion.version)).first()
        activate(s, seed.id, "test")
        s.commit()


def test_only_fully_verified_answers_are_cached(world) -> None:
    """Denials, not-found and degraded (reduced checking) answers always run again."""
    _, denied = ask(world, RISKS, "S2")
    assert denied["status"] == "denied"
    steps, _ = ask(world, RISKS, "S2")
    assert "cache_hit" not in steps
    _, final = ask(world, FILE_DAYS, "S2")
    with world.indexer.sessions() as s:
        result_answer = s.get(Answer, final["answer_id"])
    from app.agents.graph import AnswerResult, CitationInfo

    degraded = AnswerResult(1, result_answer.id, "answered", "x", [CitationInfo(
        "POL-NEW:1:1", "POL-NEW", "t", "1", None, None, None, False)], degraded=True)
    assert world.cache.store("k-degraded", degraded) is False and world.cache.cache.get("k-degraded") is None


def test_a_cache_entry_citing_a_document_the_asker_may_not_see_is_never_served(world, caplog) -> None:
    """Defence in depth: even if an entry ended up under the wrong key, serving re-checks
    every cited document against the asker's access and drops the entry."""
    _, first = ask(world, RISKS, "S1")
    s2 = UserContext("S2", "staff", frozenset({"ENG-B"}))
    s1_key = world.cache.key(RISKS, UserContext("S1", "staff", frozenset({"ENG-A"})))
    s2_key = world.cache.key(RISKS, s2)
    world.cache.cache.set(s2_key, world.cache.cache.get(s1_key), 60)  # simulate a wrong key
    caplog.set_level(logging.ERROR, logger="app.api.cache")
    assert world.cache.serve(s2_key, RISKS, s2) is None
    assert "SECURITY" in caplog.text and world.cache.cache.get(s2_key) is None
    steps, theirs = ask(world, RISKS, "S2")
    assert "cache_hit" not in steps and "ENG-A-1" not in cited_docs(theirs)


def test_every_outermost_index_write_raises_the_version_once(world) -> None:
    sessions = world.indexer.sessions
    before = index_version.current(sessions)
    with world.indexer.lock:
        with world.indexer.lock:  # nested (re-entrant) blocks count once
            pass
    assert index_version.current(sessions) == before + 1
