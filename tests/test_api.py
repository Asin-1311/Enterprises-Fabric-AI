"""API tests (FastAPI TestClient, fake LLM and re-ranker, real SQLite/Qdrant/BM25)."""

import json
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agents.graph import AgentDeps, AuditAssistant
from app.agents.query import ClientMatcher
from app.agents.synthesizer import JUDGEMENT_NOTE, NOT_AVAILABLE_MESSAGE
from app.concurrency import ReadWriteLock
from app.api.main import Services, create_app, stream_pieces
from app.db.models import AuditLog, Feedback, User
from app.security.passwords import verify_password
from tests.helpers import make_indexer, make_searcher, write_doc, write_manifest, write_people
from tests.test_agents import DOCS, ENGAGEMENTS, USERS, FakeLLM, FakeReranker, _settings

PASSWORD = "test-pass-123"
_tokens: dict[str, str] = {}


def with_emails(users_csv: str) -> str:
    """Add email (<id>@test.example) and is_admin columns to a users.csv text, plus an
    administrator A1: role staff, no engagements, admin flag (M11)."""
    lines = users_csv.strip().splitlines()
    rows = [lines[0] + ",email,is_admin"] + [
        f"{line},{line.split(',')[0].lower()}@test.example,false" for line in lines[1:]]
    rows.append("A1,Admin A,staff,true,a1@test.example,true")
    return "\n".join(rows) + "\n"


def as_user(client: TestClient, user_id: str) -> dict:
    """Authorization header for `user_id` (signs in once per test module)."""
    if user_id not in _tokens:
        r = client.post("/api/v1/auth/login",
                        json={"email": f"{user_id.lower()}@test.example", "password": PASSWORD})
        assert r.status_code == 200, r.text
        _tokens[user_id] = r.json()["token"]
    return {"Authorization": f"Bearer {_tokens[user_id]}"}


def parse_sse(body: str) -> list[tuple[str, dict]]:
    """[(event, data)] from a text/event-stream body."""
    events, name, data = [], None, []
    for line in body.splitlines():
        if line.startswith("event:"):
            name = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].strip())
        elif not line.strip() and name:
            events.append((name, json.loads("\n".join(data))))
            name, data = None, []
    if name:
        events.append((name, json.loads("\n".join(data))))
    return events


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory):
    root: Path = tmp_path_factory.mktemp("api")
    (root / "data").mkdir()
    write_people(root, with_emails(USERS), ENGAGEMENTS)
    rows = []
    for doc_id, rel, st, level, eng, eff, sup, body in DOCS:
        write_doc(root, rel, body)
        rows.append(dict(doc_id=doc_id, path=f"data/corpus/{rel}", source_type=st,
                         title=f"{doc_id} title", version="1", effective_date=eff, supersedes=sup,
                         access_level=level, engagement_id=eng, synthetic="true"))
    write_manifest(root, rows)
    indexer = make_indexer(root)
    indexer.sync(actor="test")
    with indexer.sessions() as s:
        clients = ClientMatcher.from_db(s)
    _tokens.clear()
    results = root / "eval_results"
    # The API sets the demo password for these synthetic users at start-up (real path).
    settings = _settings().model_copy(update={"eval_results_dir": results, "demo_password": PASSWORD,
                                              "auth_rate_per_minute": 1000})
    assistant = AuditAssistant(AgentDeps(
        settings=settings, llm=FakeLLM(), searcher=make_searcher(indexer, top_k=20),
        reranker=FakeReranker(), sessions=indexer.sessions, clients=clients))
    services = Services(settings, assistant, indexer, ReadWriteLock())
    with TestClient(create_app(services)) as c:
        c.results_dir = results  # type: ignore[attr-defined]
        c.indexer = indexer  # type: ignore[attr-defined]
        yield c


def ask(client: TestClient, text: str, user_id: str) -> list[tuple[str, dict]]:
    r = client.post("/api/v1/query", json={"text": text}, headers=as_user(client, user_id))
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    return parse_sse(r.text)


QUESTION = "Within how many days must the final audit file be assembled?"


def test_query_streams_progress_then_verified_answer_then_final(client) -> None:
    events = ask(client, QUESTION, "S1")
    kinds = [k for k, _ in events]
    assert kinds[0] == "progress" and kinds[-1] == "final"
    first_token = kinds.index("token")
    assert all(k == "progress" for k in kinds[:first_token])  # tokens only after verification
    assert "verifying" in [d["step"] for k, d in events[:first_token]]
    final = events[-1][1]
    streamed = "".join(d["text"] for k, d in events if k == "token")
    assert streamed == final["text"] and final["text"].endswith(JUDGEMENT_NOTE)
    assert final["status"] == "answered" and final["confidence"] == 1.0
    assert final["superseded_involved"] is True
    cite = final["citations"][0]
    assert cite["preview"] and cite["title"].endswith("title")
    assert "File Policy" not in cite["preview"].split(" ")[0]  # heading line stripped


