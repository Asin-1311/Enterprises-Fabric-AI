"""M14a: query understanding, the light path, parallel retrieval, per-sub-query re-ranking
and compression, small-to-big expansion, step budgets and what retrieval records.

Covers: R-7.3, R-5-par, R-7.6, R-8-record, R-3-adapt (chunk level and candidate counts
per intent; per-intent retriever weights follow in M15).
"""

import json
import threading
import time
from pathlib import Path

import pytest
from sqlalchemy import select

from app.agents.budgets import BudgetExceeded, BudgetTracker, StepBudget
from app.agents.graph import AgentDeps, AuditAssistant
from app.agents.llm import LLMResult
from app.agents.plan import plan_for
from app.agents.query import EXPLORATORY, INTENTS, SYSTEM as QUERY_SYSTEM, ClientMatcher, understand
from app.agents.reranker import Group, rerank, rerank_groups
from app.agents.router import RoutingDecision
from app.db.models import Retrieval, Trace
from app.retrieval.acl import UserContext
from tests.helpers import make_indexer, make_searcher, write_doc, write_manifest, write_people
from tests.test_agents import ENGAGEMENTS, USERS, FakeLLM, FakeReranker, _settings

BANNER_DOCS = {
    "CIRC-2425": ("circulars/bank_branch_fy2425.md", "2024-04-01", "",
                  "# Bank Branch Audit Circular FY 2024-25\n\n## 1. Scope\nFor FY 2024-25 the "
                  "branch audit covers advances above fifty lakh and quokkaword items.\n\n"
                  "## 2. Reporting\nFY 2024-25 reports are due in May."),
    "CIRC-2526": ("circulars/bank_branch_fy2526.md", "2025-04-01", "CIRC-2425",
                  "# Bank Branch Audit Circular FY 2025-26\n\n## 1. Scope\nFor FY 2025-26 the "
                  "branch audit covers advances above one crore and wombatword items.\n\n"
                  "## 2. Reporting\nFY 2025-26 reports are due in April.\n\n"
                  "### 2.1 Format\nUse the new reporting format, ocelotword.\n\n"
                  "### 2.2 Sign-off\nThe branch partner signs the report."),
}
for i in range(6):  # many chunks that match the WHOLE question's words, to crowd one side out
    BANNER_DOCS[f"NOTE-{i}"] = (f"firm_policy/note{i}.md", "2025-01-01", "",
                                f"# Branch audit note {i}\n\n## 1. Branch audit\nBank branch audit "
                                f"circular changes between years, branch audit circular note {i}.")


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    root: Path = tmp_path_factory.mktemp("plane")
    (root / "data").mkdir()
    write_people(root, USERS, ENGAGEMENTS)
    rows = []
    for doc_id, (rel, eff, sup, body) in BANNER_DOCS.items():
        write_doc(root, rel, body)
        rows.append(dict(doc_id=doc_id, path=f"data/corpus/{rel}", source_type=rel.split("/")[0],
                         title=doc_id, version="1", effective_date=eff, supersedes=sup,
                         access_level="staff", synthetic="true"))
    write_manifest(root, rows)
    indexer = make_indexer(root)
    indexer.sync(actor="test")
    with indexer.sessions() as s:
        clients = ClientMatcher.from_db(s)
    return indexer, clients


def assistant(world, llm: FakeLLM, **settings) -> AuditAssistant:
    indexer, clients = world
    deps = AgentDeps(settings=_settings().model_copy(update=settings), llm=llm,
                     searcher=make_searcher(indexer, top_k=20), reranker=FakeReranker(),
                     sessions=indexer.sessions, clients=clients)
    return AuditAssistant(deps)


def understood(intent: str, *subs: str):
    return lambda user: {"intent": intent, "sa_numbers": [], "clients": [], "fs_items": [],
                         "dates_or_years": [], "sub_queries": list(subs) or [user.removeprefix("Question: ")]}


