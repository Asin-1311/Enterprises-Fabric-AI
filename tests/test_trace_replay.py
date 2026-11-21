"""M18b: trace replay (eval/trace_replay.py). The whole agent pipeline is recorded over the
synthetic test corpus with a scripted model, then replayed with no model at all: a run that
builds the same requests reproduces the outcome; any change to what is sent is caught.

Covers: R-13.3-replay (recorded traces of inputs, retrieved chunk ids and model responses
are replayed, and a drop in the replay score fails the gate).
"""

from datetime import date

import pytest

from app.agents.graph import AgentDeps, AuditAssistant
from app.agents.model_router import ModelRouter
from eval import trace_replay as tr
from tests.test_agents import FakeLLM, FakeReranker, _settings
from tests.test_model_routing import corpus  # noqa: F401  (the synthetic-corpus fixture)
from tests.helpers import make_searcher

QUESTIONS = [
    {"id": "t1", "user_id": "S02", "question": "How many items must we sample for a test of controls?"},
    {"id": "t2", "user_id": "S03", "question": "Which NPA classification checks are on our bank branch audit checklist?"},
    {"id": "t3", "user_id": "S01", "question": "Within how many days must the final audit file be assembled?"},
]
DAY = date(2026, 11, 21)


class InvertedReranker(FakeReranker):
    """A changed re-ranker: the least similar passages first."""

    def score(self, query: str, passages):
        return [1.0 - s for s in super().score(query, passages)]


def _assistant(corpus, reranker=None) -> AuditAssistant:  # noqa: F811
    indexer, clients, _ = corpus
    deps = AgentDeps(settings=_settings(), llm=ModelRouter(FakeLLM(), None),
                     searcher=make_searcher(indexer, top_k=20), reranker=reranker or FakeReranker(),
                     sessions=indexer.sessions, clients=clients)
    return AuditAssistant(deps)


@pytest.fixture(scope="module")
def traces(corpus):  # noqa: F811
    assistant = _assistant(corpus)
    retrieved = tr.instrument(assistant)
    return [tr.record_one(q, assistant, retrieved, DAY) for q in QUESTIONS]


def test_a_recorded_trace_replays_without_any_model(traces, corpus) -> None:  # noqa: F811
    """Covers: R-13.3-replay (the recorded inputs, chunk ids and responses reproduce the outcome)."""
    assert all(t["calls"] and t["retrieved"] for t in traces)
    assert {c["purpose"] for t in traces for c in t["calls"]} >= {"query_understanding", "synthesis", "verification"}
    assistant = _assistant(corpus)
    assistant.deps.llm = None  # nothing to fall back on: every answer comes from the trace
    retrieved = tr.instrument(assistant)
    rows = [tr.replay_one(t, assistant, retrieved) for t in traces]
    assert all(r["reproduced"] for r in rows), rows


def test_any_change_to_what_is_sent_is_caught(traces, corpus) -> None:  # noqa: F811
    changed = _assistant(corpus, InvertedReranker())  # different evidence, so different requests
    retrieved = tr.instrument(changed)
    rows = [tr.replay_one(t, changed, retrieved) for t in traces]
    assert not all(r["reproduced"] for r in rows)
    assert any("no recorded response" in r["reason"] for r in rows)

    tampered = {**traces[0], "outcome": {**traces[0]["outcome"], "text": "a different answer"}}
    assistant = _assistant(corpus)
    retrieved = tr.instrument(assistant)
    row = tr.replay_one(tampered, assistant, retrieved)
    assert not row["reproduced"] and "different outcome" in row["reason"]


def test_the_trace_replay_gate() -> None:
    """Covers: R-13.3-replay (a lower replay score than the baseline fails, within tolerance)."""
    baseline = {"values": {"score": 1.0, "traces": 20}}
    assert tr.gate(1.0, 20, baseline, 0.05) == []
    assert tr.gate(0.95, 20, baseline, 0.05) == []  # one trace in twenty
    assert tr.gate(0.90, 20, baseline, 0.05)
    assert tr.gate(1.0, 19, baseline, 0.05)  # a changed trace set needs a new baseline


def test_replay_answers_only_the_exact_recorded_request() -> None:
    calls = [{"purpose": "synthesis", "request": tr.request_hash("synthesis", "s", "u"), "text": "T",
              "data": None, "model": "m", "effort": "medium"}]
    llm = tr.ReplayLLM(calls)
    streamed = []
    assert llm.call(purpose="synthesis", role="synth", system="s", user="u", max_tokens=1,
                    on_text=streamed.append).text == "T" and streamed == ["T"]
    with pytest.raises(tr.ReplayMismatch):
        llm.call(purpose="synthesis", role="synth", system="s", user="u2", max_tokens=1)
