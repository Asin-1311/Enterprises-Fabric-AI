"""M15b: the routing bandit, rewards from the logs and implicit signals, versioned policies
and the replay gate on activation.

Covers: R-8-bandit, R-7.4, R-7.9 (signals change the rewards; the re-ranker dataset is
M15c), R-10-policy, R-13.3-replay (the gate; CI runs it in M17).
"""

import json
import random
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app.adaptive.bandit import MIN_CANDIDATES, BanditState, allocate, arm_key
from app.adaptive.replay import load_records, replay_report
from app.adaptive.routing_policy import SIGNAL_REWARDS, Observation, learn, observations, propose
from app.agents.graph import AgentDeps, AuditAssistant
from app.agents.query import ClientMatcher
from app.api.main import Services, create_app
from app.concurrency import ReadWriteLock
from app.db.models import Answer, Feedback, FeedbackSignal, Query, Retrieval, RoutingPolicy, Trace, User
from app.ingest.manifest import read_manifest
from app.security.passwords import hash_password
from tests.helpers import make_indexer, make_searcher
from tests.test_agents import FakeLLM, FakeReranker, _settings
from tests.test_clean_clone import DATA, _clean_clone

SOURCES = ("standards", "circulars", "faq")


# --------------------------------------------------------------------- the bandit

def test_allocation_shifts_toward_the_rewarded_arm_in_a_seeded_simulation() -> None:
    """Covers: R-8-bandit. Arms succeed 80% / 30% / 20% of the time (unknown to the
    bandit). Starting from no knowledge, the allocation moves toward the best arm."""
    rng = random.Random(7)
    truth = {"standards": 0.8, "circulars": 0.3, "faq": 0.2}
    state = BanditState()
    first = allocate(state, "factual", SOURCES, 20, random.Random(1), greedy=True)
    assert {first[arm_key(st, "dense")] for st in SOURCES} == {6, 7}  # even split, no knowledge
    for _ in range(300):
        alloc = allocate(state, "factual", SOURCES, 20, rng)
        for arm, n in alloc.items():
            st = arm.split("|")[0]
            for _ in range(n):  # each candidate searched in that arm is one trial
                state.update("factual", arm, float(rng.random() < truth[st]), weight=0.05)
    learned = allocate(state, "factual", SOURCES, 20, greedy=True)
    dense = [learned[arm_key(st, "dense")] for st in SOURCES]
    assert dense[0] >= 12 and dense[0] > dense[1] > dense[2]  # best > middle > worst
    for st, p in truth.items():  # converged: the posterior means are close to the truth
        a, b = state.belief("factual", arm_key(st, "dense"))
        assert abs(a / (a + b) - p) < 0.05, st
    sampled = [allocate(state, "factual", SOURCES, 20, random.Random(i))[arm_key("standards", "keyword")]
               for i in range(50)]
    assert sum(sampled) / len(sampled) > 10  # Thompson draws now favour it too


def test_allocation_never_adds_sources_and_drops_weak_ones() -> None:
    state = BanditState()
    for _ in range(100):
        state.update("procedural", arm_key("faq", "keyword"), 0.0)
        state.update("procedural", arm_key("checklists", "keyword"), 1.0)
    alloc = allocate(state, "procedural", ["checklists", "faq"], 10, greedy=True)
    assert set(a.split("|")[0] for a in alloc) <= {"checklists", "faq"}
    assert alloc[arm_key("checklists", "keyword")] == 10 and arm_key("faq", "keyword") not in alloc
    assert all(n >= MIN_CANDIDATES for n in alloc.values())
    assert BanditState.from_json(state.to_json()).belief("procedural", arm_key("faq", "keyword")) == \
        state.belief("procedural", arm_key("faq", "keyword"))
    assert BanditState.from_json({"kind": "rules"}).arms == {}