def traces(world, query_id: int) -> dict[str, dict]:
    with world[0].sessions() as s:
        return {t.agent: json.loads(t.output) for t in s.scalars(
            select(Trace).where(Trace.query_id == query_id).order_by(Trace.id))}


COMPARE = "How did the bank branch audit circular change between FY 2024-25 and FY 2025-26?"
SIDES = ("FY 2024-25 bank branch circular scope advances quokkaword",
         "FY 2025-26 bank branch circular scope advances wombatword")


# ------------------------------------------------------------- query understanding

@pytest.mark.parametrize("intent", INTENTS)
def test_every_one_of_the_seven_intents_is_understood(intent: str) -> None:
    """Covers: R-7.3 (all seven intents, incl. analytical and open-ended)."""
    assert len(INTENTS) == 7 and {"analytical", "open_ended"} <= set(INTENTS)
    assert intent in QUERY_SYSTEM  # the model is told what each intent means
    analysis, _ = understand(f"a question ({intent})", FakeLLM(query_understanding=understood(intent)),
                             ClientMatcher({}))
    assert analysis.intent == intent


def test_an_unknown_intent_falls_back_to_factual() -> None:
    analysis, _ = understand("q", FakeLLM(query_understanding=understood("gossip")), ClientMatcher({}))
    assert analysis.intent == "factual"


def test_the_plan_depends_on_the_intent() -> None:
    """Covers: R-3-adapt (chunk level and candidate count chosen per question)."""
    from app.agents.query import QueryAnalysis

    s = _settings()
    rules = RoutingDecision(["firm_policy"], ["policy"], False)
    simple = plan_for(QueryAnalysis("factual", [], [], sub_queries=["q"]), rules, s)
    assert (simple.light_path, simple.compress, simple.candidates_per_retriever, simple.chunk_level) == \
        (True, False, s.light_top_k, "child")
    for intent in EXPLORATORY:
        p = plan_for(QueryAnalysis(intent, [], [], sub_queries=["a", "b"]), rules, s)
        assert (p.light_path, p.chunk_level, p.candidates_per_retriever) == (False, "parent", s.retrieval_top_k)
    tiebreak = RoutingDecision(["standards"], ["tiebreak"], True)
    assert not plan_for(QueryAnalysis("factual", [], [], sub_queries=["q"]), tiebreak, s).light_path
    assert not plan_for(QueryAnalysis("factual", [], ["ENG-A"], sub_queries=["q"]), rules, s).light_path


# ------------------------------------------------------------------ parallelism

def test_sub_query_searches_run_in_parallel(world) -> None:
    """Covers: R-5-par. Each sub-query is its own LangGraph branch; their searches overlap
    in time (each is slowed to 0.4 s; run one after another they could not overlap)."""
    llm = FakeLLM(query_understanding=understood("comparison", *SIDES))
    bot = assistant(world, llm)
    searcher = bot.deps.searcher
    real, windows, lock = searcher.search, [], threading.Lock()

    def slow_search(queries, user, **kwargs):
        start = time.monotonic()
        time.sleep(0.4)
        out = real(queries, user, **kwargs)
        with lock:
            windows.append((queries[0], start, time.monotonic()))
        return out

    searcher.search = slow_search  # type: ignore[method-assign]
    begun = time.monotonic()
    result = bot.ask(COMPARE, "S1")
    assert result.status == "answered"
    first = sorted(w for w in windows if w[0] in SIDES)[:2]
    assert len(first) == 2
    (_, s1, e1), (_, s2, e2) = first
    assert s1 < e2 and s2 < e1  # the two windows overlap
    rounds = traces(world, result.query_id)["retrieval"]["per_sub_query"]
    assert len(rounds) == 2 and time.monotonic() - begun < 5


