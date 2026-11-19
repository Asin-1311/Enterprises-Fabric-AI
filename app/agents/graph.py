"""The LangGraph state machine that runs one question (CLAUDE.md section 7).

    START -> understand --no_retrieval--> no_retrieval -> END
                 |
               route ----denied----> denied -> END
                 |
               plan  (light path? chunk level? candidates per retriever)
                 |
          retrieve_one x N (one per sub-query, IN PARALLEL: LangGraph fan-out) -> gather
                 |
               rerank (per sub-query, merged) --too few good chunks, first time--> followup
                 |  +-----------------------------------------------> (fan-out again)
                 |   --no good chunk--> not_found -> END
              compress (small-to-big expansion for exploratory intents; skipped on the
                 |      light path) --nothing kept--> not_found -> END
                 |
             synthesize --NOT_FOUND--> END
                 |
               verify --confidence >= 0.6--> END
                 |  +--still low after one retry--> abstain -> END
                 |
               retry (reworded, widened search; once; fan-out again) -> ...

Every node writes one masked `traces` row. `ask()` creates the `queries` row, runs the
graph and stores the `answers` and `citations` rows. Step budgets (app/agents/budgets.py)
stop a run that would pass its limits; it then ends with the best verified answer so far
or an abstention (M14a).

Smoke-test from the project root (makes real LLM calls, a few cents):
    .venv\\Scripts\\python.exe -m app.agents.graph --user S01 "Within how many days must the final audit file be assembled?"
"""

import argparse
import logging
import operator
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.agents.budgets import BudgetExceeded, StepBudget
from app.agents.llm import LLM, LLMCaller, LLMResult
from app.agents.model_router import (
    RESTRICTED_UNAVAILABLE_MESSAGE,
    ModelRouter,
    PrivateModelUnavailable,
)
from app.agents.private_llm import OllamaLLM, PrivateModelError
from app.agents.query import ClientMatcher, QueryAnalysis, understand
from app.agents.plan import RetrievalPlan, plan_for
from app.agents.reranker import Group, Ranked, compress_evidence, rerank_groups, whole_chunks
from app.agents.retriever import SubQueryResult, merge, reformulate, retrieve_one
from app.agents.router import RoutingDecision, route
from app.agents.synthesizer import (
    JUDGEMENT_NOTE,
    NOT_AVAILABLE_MESSAGE,
    DocInfo,
    Synthesis,
    synthesize,
)
from app.agents.tracing import Tracer
from app.agents.versions import ensure_active_versions
from app.agents.verifier import Verification, verify
from app.config import Settings, get_settings
from app.db.models import Answer, Citation, Document, Query, Retrieval
from app.ingest.indexer import folder_source_id
from app.retrieval.acl import UserContext, can_see, load_user
from app.retrieval.compress import CompressedChunk
from app.retrieval.spans import supporting_span
from app.retrieval.hybrid import HybridSearcher, RetrievedChunk, build_searcher
from app.retrieval.parent_expand import expand_to_sections
from app.retrieval.rerank import CrossEncoderReranker, Reranker
from app.security.injection import scan as scan_for_injection
from app.security.masking import mask_text

log = logging.getLogger(__name__)


def score_records(chunks: list[RetrievedChunk]) -> list[dict]:
    """Per chunk: the fused (RRF) score, each retriever's rank, every multiplier applied
    and the final score, for the retrieval trace (audit finding R-8-record: every
    adjustment must be recorded so its effect can be measured)."""
    return [{"chunk_id": c.chunk_id, "rrf_score": round(c.rrf_score, 6),
             "dense_rank": c.dense_rank, "keyword_rank": c.bm25_rank,
             "multipliers": {k: round(v, 6) for k, v in c.multipliers.items()},
             "final_score": round(c.score, 6)} for c in chunks]

NO_RETRIEVAL_MESSAGE = (
    "Hello. I answer questions about the Standards on Auditing (from the ICAI study material), "
    "the firm's audit methodology, policies, circulars, checklists, templates, compliance "
    "calendar and FAQ, and the engagement files you are assigned to. Every answer cites its "
    "sources. Ask me a specific question, for example: \"Within how many days must the final "
    "audit file be assembled?\""
)
ABSTAIN_MESSAGE = (
    "I could not verify enough of an answer from the available material to give it to you "
    "with confidence. Could you rephrase the question or add detail (for example the "
    "standard, policy, client or period you mean)? If you need an answer now, please raise "
    "it with your engagement manager."
)
EventCallback = Callable[[str, dict], None]


