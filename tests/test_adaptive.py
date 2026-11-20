"""M15a: retrieval weighted by question type, source freshness, the latency window and
degradation under load.

Covers: R-3-adapt, R-8-degrade, R-8-record.
"""

from datetime import date, datetime
from pathlib import Path

import pytest
from sqlalchemy import select

from app.adapters.full.latency import RedisLatencyWindow
from app.adapters.lite.latency import InMemoryLatencyWindow
from app.adaptive.degradation import REDUCED_CHECKING_NOTICE, low_risk_reasons, under_load
from app.adaptive.latency_window import p95
from app.agents.graph import AgentDeps, AuditAssistant
from app.agents.query import ClientMatcher
from app.db.models import Answer, Retrieval, Trace
from app.retrieval.fusion import reciprocal_rank_fusion
from app.retrieval.recency import multipliers, source_freshness
from tests.helpers import make_indexer, make_searcher, write_doc, write_manifest, write_people
from tests.test_agents import ENGAGEMENTS, USERS, FakeLLM, FakeReranker, _settings

DOCS = {  # doc_id: (path, source_type, access, engagement, body)
    "FAQ-1": ("faq/faq.md", "faq", "staff", "",
              "# Helpdesk FAQ\n\n## Files\n\n### Q: How many days to assemble the file?\n"
              "A: Assemble the file within sixty days; zebraword."),
    "CAL-1": ("calendar/cal.md", "calendar", "staff", "",
              "# Compliance calendar\n\n## 1. Tax audit\nThe tax audit report is due on 30 September; zebraword."),
    "POL-1": ("firm_policy/pol.md", "firm_policy", "staff", "",
              "# File policy\n\n## 1. Assembly\nOur firm assembles files within forty days; quaggaword."),
    "ENG-1": ("engagement/a/memo.md", "engagement", "staff", "ENG-A",
              "# Alpha planning memo\n\n## 1. Risks\nAlpha Fictional risk is inventory; quaggaword."),
}


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    root: Path = tmp_path_factory.mktemp("adaptive")
    (root / "data").mkdir()
    write_people(root, USERS, ENGAGEMENTS)
    rows = []
    for doc_id, (rel, st, level, eng, body) in DOCS.items():
        write_doc(root, rel, body)
        rows.append(dict(doc_id=doc_id, path=f"data/corpus/{rel}", source_type=st, title=doc_id,
                         version="1", effective_date="2025-04-01", access_level=level,
                         engagement_id=eng, synthetic="true"))
    write_manifest(root, rows)
    indexer = make_indexer(root)
    indexer.sync(actor="test")
    with indexer.sessions() as s:
        clients = ClientMatcher.from_db(s)
    return indexer, clients


def understood(intent: str, *subs: str):
    return lambda user: {"intent": intent, "sa_numbers": [], "clients": [], "fs_items": [],
                         "dates_or_years": [], "sub_queries": list(subs) or [user.removeprefix("Question: ")]}


def bot(world, llm: FakeLLM, latency=None, **settings) -> AuditAssistant:
    indexer, clients = world
    return AuditAssistant(AgentDeps(settings=_settings().model_copy(update=settings), llm=llm,
                                    searcher=make_searcher(indexer, top_k=20), reranker=FakeReranker(),
                                    sessions=indexer.sessions, clients=clients, latency=latency))


def busy() -> InMemoryLatencyWindow:
    window = InMemoryLatencyWindow(size=100, min_samples=20)
    for _ in range(25):
        window.record(20.0)  # well above the 8 s target
    return window


# ------------------------------------------------------------- fusion and freshness

def test_weighted_rrf() -> None:
    plain = dict(reciprocal_rank_fusion([["a", "b"], ["b", "a"]], k=60))
    assert plain["a"] == pytest.approx(plain["b"])
    weighted = dict(reciprocal_rank_fusion([["a", "b"], ["b", "a"]], k=60, weights=[0.4, 0.6]))
    assert weighted["b"] > weighted["a"]  # the heavier list's first item wins
    assert weighted["b"] == pytest.approx(0.4 / 62 + 0.6 / 61)


