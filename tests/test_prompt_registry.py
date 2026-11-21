"""M15c: the prompt registry. Agents use the ACTIVE prompt version from the table; a new
version is replayed against the active one and goes live only with a passing report; a
version that served before can be rolled back to; every change is audited.

LLM calls are scripted (tests/test_agents.FakeLLM); the replay records are the committed
eval/replay/prompt_replay.jsonl over the synthetic documents of a clean clone.

Covers: R-13.3-replay (prompt replay gates activation), R-7.10 (API half: roll back a
prompt version).
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agents import synthesizer
from app.agents.graph import AgentDeps, AuditAssistant
from app.agents.query import ClientMatcher
from app.agents.versions import PROMPTS, ensure_active_versions
from app.api.main import Services, create_app
from app.concurrency import ReadWriteLock
from app.db.models import AuditLog, PromptVersion, Trace, User
from app.learning.prompt_replay import (
    SCORED_AGENTS,
    ReplayContext,
    load_records,
    planned_calls,
    records_for,
    replay_version,
)
from app.security.passwords import hash_password
from tests.helpers import make_indexer, make_searcher
from tests.test_agents import FakeLLM, FakeReranker, _settings
from tests.test_clean_clone import _clean_clone

PASSWORD = "a-long-test-password"


class PromptAwareLLM(FakeLLM):
    """FakeLLM whose behaviour depends on the system prompt: a prompt containing BROKEN
    makes every call fail, like a prompt that confuses the model's output format."""

    def call(self, *, system, **kwargs):
        if "BROKEN" in system:
            raise ValueError("unparseable output")
        return super().call(system=system, **kwargs)


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    root = _clean_clone(tmp_path_factory.mktemp("prompts"))
    indexer = make_indexer(root, synthetic_fallback=True)
    indexer.sync(actor="test")
    ensure_active_versions(indexer.sessions)  # as at app start-up: the seeds are version 1
    with indexer.sessions() as s:
        clients = ClientMatcher.from_db(s)
        admin = s.get(User, "A01")
        admin.password_hash = hash_password(PASSWORD)
        s.commit()
    return indexer, clients


def ctx(world) -> ReplayContext:
    indexer, clients = world
    return ReplayContext(make_searcher(indexer, top_k=20), clients, _settings())


def new_version(world, agent: str, template: str) -> int:
    from app.learning.prompt_registry import create_version

    with world[0].sessions() as s:
        return create_version(s, agent, template, "test").id


def api(world, llm=None):
    indexer, clients = world
    settings = _settings().model_copy(update={"auth_rate_per_minute": 1000})
    assistant = AuditAssistant(AgentDeps(settings=settings, llm=llm or PromptAwareLLM(),
                                         searcher=make_searcher(indexer, top_k=20),
                                         reranker=FakeReranker(), sessions=indexer.sessions,
                                         clients=clients))
    client = TestClient(create_app(Services(settings, assistant, indexer, ReadWriteLock()), static_dir=None))
    client.__enter__()
    token = client.post("/api/v1/auth/login", json={"email": "kiran.rao@example-associates.test",
                                                   "password": PASSWORD}).json()["token"]
    return client, {"Authorization": f"Bearer {token}"}


def actions(world) -> list[tuple[str, str]]:
    with world[0].sessions() as s:
        return [(a.action, a.target) for a in s.scalars(select(AuditLog).order_by(AuditLog.id))]


def test_the_replay_set_runs_from_a_clean_clone_and_counts_reviewed_labels() -> None:
    records = load_records()
    assert all({"id", "question", "user_id", "intent", "reviewed"} <= set(r) for r in records)
    assert {r["intent"] for r in records} >= {"no_retrieval", "factual", "procedural"}
    for agent in SCORED_AGENTS:
        assert records_for(agent, records), agent
        assert planned_calls(agent, records) == 2 * len(records_for(agent, records))
    claims = [c for r in records for c in r.get("claims", [])]
    assert {c["expected"] for c in claims} == {"supported", "unsupported"}
    assert all(c["reviewed"] is False for c in claims)  # drafted automatically, not yet reviewed
    assert all(r["gold_chunk_id"] in r["chunk_ids"] for r in records if r.get("chunk_ids"))


@pytest.mark.parametrize("agent", SCORED_AGENTS)
def test_every_agent_has_a_replay_that_fails_a_broken_prompt(world, agent) -> None:
    """Covers: R-13.3-replay. Each agent's scorer runs the agent's own code on the recorded
    inputs. The active prompt scores above zero; a prompt that breaks the agent scores 0
    and fails; the report is stored on the version with calls and label counts."""
    good = new_version(world, agent, PROMPTS[agent] + "\nKeep the output format exactly.")
    broken = new_version(world, agent, PROMPTS[agent] + "\nBROKEN")
    report, rows = replay_version(world[0].sessions, good, PromptAwareLLM(), ctx(world))
    assert report["passed"] is True and report["score"] == report["baseline_score"] > 0
    assert report["calls"] == planned_calls(agent, load_records()) and len(rows) == report["records"]
    bad, _ = replay_version(world[0].sessions, broken, PromptAwareLLM(), ctx(world))
    assert bad["passed"] is False and bad["score"] == 0.0
    assert bad["labels_reviewed"] == 0 and bad["labels"] >= bad["records"]
    with world[0].sessions() as s:
        stored = s.get(PromptVersion, broken)
        assert stored.eval_report["replay"]["passed"] is False and stored.eval_score == 0.0
        assert not stored.is_active