def test_two_questions_are_answered_at_the_same_time(client, monkeypatch) -> None:
    """Covers: R-5-par. No global query lock: both questions are inside the assistant at
    once. (Phase 1's lock made the second wait, so the barrier would time out.)"""
    svc = client.app.state.services
    both_inside = threading.Barrier(2, timeout=10)
    real_ask = svc.assistant.ask

    def ask_together(*args, **kwargs):
        both_inside.wait()
        return real_ask(*args, **kwargs)

    monkeypatch.setattr(svc.assistant, "ask", ask_together)
    headers = {u: as_user(client, u) for u in ("S1", "P1")}  # sign in before the race
    finals: dict[str, str] = {}

    def one(user: str) -> None:
        events = parse_sse(client.post("/api/v1/query", json={"text": QUESTION},
                                       headers=headers[user]).text)
        finals[user] = events[-1][0]

    threads = [threading.Thread(target=one, args=(u,)) for u in headers]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert finals == {"S1": "final", "P1": "final"}


def test_denied_query_streams_generic_message(client) -> None:
    final = ask(client, "Alpha Fictional Ltd planning memo significant risks", "S2")[-1][1]
    assert final["status"] == "denied" and final["text"] == NOT_AVAILABLE_MESSAGE
    assert final["citations"] == []


def test_no_token_is_401_and_bad_body_is_422(client) -> None:
    assert client.post("/api/v1/query", json={"text": "x"}).status_code == 401
    assert client.post("/api/v1/query", json={"text": ""}, headers=as_user(client, "S1")).status_code == 422


def test_body_user_id_cannot_impersonate_another_user(client) -> None:
    # S2 is not on Alpha's engagement team. Naming S1 in the body must change nothing.
    r = client.post("/api/v1/query", headers=as_user(client, "S2"),
                    json={"text": "Alpha Fictional Ltd planning memo significant risks", "user_id": "S1"})
    final = parse_sse(r.text)[-1][1]
    assert final["status"] == "denied" and final["citations"] == []


def test_get_query_only_for_asker_or_partner(client) -> None:
    query_id = ask(client, QUESTION, "S1")[-1][1]["query_id"]
    mine = client.get(f"/api/v1/query/{query_id}", headers=as_user(client, "S1"))
    assert mine.status_code == 200
    body = mine.json()
    assert body["status"] == "answered" and body["citations"] and body["trace"]
    assert body["trace"][-1]["agent"] == "verification"
    assert client.get(f"/api/v1/query/{query_id}", headers=as_user(client, "S2")).status_code == 403
    assert client.get(f"/api/v1/query/{query_id}", headers=as_user(client, "P1")).status_code == 200
    assert client.get("/api/v1/query/999999", headers=as_user(client, "P1")).status_code == 404


def test_history_is_personal_newest_first_and_reopens_the_same_answer(client) -> None:
    original = ask(client, QUESTION, "S1")[-1][1]
    later = ask(client, "Alpha Fictional Ltd planning memo significant risks", "S1")[-1][1]
    items = client.get("/api/v1/history", headers=as_user(client, "S1")).json()
    ids = [i["query_id"] for i in items]
    assert ids.index(later["query_id"]) < ids.index(original["query_id"])  # newest first
    mine = next(i for i in items if i["query_id"] == original["query_id"])
    assert mine["question"] == QUESTION and mine["status"] == "answered" and mine["asked_at"]

    reopened = client.get(f"/api/v1/history/{original['query_id']}", headers=as_user(client, "S1")).json()
    for key in ("answer_id", "status", "text", "confidence", "claims_supported", "claims_factual",
                "removed_claims", "superseded_involved", "injection_flagged_chunks", "citations"):
        assert reopened[key] == original[key], key
    assert reopened["superseded_involved"] is True and reopened["seconds_to_answer"] is None

    # Personal: other users (even a partner) neither list nor open it; no token is 401.
    for other in ("S2", "P1"):
        assert original["query_id"] not in [
            i["query_id"] for i in client.get("/api/v1/history", headers=as_user(client, other)).json()]
        assert client.get(f"/api/v1/history/{original['query_id']}",
                          headers=as_user(client, other)).status_code == 404
    assert client.get("/api/v1/history").status_code == 401
    assert client.get("/api/v1/history/999999", headers=as_user(client, "S1")).status_code == 404