def test_source_freshness_boosts_recently_changed_sources_for_time_sensitive_questions() -> None:
    today = date(2026, 11, 20)
    assert source_freshness(datetime(2026, 11, 20, 9), today, 30) == 1.0
    assert source_freshness(datetime(2026, 11, 5), today, 30) == pytest.approx(0.5)
    assert source_freshness(datetime(2026, 9, 1), today, 30) == 0.0 and source_freshness(None, today, 30) == 0.0
    kw = dict(superseded=False, effective_date="2026-04-01", as_of=today, superseded_weight=0.5,
              recency_weight=0.2, half_life_days=365, updated_at=datetime(2026, 11, 20),
              freshness_weight=0.1, freshness_window_days=30)
    assert multipliers(time_sensitive=True, **kw)["source_freshness"] == pytest.approx(1.1)
    assert multipliers(time_sensitive=False, **kw)["source_freshness"] == 1.0  # never otherwise


# ------------------------------------------------- weights and counts by question type

def fused_rows(world, query_id: int) -> list[Retrieval]:
    with world[0].sessions() as s:
        return s.scalars(select(Retrieval).where(Retrieval.query_id == query_id)).all()


def test_question_types_get_different_weights_counts_and_chunk_levels_recorded(world) -> None:
    """Covers: R-3-adapt, R-8-record. A factual and a comparison question are retrieved with
    different retriever weights, candidate counts and chunk levels, and each fused
    `retrievals` row records them."""
    factual = bot(world, FakeLLM(), precise_top_k=3).ask("What does our file policy say?", "S1")
    compare = bot(world, FakeLLM(query_understanding=understood("comparison", "file assembly days", "tax audit due")),
                  exploratory_top_k=7).ask("How do assembly and tax audit dates compare?", "S1")
    seen = {}
    for name, result in (("factual", factual), ("comparison", compare)):
        rows = fused_rows(world, result.query_id)
        fused = [e for r in rows if r.method == "fused" for e in r.scores]
        dense = [r for r in rows if r.method == "dense"]
        plans = {(tuple(sorted(e["plan"]["retriever_weights"].items())), e["plan"]["candidates_per_retriever"],
                  e["plan"]["chunk_level"]) for e in fused}
        assert len(plans) == 1 and all(e["weights"] == dict(next(iter(plans))[0]) for e in fused)
        assert all(len(r.scores) <= next(iter(plans))[1] for r in dense)
        seen[name] = next(iter(plans))
    assert seen["factual"] == ((("dense", 0.4), ("keyword", 0.6)), 3, "child")
    assert seen["comparison"] == ((("dense", 0.6), ("keyword", 0.4)), 7, "parent")


# ---------------------------------------------------------------- the latency window

def test_latency_window_needs_enough_samples() -> None:
    w = InMemoryLatencyWindow(size=5, min_samples=3)
    w.record(1)
    w.record(2)
    assert w.p95() is None
    for x in (3, 4, 50, 6):
        w.record(x)
    assert list(w.values) == [2.0, 3.0, 4.0, 50.0, 6.0]  # the oldest (1.0) left the window
    assert w.p95() == 50.0
    assert p95([1, 2, 3, 4, 100]) == 100 and p95(list(range(1, 101))) == 95
    assert under_load(9.0, 8.0) and not under_load(7.9, 8.0) and not under_load(None, 8.0)
    assert not under_load(30.0, 8.0, enabled=False)


class FakeRedis:
    """The four Redis calls RedisLatencyWindow makes."""

    def __init__(self) -> None:
        self.lists: dict[str, list[bytes]] = {}

    def pipeline(self):
        return self

    def lpush(self, key, value):
        self.lists.setdefault(key, []).insert(0, str(value).encode())

    def ltrim(self, key, start, end):
        self.lists[key] = self.lists[key][start:end + 1]

    def execute(self):
        return []

    def lrange(self, key, start, end):
        return self.lists.get(key, [])[start:end + 1]


