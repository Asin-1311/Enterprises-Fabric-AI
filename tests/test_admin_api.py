"""M11: sources, targeted re-index, routing-policy activation, implicit signals, and the
new data recorded while answering (versions, retrieval steps, citation spans).

Uses the API fixture from tests/test_api.py (fake LLM, real SQLite, Qdrant and BM25).
A1 is an administrator with role staff; P1 is a partner who is NOT an administrator.
"""

import math

import pytest
from sqlalchemy import select

from app.agents import synthesizer
from app.agents.versions import PROMPTS, ensure_active_versions
from app.db.models import (
    AuditLog,
    Chunk,
    Citation,
    Document,
    PromptVersion,
    Query,
    Retrieval,
    RoutingPolicy,
    Trace,
)
from app.retrieval.spans import locate, supporting_span
from tests.test_api import QUESTION, as_user, ask, client  # noqa: F401  (fixture)

FIRM_POLICY_DOCS = {"POL-OLD", "POL-NEW", "SEC-OLD", "SEC-NEW", "MGR-1"}


def _actions(client, actor: str) -> list[str]:
    with client.indexer.sessions() as s:
        return [a.action for a in s.scalars(select(AuditLog).where(AuditLog.actor == actor))]


# --------------------------------------------------------------------------- sources

def test_folder_sources_are_registered_and_their_status_is_admin_only(client) -> None:
    """Covers: R-10-sources, R-9-tables."""
    r = client.get("/api/v1/sources/fs-firm_policy/status", headers=as_user(client, "A1"))
    assert r.status_code == 200
    status = r.json()
    assert status["type"] == "filesystem" and status["status"] == "active"
    assert status["documents"] == len(FIRM_POLICY_DOCS) and status["chunks"] >= status["documents"]
    assert status["freshness_lag_seconds"] is not None and status["freshness_lag_seconds"] >= 0
    engagement = client.get("/api/v1/sources/fs-engagement/status", headers=as_user(client, "A1"))
    assert engagement.json()["classification"] == "confidential"
    assert client.get("/api/v1/sources/fs-firm_policy/status",
                      headers=as_user(client, "P1")).status_code == 403  # partner, not admin
    assert client.get("/api/v1/sources/nope/status", headers=as_user(client, "A1")).status_code == 404
    with client.indexer.sessions() as s:
        assert {d.source_id for d in s.scalars(select(Document))} >= {"fs-firm_policy", "fs-engagement"}


def test_registering_a_source_checks_settings_and_refuses_stored_secrets(client) -> None:
    """Covers: R-10-sources. Secrets must be referenced ("env:NAME"), never stored."""
    admin = as_user(client, "A1")
    git = {"id": "git-firm-docs", "type": "git", "classification": "confidential",
           "connector_config": {"url": "https://git.example/firm.git", "branch": "main",
                                "auth": {"token": "ghp_not_a_reference"},
                                "defaults": {"source_type": "firm_policy", "access_level": "staff"}}}
    r = client.post("/api/v1/sources", json=git, headers=admin)
    assert r.status_code == 422 and "auth.token looks like a secret" in str(r.json())

    git["connector_config"]["auth"]["token"] = "env:GIT_TOKEN"
    r = client.post("/api/v1/sources", json=git, headers=admin)
    assert r.status_code == 201 and r.json()["status"] == "active"
    assert client.post("/api/v1/sources", json=git, headers=admin).status_code == 409
    assert "source_registered" in _actions(client, "A1")

    status = client.get("/api/v1/sources/git-firm-docs/status", headers=admin).json()
    assert status["documents"] == 0 and status["freshness_lag_seconds"] is None  # no events yet

    bad = [
        {"id": "fs-outside", "type": "filesystem", "connector_config": {"path": "../.."}},
        {"id": "fs-missing", "type": "filesystem", "connector_config": {"path": "data/corpus/none"}},
        {"id": "s3-no-bucket", "type": "s3", "connector_config": {}},
        {"id": "BadId!", "type": "s3", "connector_config": {"bucket": "b"}},
        {"id": "s3-role", "type": "s3", "connector_config": {"bucket": "b"}, "role": "partner"},
        # M12c: a polling source must say what its documents are and who may see them.
        {"id": "s3-no-defaults", "type": "s3", "connector_config": {"bucket": "b"}},
        {"id": "git-ext", "type": "git", "connector_config": {
            "url": "ext::sh -c id", "branch": "main",
            "defaults": {"source_type": "firm_policy", "access_level": "staff"}}},
    ]
    for body in bad:
        assert client.post("/api/v1/sources", json=body, headers=admin).status_code == 422, body
    assert client.post("/api/v1/sources", json={**git, "id": "git-other"},
                       headers=as_user(client, "P1")).status_code == 403