def test_answers_citing_phase1_chunk_ids_still_open_their_sources(client) -> None:
    """M12b: an answer written before stable chunk ids cites "<doc>:0003"; the API resolves
    it through legacy_chunk_id to the chunk with the same text."""
    from app.db.models import Chunk, Citation

    original = ask(client, QUESTION, "S1")[-1][1]
    cited = original["citations"][0]
    assert cited["chunk_id"].count(":") == 2  # new answers cite stable ids
    old_id = f"{cited['doc_id']}:0003"
    with client.app.state.services.indexer.sessions() as s:
        s.get(Chunk, cited["chunk_id"]).legacy_chunk_id = old_id
        for row in s.scalars(select(Citation).where(Citation.answer_id == original["answer_id"],
                                                    Citation.chunk_id == cited["chunk_id"])):
            row.chunk_id = old_id  # as a Phase 1 answer stored it
        s.commit()

    reopened = client.get(f"/api/v1/history/{original['query_id']}",
                          headers=as_user(client, "S1")).json()
    old = next(c for c in reopened["citations"] if c["chunk_id"] == old_id)
    assert {k: v for k, v in old.items() if k != "chunk_id"} == \
        {k: v for k, v in cited.items() if k != "chunk_id"}


def test_traces_are_partner_only(client) -> None:
    query_id = ask(client, QUESTION, "S1")[-1][1]["query_id"]
    assert client.get(f"/api/v1/traces/{query_id}", headers=as_user(client, "S1")).status_code == 403
    rows = client.get(f"/api/v1/traces/{query_id}", headers=as_user(client, "P1")).json()
    assert [r["agent"] for r in rows][:2] == ["query_understanding", "source_routing"]
    assert rows[0]["model"] == "fake-light" and isinstance(rows[0]["output"], dict)
    assert not any(r["content_hidden"] for r in rows)
    # Administrators may inspect traces too (CLAUDE.md section 10), but an admin who is not a
    # partner sees how each step ran, not the document text or drafts inside it.
    admin_rows = client.get(f"/api/v1/traces/{query_id}", headers=as_user(client, "A1")).json()
    assert [r["agent"] for r in admin_rows] == [r["agent"] for r in rows]
    assert all(r["content_hidden"] and r["input"] is None and r["output"] is None
               for r in admin_rows)
    assert admin_rows[0]["model"] == "fake-light" and admin_rows[0]["policy_version_id"]


def test_feedback_is_stored_masked(client) -> None:
    answer_id = ask(client, QUESTION, "S1")[-1][1]["answer_id"]
    s1 = as_user(client, "S1")
    r = client.post("/api/v1/feedback", headers=s1, json={
        "answer_id": answer_id, "rating": "not_helpful",
        "correction": "Client PAN is ZZZZQ1234Q, mail me at a@b.example"})
    assert r.status_code == 200 and r.json()["stored"]
    with client.indexer.sessions() as s:
        row = s.scalars(select(Feedback).order_by(Feedback.id.desc())).first()
    assert row.user_id == "S1" and row.rating == "not_helpful" and "ZZZZQ1234Q" not in row.correction
    assert "[PAN]" in row.correction and "[EMAIL]" in row.correction
    bad = {"answer_id": answer_id, "rating": "meh"}
    assert client.post("/api/v1/feedback", json=bad, headers=s1).status_code == 422
    missing = {"answer_id": 999999, "rating": "helpful"}
    assert client.post("/api/v1/feedback", json=missing, headers=s1).status_code == 404
    someone_elses = {"answer_id": answer_id, "rating": "helpful"}  # only the asker may rate
    assert client.post("/api/v1/feedback", json=someone_elses,
                       headers=as_user(client, "S2")).status_code == 404


def test_reindex_is_admin_only_and_logged(client) -> None:
    """Re-indexing is an administrative action (M11): not even a partner may do it."""
    everything = {"all": True}
    for user in ("S1", "P1"):
        r = client.post("/api/v1/documents/reindex", json=everything, headers=as_user(client, user))
        assert r.status_code == 403
    r = client.post("/api/v1/documents/reindex", json=everything, headers=as_user(client, "A1"))
    assert r.status_code == 200 and r.json()["unchanged"] == len(DOCS) and r.json()["scope"] == "all"
    with client.indexer.sessions() as s:
        actions = [a.action for a in s.scalars(select(AuditLog).where(AuditLog.actor == "A1"))]
    assert "manual_reindex_requested" in actions


def test_reloading_users_csv_keeps_passwords(client) -> None:
    client.indexer.load_reference_data()  # what every re-index does
    with client.indexer.sessions() as s:
        assert verify_password(PASSWORD, s.get(User, "S1").password_hash)