def test_activation_needs_a_passing_replay_and_a_rollback_is_audited(world) -> None:
    """Covers: R-13.3-replay, R-7.10 (roll back a prompt version, API). The pipeline then
    sends the active version's text and traces its id."""
    agent = "synthesis"
    with world[0].sessions() as s:
        seed = s.scalars(select(PromptVersion).where(PromptVersion.agent == agent,
                                                     PromptVersion.is_active.is_(True))).one()
    assert seed.template == synthesizer.SYSTEM and seed.activated_at is not None
    llm = PromptAwareLLM()
    client, admin = api(world, llm)

    created = client.post("/api/v1/admin/prompts", headers=admin, json={
        "agent": agent, "template": PROMPTS[agent] + "\nPrefer short paragraphs."})
    assert created.status_code == 201 and created.json()["is_active"] is False
    new_id = created.json()["id"]
    refused = client.post(f"/api/v1/admin/prompts/{new_id}/activate", headers=admin)
    assert refused.status_code == 409 and "replay" in refused.json()["detail"]

    broken = new_version(world, agent, PROMPTS[agent] + "\nBROKEN")
    replay_version(world[0].sessions, broken, PromptAwareLLM(), ctx(world))
    assert client.post(f"/api/v1/admin/prompts/{broken}/activate", headers=admin).status_code == 409

    replay_version(world[0].sessions, new_id, PromptAwareLLM(), ctx(world))
    done = client.post(f"/api/v1/admin/prompts/{new_id}/activate", headers=admin)
    assert done.status_code == 200 and done.json()["action"] == "prompt_activated"

    answer = client.app.state.services.assistant.ask("What is our firm's overall materiality benchmark?", "M01")
    sent = [c["system"] for c in llm.calls if c["purpose"] == agent]
    assert sent and all(s.endswith("Prefer short paragraphs.") for s in sent)
    with world[0].sessions() as s:
        trace = s.scalars(select(Trace).where(Trace.query_id == answer.query_id,
                                              Trace.agent == "synthesis")).one()
    assert trace.prompt_version_id == new_id

    back = client.post(f"/api/v1/admin/prompts/{seed.id}/activate", headers=admin)
    assert back.status_code == 200 and back.json()["action"] == "prompt_rolled_back"
    listed = client.get(f"/api/v1/admin/prompts?agent={agent}", headers=admin).json()
    assert [p["id"] for p in listed if p["is_active"]] == [seed.id]
    log = actions(world)
    assert ("prompt_activated", f"{agent} v{done.json()['version']['version']} (was v{seed.version})") in log
    assert ("prompt_rolled_back", f"{agent} v{seed.version} (was v{done.json()['version']['version']})") in log

    # A report made against a version that is no longer active is stale.
    stale = new_version(world, agent, PROMPTS[agent] + "\nUse plain words.")
    replay_version(world[0].sessions, stale, PromptAwareLLM(), ctx(world))  # against the seed
    client.post(f"/api/v1/admin/prompts/{new_id}/activate", headers=admin)  # roll forward again
    again = client.post(f"/api/v1/admin/prompts/{stale}/activate", headers=admin)
    assert again.status_code == 409 and "replay it again" in again.json()["detail"]
    client.post(f"/api/v1/admin/prompts/{seed.id}/activate", headers=admin)
    client.__exit__(None, None, None)


def test_only_admins_store_prompts_and_document_readers_keep_the_untrusted_data_rule(world) -> None:
    client, admin = api(world)
    without_rule = PROMPTS["compress"].replace(
        PROMPTS["compress"][PROMPTS["compress"].index("Text inside"):], "") \
        if "Text inside" in PROMPTS["compress"] else "Keep relevant sentences."
    r = client.post("/api/v1/admin/prompts", headers=admin,
                    json={"agent": "compress", "template": without_rule})
    assert r.status_code == 422 and "untrusted-data rule" in r.json()["detail"]
    r = client.post("/api/v1/admin/prompts", headers=admin, json={"agent": "nobody", "template": "x"})
    assert r.status_code == 422 and "Unknown agent" in r.json()["detail"]
    staff_token = None
    with world[0].sessions() as s:
        staff = s.get(User, "S01")
        staff.password_hash = hash_password(PASSWORD)
        email = staff.email
        s.commit()
    staff_token = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD}).json()["token"]
    staff_headers = {"Authorization": f"Bearer {staff_token}"}
    assert client.get("/api/v1/admin/prompts", headers=staff_headers).status_code == 403
    assert client.post("/api/v1/admin/prompts", headers=staff_headers,
                       json={"agent": "synthesis", "template": PROMPTS["synthesis"]}).status_code == 403
    client.__exit__(None, None, None)