# --------------------------------------------------------------------------- re-index

def _indexed_at(client) -> dict:
    with client.indexer.sessions() as s:
        return {d.doc_id: d.indexed_at for d in s.scalars(select(Document))}


def test_targeted_reindex_touches_only_that_document_or_source(client) -> None:
    """Covers: R-10-reindex."""
    admin, embedder = as_user(client, "A1"), client.indexer.embedder
    before, calls = _indexed_at(client), embedder.calls
    r = client.post("/api/v1/documents/reindex", json={"doc_id": "POL-NEW"}, headers=admin)
    assert r.status_code == 200 and r.json() == {
        "scope": "document", "target": "POL-NEW", "indexed": ["POL-NEW"],
        "metadata_updated": [], "unchanged": 0, "removed": []}
    after = _indexed_at(client)
    assert {d for d in after if after[d] != before[d]} == {"POL-NEW"}
    assert embedder.calls == calls + 1  # one document embedded, nothing else

    r = client.post("/api/v1/documents/reindex", json={"source_id": "fs-firm_policy"}, headers=admin)
    assert r.status_code == 200 and set(r.json()["indexed"]) == FIRM_POLICY_DOCS
    changed = {d for d, t in _indexed_at(client).items() if t != after[d]}
    assert changed == FIRM_POLICY_DOCS  # the engagement and circular documents untouched

    for body, code in (({"doc_id": "NOPE"}, 404), ({"source_id": "nope"}, 404),
                       ({"doc_id": "POL-NEW", "all": True}, 422), ({}, 422)):
        assert client.post("/api/v1/documents/reindex", json=body, headers=admin).status_code == code


# --------------------------------------------------------------------------- routing policy

def test_routing_policy_activation_needs_a_passing_replay_report(client) -> None:
    """Covers: R-10-policy (activation gate; the replay suite itself comes in M15)."""
    admin = as_user(client, "A1")
    ensure_active_versions(client.indexer.sessions)  # seeds version 1 if not yet there
    with client.indexer.sessions() as s:
        v1 = s.scalars(select(RoutingPolicy).where(RoutingPolicy.is_active.is_(True))).one()
        assert v1.version == 1 and v1.weights["kind"] == "rules"
        s.add(RoutingPolicy(version=2, weights={"kind": "test"}, created_by="test", eval_report=None))
        s.commit()

    def activate(version: int, headers=admin):
        return client.put("/api/v1/routing-policy", json={"version": version}, headers=headers)

    assert activate(2).status_code == 409  # no report
    for report in ({"replay": {"passed": False}}, {"replay": "passed"}):
        with client.indexer.sessions() as s:
            s.scalars(select(RoutingPolicy).where(RoutingPolicy.version == 2)).one().eval_report = report
            s.commit()
        assert activate(2).status_code == 409
    with client.indexer.sessions() as s:
        s.scalars(select(RoutingPolicy).where(RoutingPolicy.version == 2)).one().eval_report = {
            "replay": {"passed": True, "report": "eval/results/<run>_replay"}}
        s.commit()
    assert activate(2, as_user(client, "P1")).status_code == 403
    r = activate(2)
    assert r.status_code == 200 and r.json()["is_active"] is True
    back = activate(1)  # rolling back to a version that served users before is allowed
    assert back.status_code == 200 and back.json()["version"] == 1
    assert activate(99).status_code == 404
    with client.indexer.sessions() as s:
        assert [p.version for p in s.scalars(select(RoutingPolicy).where(
            RoutingPolicy.is_active.is_(True)))] == [1]
    assert _actions(client, "A1").count("routing_policy_activated") == 2


# --------------------------------------------------------------------------- what an answer records