def test_dense_and_keyword_search_of_one_sub_query_overlap(world) -> None:
    """Covers: R-5-par (inside one sub-query)."""
    searcher = make_searcher(world[0], top_k=5)
    windows, lock = {}, threading.Lock()

    def slowed(method, fn):
        def run(*args, **kwargs):
            start = time.monotonic()
            time.sleep(0.3)
            out = fn(*args, **kwargs)
            with lock:
                windows[method] = (start, time.monotonic())
            return out
        return run

    searcher.store.search_scoped = slowed("dense", searcher.store.search_scoped)  # type: ignore[method-assign]
    searcher.bm25.search_scoped = slowed("keyword", searcher.bm25.search_scoped)  # type: ignore[method-assign]
    searcher.search("bank branch circular", UserContext("S1", "staff", frozenset()))
    (ds, de), (ks, ke) = windows["dense"], windows["keyword"]
    assert ds < ke and ks < de


# ------------------------------------------------ per-sub-query re-ranking + compression

def test_per_sub_query_reranking_keeps_both_sides_of_a_comparison(world) -> None:
    """Covers: R-7.6 (re-ranking). Ranked against the whole question, the many generic
    'branch audit circular' notes crowd one year out; per sub-query, both years survive."""
    searcher = make_searcher(world[0], top_k=20)
    user = UserContext("S1", "staff", frozenset())
    groups = [Group(q, searcher.search([q], user, limit=20)) for q in SIDES]
    whole, _, _ = rerank(COMPARE, [c for g in groups for c in g.candidates], FakeReranker(), 3, 0.0)
    split, _, _ = rerank_groups(groups, FakeReranker(), 3, 0.0)
    docs = lambda ranked: {r.chunk.doc_id for r in ranked}  # noqa: E731
    assert not {"CIRC-2425", "CIRC-2526"} <= docs(whole)    # the problem this fixes
    assert {"CIRC-2425", "CIRC-2526"} <= docs(split)
    assert {r.query for r in split} == set(SIDES)


def test_both_sides_survive_reranking_and_compression_in_the_pipeline(world) -> None:
    """Covers: R-7.6. Through the whole graph: the compression request names each chunk's
    sub-query, and the evidence that reaches synthesis covers both years."""
    llm = FakeLLM(query_understanding=understood("comparison", *SIDES))
    result = assistant(world, llm, rerank_top_n=3).ask(COMPARE, "S1")
    compress_call = next(c for c in llm.calls if c["purpose"] == "compress")
    assert all(f'sub_query="{q}"' in compress_call["user"] for q in SIDES)
    synthesis = next(c for c in llm.calls if c["purpose"] == "synthesis")
    assert "quokkaword" in synthesis["user"] and "wombatword" in synthesis["user"]
    assert result.status == "answered"


# ------------------------------------------------------------- small-to-big, light path

def test_exploratory_questions_expand_to_the_section(world) -> None:
    """Small-to-big: for a comparison, the matched chunk's section (its parent lead and
    sibling windows) is added before compression, within the configured cap."""
    llm = FakeLLM(query_understanding=understood("comparison", "new reporting format ocelotword"))
    result = assistant(world, llm, parent_expansion_max=2).ask("What changed in reporting format?", "S1")
    with world[0].sessions() as s:
        row = s.scalars(select(Trace).where(Trace.query_id == result.query_id,
                                            Trace.agent == "compress")).one()
    added = json.loads(row.input)["expanded"]  # chunks added by small-to-big
    kept = json.loads(row.output)["kept"]      # what compression kept
    assert added and len(added) <= 2 and all(cid.startswith("CIRC-2526:") for cid in added)
    assert kept