def test_implicit_signals_change_the_rewards() -> None:
    """Covers: R-7.9 (signals feed the bandit)."""
    base = [Observation("factual", ("faq|dense",), 1.0, 1.0, 1, "answer")]
    with_signals = base + [Observation("factual", ("faq|dense",), *SIGNAL_REWARDS["rephrase"], 1, "rephrase"),
                           Observation("factual", ("faq|dense",), *SIGNAL_REWARDS["copy"], 1, "copy")]
    a = learn(BanditState(), base).belief("factual", "faq|dense")
    b = learn(BanditState(), with_signals).belief("factual", "faq|dense")
    assert a == (2.0, 1.0) and b == (2.5, 1.5)


# --------------------------------------------------------- from the logs to a version

@pytest.fixture(scope="module")
def world(tmp_path_factory):
    root = _clean_clone(tmp_path_factory.mktemp("routing"))
    indexer = make_indexer(root, synthetic_fallback=True)
    indexer.sync(actor="test")
    with indexer.sessions() as s:
        clients = ClientMatcher.from_db(s)
    synthetic = {r.doc_id for r in read_manifest(DATA / "manifest.synthetic.csv")[1]}
    records = [r for r in load_records() if set(r["gold_doc_ids"]) <= synthetic]
    return indexer, clients, records


def bot(world) -> AuditAssistant:
    indexer, clients, _ = world
    settings = _settings().model_copy(update={"bandit_seed": 3})
    return AuditAssistant(AgentDeps(settings=settings, llm=FakeLLM(), searcher=make_searcher(indexer, top_k=20),
                                    reranker=FakeReranker(), sessions=indexer.sessions, clients=clients))


def age(world, query_ids: list[int], days: float) -> None:
    with world[0].sessions() as s:
        s.execute(update(Query).where(Query.id.in_(query_ids)).values(
            created_at=(datetime.now(timezone.utc) - timedelta(days=days)).replace(tzinfo=None)))
        s.commit()


def test_a_new_policy_version_is_learned_from_the_logs_and_replayed(world) -> None:
    """Covers: R-7.4. Answers older than the feedback window, their feedback and signals
    become rewards; the result is saved as a NEW, INACTIVE version with a replay report;
    answers inside the window are not used yet."""
    indexer, _, records = world
    assistant = bot(world)
    asked = [assistant.ask(r["question"], r["user_id"]) for r in records[:4]]
    age(world, [a.query_id for a in asked[:3]], days=2)  # the 4th is too recent to count
    with indexer.sessions() as s:
        s.add(Feedback(answer_id=asked[0].answer_id, user_id=records[0]["user_id"], rating="not_helpful",
                       created_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=2)))
        s.add(FeedbackSignal(answer_id=asked[1].answer_id, user_id=records[1]["user_id"], kind="copy"))
        s.commit()
    seen = observations(indexer.sessions, _settings(), datetime.now(timezone.utc))
    answers = {o.answer_id for o in seen if o.kind == "answer"}
    assert asked[3].answer_id not in answers and answers <= {a.answer_id for a in asked[:3]}
    first = next(o for o in seen if o.answer_id == asked[0].answer_id and o.kind == "answer")
    assert first.reward == 0.0  # "not helpful" within the window
    assert any(o.kind == "copy" for o in seen)
    assert all(arm.split("|")[1] in ("dense", "keyword") for o in seen for arm in o.arms)

    policy = propose(indexer.sessions, make_searcher(indexer, top_k=20), _settings(), "test",
                     records=records[:8])
    assert policy.is_active is False and policy.weights["kind"] == "bandit"
    assert policy.weights["observations"]["answers"] == len(answers)
    replay = policy.eval_report["replay"]
    assert set(replay) >= {"passed", "score", "baseline_score", "tolerance", "questions"}
    assert replay["questions"] == 8
    with indexer.sessions() as s:
        active = s.scalars(select(RoutingPolicy).where(RoutingPolicy.is_active.is_(True))).one()
    assert active.version != policy.version  # proposing never activates