def test_answering_records_versions_retrieval_steps_and_spans(client) -> None:
    """Covers: R-8-record, R-9-tables, R-9-span. Every trace names the routing policy;
    model steps name their prompt version; every retrieval step is a `retrievals` row with
    raw scores, and the fused row carries each chunk's weights and multipliers; citation
    spans point into the chunk's text."""
    final = ask(client, QUESTION, "S1")[-1][1]
    qid = final["query_id"]
    with client.indexer.sessions() as s:
        policy = s.scalars(select(RoutingPolicy).where(RoutingPolicy.is_active.is_(True))).one()
        traces = {t.agent: t for t in s.scalars(select(Trace).where(Trace.query_id == qid))}
        prompt = s.get(PromptVersion, traces["synthesis"].prompt_version_id)
        steps = list(s.scalars(select(Retrieval).where(Retrieval.query_id == qid)))
        query = s.get(Query, qid)
        cites = list(s.scalars(select(Citation).where(Citation.answer_id == final["answer_id"])
                               .order_by(Citation.id)))
        texts = {c.chunk_id: s.get(Chunk, c.chunk_id).text for c in cites}
    assert all(t.policy_version_id == policy.id for t in traces.values())
    assert prompt.agent == "synthesis" and prompt.template == synthesizer.SYSTEM and prompt.is_active
    assert traces["retrieval"].prompt_version_id is None  # no model call in that step
    assert query.injection_hits == []

    methods = {st.method for st in steps}
    assert methods == {"dense", "keyword", "fused"}
    fused = next(st for st in steps if st.method == "fused")
    for entry in fused.scores:
        assert entry["weights"] == {"dense": 1.0, "keyword": 1.0}
        assert set(entry["multipliers"]) == {"superseded", "recency"}
        assert entry["final_score"] == pytest.approx(
            entry["rrf_score"] * math.prod(entry["multipliers"].values()), abs=1e-5)
    dense = next(st for st in steps if st.method == "dense")
    assert [e["rank"] for e in dense.scores] == list(range(1, len(dense.scores) + 1))

    assert cites and final["citations"]
    located = [c for c in cites if c.span_start is not None]
    assert located, "no citation span was located"
    for c, out in zip(cites, final["citations"]):
        assert (c.span_start, c.span_end) == (out["span_start"], out["span_end"])
    for c in located:
        assert 0 <= c.span_start < c.span_end <= len(texts[c.chunk_id])
        supported = texts[c.chunk_id][c.span_start:c.span_end]
        assert supported.strip() and supported in texts[c.chunk_id]


def test_spans_ignore_whitespace_differences_and_cover_every_sentence() -> None:
    text = "Heading line\nThe final audit file is assembled\n within 60 days.  Other text. Last bit."
    assert locate(text, "The final audit file is assembled within 60 days.") == (13, 63)
    assert locate(text, "Not in the chunk.") is None
    assert supporting_span(text, ["Other text.", "The final audit file is assembled within 60 days."]) \
        == (13, 76)
    assert supporting_span(text, []) is None


def test_a_changed_prompt_is_recorded_as_a_new_active_version(client, monkeypatch) -> None:
    with client.indexer.sessions() as s:
        before = s.scalars(select(PromptVersion).where(PromptVersion.agent == "compress",
                                                       PromptVersion.is_active.is_(True))).first()
    monkeypatch.setitem(PROMPTS, "compress", PROMPTS["compress"] + "\nEdited.")
    versions = ensure_active_versions(client.indexer.sessions)
    with client.indexer.sessions() as s:
        new = s.get(PromptVersion, versions.prompt_ids["compress"])
        rows = list(s.scalars(select(PromptVersion).where(PromptVersion.agent == "compress")))
    assert new.template.endswith("Edited.") and new.is_active
    if before is not None:
        assert new.version == before.version + 1
    assert sum(r.is_active for r in rows) == 1
    assert "prompt_version_recorded" in _actions(client, "system")


# --------------------------------------------------------------------------- signals

def test_feedback_signals_are_stored_for_own_answers_only(client) -> None:
    answer_id = ask(client, QUESTION, "S1")[-1][1]["answer_id"]
    signal = {"answer_id": answer_id, "kind": "citation_click"}
    r = client.post("/api/v1/feedback/signals", json=signal, headers=as_user(client, "S1"))
    assert r.status_code == 201 and r.json()["stored"] is True
    assert client.post("/api/v1/feedback/signals", json=signal,
                       headers=as_user(client, "S2")).status_code == 404
    assert client.post("/api/v1/feedback/signals", json={**signal, "kind": "liked"},
                       headers=as_user(client, "S1")).status_code == 422