def test_the_redis_window_is_shared_and_trimmed() -> None:
    client = FakeRedis()
    a, b = (RedisLatencyWindow("redis://x", size=3, min_samples=2, client=client) for _ in range(2))
    a.record(1.0)
    assert b.p95() is None
    b.record(9.0)
    a.record(2.0)
    a.record(3.0)  # the oldest (1.0) drops out of the window of 3
    assert sorted(float(v) for v in client.lists["fabric:latency:answers"]) == [2.0, 3.0, 9.0]
    assert a.p95() == b.p95() == 9.0


# ---------------------------------------------------------------- degradation

def test_under_load_a_low_risk_question_skips_verification_and_says_so(world) -> None:
    """Covers: R-8-degrade. With the p95 above target: fewer candidates, no compression,
    no verification for a low-risk question (factual, staff-level FAQ/calendar/standards
    evidence), the notice first, no confidence, stored as degraded."""
    llm = FakeLLM(routing_tiebreak=lambda user: {"source_types": ["faq", "calendar"]})
    result = bot(world, llm, busy(), precise_top_k=10).ask("Assemble the file within sixty days zebraword?", "S1")
    purposes = [c["purpose"] for c in llm.calls]
    assert "verification" not in purposes and "compress" not in purposes
    assert result.status == "answered" and result.degraded and result.confidence is None
    assert result.text.startswith(REDUCED_CHECKING_NOTICE) and result.citations
    with world[0].sessions() as s:
        assert s.get(Answer, result.answer_id).degraded is True
        plan = s.scalars(select(Trace).where(Trace.query_id == result.query_id,
                                             Trace.agent == "retrieval_plan")).one()
    import json
    recorded = json.loads(plan.output)
    assert recorded["under_load"] is True and recorded["candidates_per_retriever"] == 5


def test_under_load_risky_questions_are_still_verified(world) -> None:
    """Covers: R-8-degrade. Firm policy, engagement files and non-factual questions are
    never answered without verification, load or not."""
    for question, llm in (("Our firm assembles files within forty days quaggaword?", FakeLLM()),
                          ("Alpha Fictional risk inventory quaggaword", FakeLLM()),
                          ("Compare file assembly and tax audit dates", FakeLLM(
                              query_understanding=understood(
                                  "comparison", "assemble the file within sixty days zebraword",
                                  "tax audit report due September zebraword"),
                              routing_tiebreak=lambda user: {"source_types": ["faq", "calendar"]}))):
        result = bot(world, llm, busy()).ask(question, "S1")
        assert "verification" in [c["purpose"] for c in llm.calls], question
        assert not result.degraded and not result.text.startswith(REDUCED_CHECKING_NOTICE)


def test_not_under_load_nothing_changes_and_answers_feed_the_window(world) -> None:
    window = InMemoryLatencyWindow(size=100, min_samples=20)
    llm = FakeLLM(routing_tiebreak=lambda user: {"source_types": ["faq", "calendar"]})
    result = bot(world, llm, window).ask("Assemble the file within sixty days zebraword?", "S1")
    assert "verification" in [c["purpose"] for c in llm.calls] and not result.degraded
    assert len(window.values) == 1  # this answer's time was recorded


def test_low_risk_rule() -> None:
    from app.retrieval.hybrid import RetrievedChunk

    def chunk(**kw):
        base = dict(chunk_id="X:1:1", doc_id="X", title="t", source_type="faq", section=None,
                    section_title=None, page_start=None, page_end=None, effective_date=None,
                    superseded=False, text="t", score=1, rrf_score=1, dense_rank=1, bm25_rank=1)
        return RetrievedChunk(**(base | kw))

    assert low_risk_reasons("factual", [chunk(), chunk(source_type="standards"), chunk(source_type="calendar")]) == []
    assert low_risk_reasons("procedural", [chunk()])
    assert low_risk_reasons("factual", [chunk(source_type="engagement")])
    assert low_risk_reasons("factual", [chunk(source_type="firm_policy")])
    assert low_risk_reasons("factual", [chunk(superseded=True)])
    assert low_risk_reasons("factual", [chunk(access_level="manager")])
    assert low_risk_reasons("factual", [])