def test_sources_status(client) -> None:
    rows = {r["source_type"]: r for r in client.get(
        "/api/v1/sources/status", headers=as_user(client, "S1")).json()}
    assert rows["firm_policy"]["documents"] == 5 and rows["firm_policy"]["chunks"] >= 5
    assert rows["engagement"]["last_indexed_at"]


def test_metrics_quality_reads_saved_quality_runs_in_a_window(client) -> None:
    """Covers: R-10-metrics. Only retrieval/answers runs, filtered by from/to; calibration
    and known-invalid runs are never returned; values are copied, never computed."""
    s1 = as_user(client, "S1")
    assert client.get("/api/v1/metrics/quality", headers=s1).status_code == 404
    for name, value in [("20260101-000000_retrieval", 0.1), ("20260202-000000_retrieval", 0.9),
                        ("20260203-000000_answers", 0.7), ("20260204-000000_calibration", 0.5),
                        ("20261107-125802_retrieval", 0.0)]:  # the known-invalid run
        run = client.results_dir / name
        run.mkdir(parents=True)
        (run / "metrics.json").write_text(json.dumps({"value": value}), encoding="utf-8")

    def runs(**params) -> list[tuple[str, float]]:
        body = client.get("/api/v1/metrics/quality", headers=s1, params=params).json()
        return [(r["run"], r["metrics"]["value"]) for r in body["runs"]]

    assert runs() == [("20260202-000000_retrieval", 0.9), ("20260203-000000_answers", 0.7)]
    assert runs(**{"from": "2026-01-01", "to": "2026-01-31"}) == [("20260101-000000_retrieval", 0.1)]
    assert runs(**{"from": "2026-02-02"}) == [("20260203-000000_answers", 0.7),
                                             ("20260202-000000_retrieval", 0.9)]
    assert runs(to="2026-02-02") == [("20260202-000000_retrieval", 0.9),
                                     ("20260101-000000_retrieval", 0.1)]  # bare date = whole day
    bad = client.get("/api/v1/metrics/quality", headers=s1, params={"from": "last week"})
    assert bad.status_code == 422
    backwards = {"from": "2026-03-01", "to": "2026-01-01"}
    assert client.get("/api/v1/metrics/quality", headers=s1, params=backwards).status_code == 422
    empty = {"from": "2030-01-01"}
    assert client.get("/api/v1/metrics/quality", headers=s1, params=empty).status_code == 404


def test_users_endpoint_is_partner_only(client) -> None:
    assert client.get("/api/v1/users", headers=as_user(client, "S1")).status_code == 403
    users = {u["user_id"]: u for u in client.get("/api/v1/users", headers=as_user(client, "P1")).json()}
    assert users["S1"]["engagements"] == ["Alpha Fictional Ltd"]
    assert users["P1"]["engagements"] == ["all engagements"]
    assert users["M2"]["engagements"] == []
    me = client.get("/api/v1/auth/me", headers=as_user(client, "S1")).json()
    assert me["engagements"] == ["Alpha Fictional Ltd"] and me["email"] == "s1@test.example"


def test_stream_pieces_rejoin_exactly() -> None:
    text = "Line one [A:0001].\n\n- point two\n_note_"
    assert "".join(stream_pieces(text)) == text


def test_errors_shown_to_clients_are_generic_with_a_reference(client, monkeypatch, caplog) -> None:
    """Audit finding (M9): no internal details in client-visible errors; the details are
    in the server log under the same reference."""
    import logging

    svc = client.app.state.services

    def broken_ask(*args, **kwargs):
        raise RuntimeError("secret detail: C:/internal/fabric.db is locked")

    monkeypatch.setattr(svc.assistant, "ask", broken_ask)
    caplog.set_level(logging.ERROR, logger="uvicorn.error")
    kind, data = ask(client, QUESTION, "S1")[-1]
    assert kind == "error"
    assert "secret detail" not in data["message"] and "RuntimeError" not in data["message"]
    assert data["reference"] in data["message"]
    assert data["reference"] in caplog.text and "secret detail" in caplog.text


def test_unhandled_route_errors_are_generic(tmp_path, caplog) -> None:
    import logging

    app = create_app(services=None, static_dir=None)

    def boom() -> dict:
        raise ZeroDivisionError("internal path /srv/secret")

    app.add_api_route("/api/v1/boom", boom)
    caplog.set_level(logging.ERROR, logger="uvicorn.error")
    r = TestClient(app, raise_server_exceptions=False).get("/api/v1/boom")
    assert r.status_code == 500
    body = r.json()
    assert "internal path" not in body["detail"] and "ZeroDivisionError" not in body["detail"]
    assert body["reference"] in body["detail"] and body["reference"] in caplog.text