def test_a_harmful_policy_fails_replay_and_cannot_be_activated(world) -> None:
    """Covers: R-13.3-replay, R-10-policy. A policy that starves the sources holding the
    answers loses on replay and the API refuses it; a sound one passes and is activated."""
    indexer, clients, records = world
    sample = records[:12]
    searcher = make_searcher(indexer, top_k=20)
    harmful = BanditState()
    for r in sample:  # teach it that every source type holding a gold document is useless
        with indexer.sessions() as s:
            from app.db.models import Document

            gold_types = {d.source_type for d in s.scalars(select(Document).where(
                Document.doc_id.in_(r["gold_doc_ids"])))}
        for st in r["source_types"]:
            for method in ("dense", "keyword"):
                for _ in range(200):
                    harmful.update(r["intent"], arm_key(st, method), 0.0 if st in gold_types else 1.0)
    bad = replay_report(searcher, harmful.to_json(), {"kind": "rules"}, _settings(), sample)
    sound = replay_report(searcher, BanditState().to_json(), {"kind": "rules"}, _settings(), sample)
    assert bad["passed"] is False and bad["score"] < bad["baseline_score"] - 0.02
    assert sound["passed"] is True

    with indexer.sessions() as s:
        base = s.query(RoutingPolicy).count()
        s.add_all([RoutingPolicy(version=100 + base, weights=harmful.to_json(), created_by="test",
                                 eval_report={"replay": bad}),
                   RoutingPolicy(version=101 + base, weights=BanditState().to_json(), created_by="test",
                                 eval_report={"replay": sound})])
        admin = s.get(User, "A01")
        admin.password_hash = hash_password("a-long-test-password")
        s.commit()
    settings = _settings().model_copy(update={"auth_rate_per_minute": 1000})
    services = Services(settings, bot(world), indexer, ReadWriteLock())
    with TestClient(create_app(services, static_dir=None)) as c:
        token = c.post("/api/v1/auth/login", json={"email": "kiran.rao@example-associates.test",
                                                   "password": "a-long-test-password"}).json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        refused = c.put("/api/v1/routing-policy", json={"version": 100 + base}, headers=headers)
        assert refused.status_code == 409
        accepted = c.put("/api/v1/routing-policy", json={"version": 101 + base}, headers=headers)
        assert accepted.status_code == 200 and accepted.json()["is_active"] is True


def test_the_active_learned_policy_steers_retrieval(world) -> None:
    """Covers: R-8-bandit (in the pipeline). With a learned policy active, the plan's
    allocation splits each retriever's candidates among the routed sources, and every
    arm's search is recorded with the source it searched."""
    indexer, _, records = world
    state = BanditState()
    for _ in range(50):
        state.update("factual", arm_key("firm_policy", "dense"), 1.0)
        state.update("factual", arm_key("firm_policy", "keyword"), 1.0)
    with indexer.sessions() as s:
        for p in s.scalars(select(RoutingPolicy)):
            p.is_active = False
        version = s.query(RoutingPolicy).count() + 200
        s.add(RoutingPolicy(version=version, weights=state.to_json(), created_by="test",
                            eval_report={"replay": {"passed": True}}, is_active=True))
        s.commit()
    result = bot(world).ask("What is our firm's overall materiality benchmark?", "M01")
    with indexer.sessions() as s:
        plan = json.loads(s.scalars(select(Trace).where(Trace.query_id == result.query_id,
                                                        Trace.agent == "retrieval_plan")).one().output)
        rows = s.scalars(select(Retrieval).where(Retrieval.query_id == result.query_id,
                                                 Retrieval.method.in_(("dense", "keyword")))).all()
    assert plan["policy_version"] == version and plan["allocation"]
    dense = {a: n for a, n in plan["allocation"].items() if a.endswith("|dense")}
    routed = len(dense)
    # Proportional to the draws: the proven arm gets the largest share, more than an even split.
    assert max(dense, key=dense.get) == arm_key("firm_policy", "dense")
    assert dense[arm_key("firm_policy", "dense")] > plan["candidates_per_retriever"] / routed
    assert {r.source_id for r in rows} <= {f"fs-{a.split('|')[0]}" for a in plan["allocation"]}