def test_the_light_path_skips_compression_and_searches_fewer_candidates(world) -> None:
    """Covers: R-3-adapt, R-8-record: the plan is recorded with every fused retrieval row."""
    llm = FakeLLM()  # factual, one sub-query
    result = assistant(world, llm, light_top_k=4).ask("What does our FY 2025-26 branch audit circular say about scope?", "S1")
    assert "compress" not in [c["purpose"] for c in llm.calls] and result.status == "answered"
    plan = traces(world, result.query_id)["retrieval_plan"]
    assert plan["light_path"] is True and plan["candidates_per_retriever"] == 4
    with world[0].sessions() as s:
        rows = s.scalars(select(Retrieval).where(Retrieval.query_id == result.query_id)).all()
    dense = [r for r in rows if r.method == "dense"]
    assert dense and all(len(r.scores) <= 4 for r in dense)  # 4 candidates per retriever
    fused = [r for r in rows if r.method == "fused"]
    assert fused and all(e["plan"]["light_path"] and e["plan"]["chunk_level"] == "child"
                         for r in fused for e in r.scores)


# --------------------------------------------------------------------- step budgets

def result_of(tokens: int) -> LLMResult:
    return LLMResult("x", None, "m", "low", tokens, 0, 1.0)


def test_budget_tracker_limits() -> None:
    t = BudgetTracker(StepBudget(max_llm_calls=2, max_tokens=100, max_seconds=60, max_retrieval_rounds=1))
    t.check(llm_calls=1)
    t.charge([result_of(40)])
    t.check(retrieval_round=True)
    assert not t.allows(retrieval_round=True)
    with pytest.raises(BudgetExceeded) as rounds:
        t.check(retrieval_round=True)
    assert rounds.value.limit == "retrieval_rounds"
    t.charge([result_of(70)])
    with pytest.raises(BudgetExceeded) as tokens:
        t.check(llm_calls=1)
    assert tokens.value.limit == "tokens"
    slow = BudgetTracker(StepBudget(max_seconds=0.01))
    time.sleep(0.05)
    with pytest.raises(BudgetExceeded):
        slow.check()


def test_a_run_over_budget_abstains_and_is_logged(world) -> None:
    """Too few LLM calls allowed to reach synthesis: no unverified draft is returned."""
    llm = FakeLLM(query_understanding=understood("comparison", *SIDES))
    result = assistant(world, llm, budget_max_llm_calls=2).ask(COMPARE, "S1")
    assert result.status == "abstained" and "synthesis" not in [c["purpose"] for c in llm.calls]
    stop = traces(world, result.query_id)["budget_exceeded"]
    assert stop["ended_with"] == "abstention"


def test_a_budget_stop_after_verification_keeps_the_verified_answer(world, monkeypatch) -> None:
    """If the budget runs out on the retry after verification, the verified claims so far
    are the answer (with their confidence), never the unverified draft."""
    def half_supported(user: str) -> dict:
        import re
        ids = [int(i) for i in re.findall(r"\[claim (\d+)\]", user)]
        return {"claims": [{"id": i, "type": "factual",
                            "verdict": "supported" if n == 0 else "unsupported", "reason": "r"}
                           for n, i in enumerate(ids)]}

    def two_claims(user: str) -> str:
        import re
        cites = re.findall(r'cite="([^"]+)"', user)
        return f"First fact here [{cites[0]}]. Second fact here [{cites[-1]}]. Third fact [{cites[0]}]."

    llm = FakeLLM(verification=half_supported, synthesis=two_claims)
    bot = assistant(world, llm)
    monkeypatch.setattr(BudgetTracker, "allows", lambda self, **kw: True)  # take the retry path
    real_check = BudgetTracker.check

    def no_more_rounds(self, *, llm_calls=0, retrieval_round=False):
        if retrieval_round and self.retrieval_rounds >= 1:
            raise BudgetExceeded("retrieval_rounds", self.usage())
        return real_check(self, llm_calls=llm_calls, retrieval_round=retrieval_round)

    monkeypatch.setattr(BudgetTracker, "check", no_more_rounds)
    result = bot.ask("When are FY 2025-26 reports due?", "S1")
    assert result.status == "answered" and 0 < result.confidence < 0.6
    assert "First fact here" in result.text and "Second fact here" not in result.text
    assert traces(world, result.query_id)["budget_exceeded"]["ended_with"] == "best_verified_answer"