@dataclass
class AgentDeps:
    """Everything the nodes need, created once and shared across requests."""

    settings: Settings
    llm: LLMCaller
    searcher: HybridSearcher
    reranker: Reranker
    sessions: sessionmaker[Session]
    clients: ClientMatcher


class GraphState(TypedDict, total=False):
    question: str
    user: UserContext
    tracer: Tracer
    on_event: EventCallback | None
    on_token: Callable[[str], None] | None
    analysis: QueryAnalysis
    routing: RoutingDecision
    plan: RetrievalPlan
    # M14a fan-out: each retrieval round's queries, and every sub-query search result
    # (parallel branches append to this list; `gather` takes the current round's).
    round: int
    round_kind: str  # retrieval | followup_retrieval | verification_retry
    pending_queries: list[str]
    pending_sources: list[str] | None
    subquery_results: Annotated[list[SubQueryResult], operator.add]
    groups: list[Group]
    expanded: list[str]
    candidates: list[RetrievedChunk]
    injection_hits: dict[str, list[str]]
    ranked: list[Ranked]
    rerank_scores: dict[str, float]
    good_count: int
    followup_done: bool
    evidence: list[CompressedChunk]
    synthesis: Synthesis
    verification: Verification
    verify_retry_done: bool
    confidence: float | None
    status: str
    answer: str


class SubQueryTask(TypedDict):
    """What one parallel retrieval branch needs (sent with LangGraph's Send)."""

    query: str
    round: int
    user: UserContext
    source_types: list[str] | None
    time_sensitive: bool
    top_k: int
    plan: dict


@dataclass
class CitationInfo:
    chunk_id: str
    doc_id: str
    title: str
    section: str | None
    section_title: str | None
    page_start: int | None
    page_end: int | None
    superseded: bool
    # Character range of the supporting sentences in the chunk's text (M11), or None.
    span_start: int | None = None
    span_end: int | None = None


@dataclass
class AnswerResult:
    query_id: int
    answer_id: int
    status: str  # answered | abstained | not_found | denied | no_retrieval
    text: str
    citations: list[CitationInfo] = field(default_factory=list)
    superseded_involved: bool = False
    injection_hits: dict[str, list[str]] = field(default_factory=dict)
    confidence: float | None = None  # supported factual claims / factual claims
    removed_claims: list[str] = field(default_factory=list)
    claims_factual: int = 0
    claims_supported: int = 0


def _emit(state: GraphState, step: str, **payload: Any) -> None:
    if state.get("on_event"):
        state["on_event"](step, payload)


def _timed(fn: Callable[[], Any]) -> tuple[Any, float]:
    start = time.perf_counter()
    out = fn()
    return out, (time.perf_counter() - start) * 1000


class AuditAssistant:
    """Builds the graph once; `ask()` answers one question for one user."""

    def __init__(self, deps: AgentDeps) -> None:
        self.deps = deps
        self.graph = self._build()
        # Looking up (and, the first time, recording) the active versions writes rows, so
        # two questions arriving together must not both try to create version 1.
        self._versions_lock = threading.Lock()

    # ------------------------------------------------------------------- nodes

    def _understand(self, state: GraphState) -> GraphState:
        _emit(state, "understanding")
        state["tracer"].budget.check(llm_calls=1)
        # The user's own question is scanned too, not only retrieved text (audit finding
        # R-12-scan). A hit is recorded and logged; the answer pipeline is unchanged,
        # because the question is the user's instruction channel and every safeguard
        # (access filter, untrusted-data rule, verification) still applies.
        question_hits = scan_for_injection(state["question"])
        if question_hits:
            log.warning("Instruction-like text in the question of query %s: %s",
                        state["tracer"].query_id, ", ".join(question_hits))
        analysis, res = understand(state["question"], self.deps.llm, self.deps.clients)
        state["tracer"].log("query_understanding", {"question": state["question"]},
                            {**asdict(analysis), "question_injection_hits": question_hits},
                            [res])
        return {"analysis": analysis}

    def _route(self, state: GraphState) -> GraphState:
        _emit(state, "routing")
        decision, res = route(state["question"], state["analysis"], state["user"], self.deps.llm)
        state["tracer"].log("source_routing", {"question": state["question"],
                                               "analysis": asdict(state["analysis"])},
                            asdict(decision), [res] if res else [])
        return {"routing": decision}

    def _plan(self, state: GraphState) -> GraphState:
        """How much retrieval this question gets (app/agents/plan.py), then round 1."""
        state["tracer"].budget.check(retrieval_round=True)
        plan = plan_for(state["analysis"], state["routing"], self.deps.settings)
        state["tracer"].log("retrieval_plan", {"intent": state["analysis"].intent,
                                               "sub_queries": state["analysis"].sub_queries},
                            plan.record())
        return {"plan": plan, "round": 1, "round_kind": "retrieval", "groups": [],
                "candidates": [], "injection_hits": {}, "followup_done": False,
                "pending_queries": state["analysis"].sub_queries,
                "pending_sources": state["routing"].source_types}

    def _fan_out(self, state: GraphState) -> list[Send]:
        """One parallel retrieval branch per pending query (LangGraph fan-out)."""
        _emit(state, "retrieving", sources=state.get("pending_sources"),
              sub_queries=len(state["pending_queries"]))
        plan = state["plan"]
        return [Send("retrieve_one", SubQueryTask(
            query=q, round=state["round"], user=state["user"],
            source_types=state.get("pending_sources"),
            time_sensitive=state["analysis"].intent == "time_sensitive",
            top_k=plan.candidates_per_retriever, plan=plan.record()))
            for q in state["pending_queries"]]

    def _retrieve_one(self, task: SubQueryTask) -> dict:
        """One sub-query's search (runs in parallel with the others of its round)."""
        result = retrieve_one(self.deps.searcher, task["query"], task["user"], round=task["round"],
                              source_types=task["source_types"],
                              time_sensitive=task["time_sensitive"],
                              limit=self.deps.settings.retrieval_top_k, top_k=task["top_k"],
                              plan=task["plan"])
        return {"subquery_results": [result]}

    def _gather(self, state: GraphState) -> GraphState:
        """Join the round's parallel searches: groups for re-ranking, merged candidates."""
        results = [r for r in state.get("subquery_results", []) if r.round == state["round"]]
        results.sort(key=lambda r: state["pending_queries"].index(r.query)
                     if r.query in state["pending_queries"] else 0)
        merged = state.get("candidates", [])
        hits = dict(state.get("injection_hits", {}))
        for r in results:
            merged = merge(merged, r.candidates)
            hits.update(r.injection_hits)
        groups = [*state.get("groups", []), *(Group(r.query, r.candidates) for r in results)]
        started = min((r.started for r in results), default=0.0)
        state["tracer"].log(state["round_kind"], {"queries": [r.query for r in results],
                                                  "source_types": state.get("pending_sources")},
                            {"chunk_ids": [c.chunk_id for r in results for c in r.candidates],
                             "per_sub_query": [{"query": r.query, "candidates": len(r.candidates),
                                                "start_ms": round((r.started - started) * 1000, 1),
                                                "end_ms": round((r.ended - started) * 1000, 1)}
                                               for r in results],
                             "scores": [rec | {"sub_query": r.query}
                                        for r in results for rec in score_records(r.candidates)],
                             "injection_hits": {k: v for r in results for k, v in r.injection_hits.items()}},
                            latency_ms=round((max((r.ended for r in results), default=started) - started) * 1000, 1))
        self._save_retrievals(state, [step for r in results for step in r.steps],
                              state.get("pending_sources"))
        return {"candidates": merged, "injection_hits": hits, "groups": groups}

    def _save_retrievals(self, state: GraphState, steps: list[dict],
                         source_types: list[str] | None) -> None:
        """One `retrievals` row per sub-query and method, plus the fused list (M11).
        The sub-query text is masked like every stored question."""
        source_id = folder_source_id(source_types[0]) if source_types and len(source_types) == 1 \
            else None
        with self.deps.sessions() as s:
            s.add_all(Retrieval(query_id=state["tracer"].query_id, sub_query=mask_text(step["sub_query"]),
                                source_id=source_id, method=step["method"], scores=step["scores"])
                      for step in steps)
            s.commit()

    def _rerank(self, state: GraphState) -> GraphState:
        _emit(state, "reranking", candidates=len(state["candidates"]))
        s = self.deps.settings
        known = state.get("rerank_scores", {})
        (ranked, good, scores), ms = _timed(lambda: rerank_groups(
            state["groups"], self.deps.reranker, s.rerank_top_n, s.good_chunk_threshold, known))
        state["tracer"].log("rerank", {"candidates": len(state["candidates"]),
                                       "groups": [g.query for g in state["groups"]],
                                       "newly_scored": len(scores) - len(known)},
                            {"top": [(r.chunk.chunk_id, round(r.rerank_score, 4), r.query)
                                     for r in ranked], "good_count": good}, latency_ms=ms)
        return {"ranked": ranked, "good_count": good, "rerank_scores": scores}

    def _followup(self, state: GraphState) -> GraphState:
        _emit(state, "followup_retrieval")
        state["tracer"].budget.check(llm_calls=1, retrieval_round=True)
        queries, res = reformulate(state["question"], self.deps.llm)
        state["tracer"].log("reformulate", {"question": state["question"]}, {"queries": queries}, [res])
        # The follow-up widens to every source type the user may see.
        return {"followup_done": True, "round": state["round"] + 1,
                "round_kind": "followup_retrieval", "pending_queries": queries,
                "pending_sources": None}

    def _compress(self, state: GraphState) -> GraphState:
        """Small-to-big for exploratory questions, then compression (skipped on the light
        path, where the good chunks go to synthesis whole)."""
        _emit(state, "compressing")
        plan, s = state["plan"], self.deps.settings
        ranked, expanded = state["ranked"], []
        if plan.chunk_level == "parent":
            ranked, expanded = expand_to_sections(ranked, self.deps.searcher, state["user"],
                                                  s.parent_expansion_max)
        if plan.compress:
            state["tracer"].budget.check(llm_calls=1)
            evidence, res = compress_evidence(state["question"], ranked, self.deps.llm,
                                              s.good_chunk_threshold)
        else:
            evidence, res = whole_chunks(ranked, s.good_chunk_threshold), None
        state["tracer"].log("compress", {"chunks": [r.chunk.chunk_id for r in ranked],
                                         "expanded": expanded, "light_path": plan.light_path},
                            {"kept": {e.chunk_id: len(e.sentences) for e in evidence}},
                            [res] if res else [])
        return {"evidence": evidence, "ranked": ranked, "expanded": expanded}

    def _synthesize(self, state: GraphState) -> GraphState:
        _emit(state, "synthesizing")
        state["tracer"].budget.check(llm_calls=1)
        chunks = {r.chunk.chunk_id: r.chunk for r in state["ranked"]}
        docs = self._doc_info({chunks[e.chunk_id].doc_id for e in state["evidence"]}, state["user"])
        synthesis, res = synthesize(state["question"], state["evidence"], chunks, docs,
                                    self.deps.llm, on_text=state.get("on_token"))
        state["tracer"].log("synthesis", {"evidence": [e.chunk_id for e in state["evidence"]]},
                            {"status": synthesis.status, "text": synthesis.text,
                             "invalid_citations": synthesis.invalid_citations,
                             "superseded_involved": synthesis.superseded_involved}, [res])
        return {"synthesis": synthesis, "status": synthesis.status, "answer": synthesis.text}

    def _verify(self, state: GraphState) -> GraphState:
        _emit(state, "verifying")
        state["tracer"].budget.check(llm_calls=1)
        draft = state["synthesis"].text.removesuffix(JUDGEMENT_NOTE).rstrip()
        chunks = {r.chunk.chunk_id: r.chunk for r in state["ranked"]}
        docs = self._doc_info({c.doc_id for c in chunks.values()}, state["user"])
        v, res = verify(state["question"], draft, chunks, self.deps.llm, docs,
                        [e.chunk_id for e in state["evidence"]])
        state["tracer"].log(
            "verification",
            {"claims": [{"id": c.id, "text": c.text, "cites": c.citations} for c in v.claims]},
            {"verdicts": v.verdicts, "confidence": v.confidence, "factual": v.factual,
             "supported": v.supported, "removed": v.removed, "repaired": v.repaired},
            [res] if res else [])
        answer = f"{v.text}\n\n{JUDGEMENT_NOTE}" if v.text else ""
        return {"verification": v, "confidence": v.confidence, "answer": answer}

    def _retry(self, state: GraphState) -> GraphState:
        """Low confidence: ONE loop back to retrieval with reworded, widened queries."""
        _emit(state, "retrying_retrieval", confidence=state["confidence"])
        state["tracer"].budget.check(llm_calls=1, retrieval_round=True)
        queries, res = reformulate(state["question"], self.deps.llm)
        state["tracer"].log("reformulate", {"question": state["question"],
                                            "confidence": state["confidence"]},
                            {"queries": queries}, [res])
        return {"verify_retry_done": True, "followup_done": True, "round": state["round"] + 1,
                "round_kind": "verification_retry", "pending_queries": queries,
                "pending_sources": None}

    def _abstain(self, state: GraphState) -> GraphState:
        state["tracer"].log("abstain", {"confidence": state.get("confidence")},
                            {"answer": "clarify_or_escalate"})
        return {"status": "abstained", "answer": ABSTAIN_MESSAGE}

    def _no_retrieval(self, state: GraphState) -> GraphState:
        state["tracer"].log("no_retrieval", {"question": state["question"]}, {"answer": "canned"})
        return {"status": "no_retrieval", "answer": NO_RETRIEVAL_MESSAGE}

    def _denied(self, state: GraphState) -> GraphState:
        # Same wording as "not found": never confirm that restricted material exists.
        state["tracer"].log("access_denied", {"question": state["question"]},
                            {"engagements": list(state["routing"].denied_engagements)})
        return {"status": "denied", "answer": NOT_AVAILABLE_MESSAGE}

    def _not_found(self, state: GraphState) -> GraphState:
        state["tracer"].log("not_found", {"question": state["question"]},
                            {"good_count": state.get("good_count", 0)})
        return {"status": "not_found", "answer": NOT_AVAILABLE_MESSAGE}

    # ------------------------------------------------------------------- edges

    @staticmethod
    def _after_understand(state: GraphState) -> str:
        return "no_retrieval" if state["analysis"].intent == "no_retrieval" else "route"

    @staticmethod
    def _after_route(state: GraphState) -> str:
        return "denied" if state["routing"].denied else "plan"

    def _after_rerank(self, state: GraphState) -> str:
        if state["good_count"] < self.deps.settings.min_good_chunks and not state["followup_done"] \
                and state["tracer"].budget.allows(llm_calls=1, retrieval_round=True):
            return "followup"
        return "compress" if state["good_count"] > 0 else "not_found"

    @staticmethod
    def _after_compress(state: GraphState) -> str:
        return "synthesize" if state["evidence"] else "not_found"

    @staticmethod
    def _after_synthesize(state: GraphState) -> str:
        return "verify" if state["status"] == "answered" else END

    def _after_verify(self, state: GraphState) -> str:
        v = state["verification"]
        if v.confidence >= self.deps.settings.confidence_threshold and v.cited_ids:
            return END
        if state.get("verify_retry_done") or not state["tracer"].budget.allows(
                llm_calls=4, retrieval_round=True):  # reformulate, compress, synthesize, verify
            return "abstain"
        return "retry"

    def _build(self):
        g = StateGraph(GraphState)
        for name, fn in [("understand", self._understand), ("route", self._route),
                         ("plan", self._plan), ("retrieve_one", self._retrieve_one),
                         ("gather", self._gather), ("rerank", self._rerank),
                         ("followup", self._followup), ("compress", self._compress),
                         ("synthesize", self._synthesize), ("verify", self._verify),
                         ("retry", self._retry), ("abstain", self._abstain),
                         ("no_retrieval", self._no_retrieval),
                         ("denied", self._denied), ("not_found", self._not_found)]:
            g.add_node(name, self._snapshotting(fn) if name != "retrieve_one" else fn)
        g.add_edge(START, "understand")
        g.add_conditional_edges("understand", self._after_understand, ["no_retrieval", "route"])
        g.add_conditional_edges("route", self._after_route, ["denied", "plan"])
        for fan_out_from in ("plan", "followup", "retry"):  # one branch per sub-query
            g.add_conditional_edges(fan_out_from, self._fan_out, ["retrieve_one"])
        g.add_edge("retrieve_one", "gather")
        g.add_edge("gather", "rerank")
        g.add_conditional_edges("rerank", self._after_rerank, ["followup", "compress", "not_found"])
        g.add_conditional_edges("compress", self._after_compress, ["synthesize", "not_found"])
        g.add_conditional_edges("synthesize", self._after_synthesize, ["verify", END])
        g.add_conditional_edges("verify", self._after_verify, [END, "retry", "abstain"])
        for terminal in ("abstain", "no_retrieval", "denied", "not_found"):
            g.add_edge(terminal, END)
        return g.compile()

    @staticmethod
    def _snapshotting(fn: Callable[[GraphState], GraphState]) -> Callable[[GraphState], GraphState]:
        """Keep the latest state on the tracer, so a run stopped by its budget can still
        answer with what it verified (M14a)."""
        def node(state: GraphState) -> GraphState:
            out = fn(state)
            state["tracer"].snapshot.update({**state, **(out or {})})
            return out
        node.__name__ = getattr(fn, "__name__", "node")
        return node

    # ----------------------------------------------------------------- helpers

    def _doc_info(self, doc_ids: set[str], user: UserContext) -> dict[str, DocInfo]:
        """Title/version/dates of evidence documents and their supersede links. A link to a
        document the user may not see is omitted, so its existence is not revealed."""
        with self.deps.sessions() as s:
            docs = {d.doc_id: d for d in s.scalars(select(Document))}
        out = {}
        for doc_id in doc_ids:
            d = docs[doc_id]
            newer = next((n for n in docs.values() if n.supersedes == doc_id), None)
            older = docs.get(d.supersedes) if d.supersedes else None
            visible = lambda x: x is not None and can_see(user, x.access_level, x.engagement_id)  # noqa: E731
            out[doc_id] = DocInfo(
                title=d.title, version=d.version, effective_date=d.effective_date,
                source_type=d.source_type,
                superseded_by=(newer.doc_id, newer.effective_date) if visible(newer) else None,
                supersedes=(older.doc_id, older.effective_date) if visible(older) else None)
        return out

    # --------------------------------------------------------------------- run

    def _budget_stop(self, tracer: Tracer, exc: BudgetExceeded) -> GraphState:
        """End a run that hit its budget: the best verified answer so far (only supported
        claims, with their confidence), else an abstention. Never an unverified draft."""
        log.warning("Query %s stopped by its %s budget: %s", tracer.query_id, exc.limit, exc.usage)
        state: GraphState = dict(tracer.snapshot)  # type: ignore[assignment]
        v = state.get("verification")
        verified = v is not None and v.cited_ids and v.supported > 0
        tracer.log("budget_exceeded", {"limit": exc.limit, "usage": exc.usage},
                   {"ended_with": "best_verified_answer" if verified else "abstention"})
        if verified:
            state.update(status="answered", answer=f"{v.text}\n\n{JUDGEMENT_NOTE}",
                         confidence=v.confidence)
        else:
            state.update(status="abstained", answer=ABSTAIN_MESSAGE, verification=None)
        return state

    def ask(self, question: str, user_id: str, on_event: EventCallback | None = None,
            on_token: Callable[[str], None] | None = None) -> AnswerResult:
        """Answer `question` for `user_id`; stores query, traces, answer and citations."""
        with self._versions_lock:
            versions = ensure_active_versions(self.deps.sessions)
        with self.deps.sessions() as s:
            user = load_user(s, user_id)
            q = Query(user_id=user_id, text=mask_text(question),
                      injection_hits=scan_for_injection(question))
            s.add(q)
            s.commit()
            query_id = q.id
        s = self.deps.settings
        tracer = Tracer(self.deps.sessions, query_id, versions, StepBudget(
            s.budget_max_llm_calls, s.budget_max_tokens, s.budget_max_seconds,
            s.budget_max_retrieval_rounds))
        try:
            state: GraphState = self.graph.invoke({
                "question": question, "user": user, "tracer": tracer,
                "on_event": on_event, "on_token": on_token})
        except BudgetExceeded as exc:
            state = self._budget_stop(tracer, exc)
        except (PrivateModelUnavailable, PrivateModelError) as exc:
            # Restricted evidence and no private model to read it: fail closed (M13a).
            log.warning("Question %s needs restricted material: %s", query_id, exc)
            state = {"status": "restricted_unavailable", "answer": RESTRICTED_UNAVAILABLE_MESSAGE}

        synthesis = state.get("synthesis")
        v: Verification | None = state.get("verification")
        chunks = {r.chunk.chunk_id: r.chunk for r in state.get("ranked", [])}
        cited = v.cited_ids if v is not None and state["status"] == "answered" else []
        evidence = {e.chunk_id: e.sentences for e in state.get("evidence", [])}
        citations = []
        for cid in (c for c in cited if c in chunks):
            span = supporting_span(chunks[cid].text, evidence.get(cid, []))
            citations.append(CitationInfo(
                cid, chunks[cid].doc_id, chunks[cid].title, chunks[cid].section,
                chunks[cid].section_title, chunks[cid].page_start, chunks[cid].page_end,
                chunks[cid].superseded, *(span or (None, None))))
        confidence = state.get("confidence")
        with self.deps.sessions() as s:
            answer = Answer(query_id=query_id, text=state["answer"], confidence=confidence,
                            status=state["status"])
            s.add(answer)
            s.flush()
            s.add_all(Citation(answer_id=answer.id, chunk_id=c.chunk_id, span_start=c.span_start,
                               span_end=c.span_end) for c in citations)
            if state.get("analysis") is not None:
                s.get(Query, query_id).intent = state["analysis"].intent
            s.commit()
            answer_id = answer.id
        return AnswerResult(
            query_id, answer_id, state["status"], state["answer"], citations,
            bool(synthesis and synthesis.superseded_involved and state["status"] == "answered"),
            state.get("injection_hits", {}), confidence,
            v.removed if v else [], v.factual if v else 0, v.supported if v else 0)


def build_assistant(settings: Settings) -> AuditAssistant:
    """Assistant wired to the real indexes, re-ranker and LLM client."""
    from app.llm_client import make_client

    searcher = build_searcher(settings)
    with searcher.sessions() as s:
        clients = ClientMatcher.from_db(s)
    # Every request goes through the model router: restricted text reaches only the
    # private model (PRIVATE_MODEL on Ollama), or nothing if none is configured (M13a).
    private = OllamaLLM(settings.private_model, settings.ollama_url) if settings.private_model else None
    llm = ModelRouter(LLM(make_client(settings), settings), private)
    return AuditAssistant(AgentDeps(
        settings=settings, llm=llm, searcher=searcher,
        reranker=CrossEncoderReranker(settings.reranker_model), sessions=searcher.sessions,
        clients=clients))


def main() -> int:
    parser = argparse.ArgumentParser(description="Ask the assistant one question (live LLM calls).")
    parser.add_argument("question")
    parser.add_argument("--user", required=True, help="synthetic user id, e.g. S01")
    args = parser.parse_args()
    assistant = build_assistant(get_settings())
    result = assistant.ask(args.question, args.user,
                           on_event=lambda step, _: print(f"  ... {step}", flush=True))
    conf = f"  confidence: {result.confidence:.0%}" if result.confidence is not None else ""
    print(f"\nstatus: {result.status}{conf}\n\n{result.text}\n")
    if result.removed_claims:
        print(f"  removed as unverified: {result.removed_claims}")
    for c in result.citations:
        pages = f", p.{c.page_start}" + (f"-{c.page_end}" if c.page_end != c.page_start else "") \
            if c.page_start else ""
        flag = " [superseded]" if c.superseded else ""
        print(f"  [{c.chunk_id}] {c.title}, §{c.section} {c.section_title or ''}{pages}{flag}")
    if result.superseded_involved:
        print("  (superseded policy involved)")
    if result.injection_hits:
        print(f"  injection patterns found in retrieved text: {result.injection_hits}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
