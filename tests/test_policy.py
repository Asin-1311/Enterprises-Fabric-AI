"""M13a: the per-candidate policy check (OPA in full, the same rules in code in lite).

Covers: R-12-opa. The OPA server itself (`opa test` and the equivalence on the real
server) is tested in tests/integration/test_full_backends.py.
"""

import json
import logging
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from app.adapters.full.policy import DECISIONS_PATH, OpaPolicyEngine
from app.adapters.lite.policy import InCodePolicy
from app.db.models import Chunk, Document, User
from app.ports import PolicyUnavailable
from app.retrieval.acl import UserContext, allowed_chunk_ids, load_user
from tests.helpers import make_indexer, make_searcher
from tests.test_clean_clone import _clean_clone


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    """The synthetic corpus as a clean clone indexes it (all 42 documents, restricted
    ones included), with every synthetic user."""
    root = _clean_clone(tmp_path_factory.mktemp("policy"))
    indexer = make_indexer(root, synthetic_fallback=True)
    indexer.sync(actor="test")
    return indexer


def candidates(session) -> tuple[list[str], list[dict]]:
    rows = session.execute(select(Chunk.chunk_id, Document).join(
        Document, Document.doc_id == Chunk.doc_id).order_by(Chunk.chunk_id)).all()
    return [cid for cid, _ in rows], [
        {"doc_id": d.doc_id, "access_level": d.access_level, "engagement_id": d.engagement_id,
         "source_type": d.source_type, "classification": d.classification} for _, d in rows]


def test_the_policy_in_code_agrees_with_the_prefilter_on_every_chunk_for_every_user(corpus) -> None:
    """Covers: R-12-opa (lite half): for every synthetic user and every chunk of the
    synthetic corpus, the policy allows exactly what the index pre-filter allows."""
    policy = InCodePolicy()
    with corpus.sessions() as s:
        ids, docs = candidates(s)
        users = [load_user(s, u) for u in s.scalars(select(User.user_id))]
        assert len(users) >= 8 and len(ids) > 100
        assert {"restricted", "confidential", "public_internal"} <= {d["classification"] for d in docs}
        for user in users:
            decided = {cid for cid, ok in zip(ids, policy.allowed(user, docs)) if ok}
            assert decided == allowed_chunk_ids(s, user), user.user_id


def test_a_policy_denial_after_retrieval_drops_the_chunk_and_is_logged(corpus, caplog) -> None:
    """A chunk the pre-filter allowed but the policy denies is a disagreement: logged as
    a security error, counted, and never returned."""
    searcher = make_searcher(corpus)
    staff = UserContext("S01", "staff", frozenset({"ENG-NWT-FY26"}))
    question = "overall materiality benchmark manufacturing clients"
    denied = searcher.search(question, staff)[0].doc_id  # the best hit without the denial

    class DenyOneDocument(InCodePolicy):
        name = "test"

        def allowed(self, user, documents):
            return [d["doc_id"] != denied and ok
                    for d, ok in zip(documents, super().allowed(user, documents))]

    searcher.policy = DenyOneDocument()
    caplog.set_level(logging.ERROR, logger="app.retrieval.hybrid")
    results = searcher.search(question, staff)
    assert results and all(r.doc_id != denied for r in results)
    assert searcher.policy_disagreements >= 1
    assert "SECURITY: pre-filter and test policy disagree" in caplog.text


def test_search_fails_closed_when_the_policy_cannot_answer(corpus) -> None:
    class Down(InCodePolicy):
        def allowed(self, user, documents):
            raise PolicyUnavailable("OPA is down")

    searcher = make_searcher(corpus)
    searcher.policy = Down()
    with pytest.raises(PolicyUnavailable):
        searcher.search("materiality", UserContext("P01", "partner", frozenset()))


def test_results_carry_the_classification(corpus) -> None:
    searcher = make_searcher(corpus)
    partner = UserContext("P01", "partner", frozenset())
    hits = searcher.search("encryption key custody recovery custodians", partner)
    assert any(h.doc_id == "POL-SEC-01" and h.classification == "restricted" for h in hits)


# ------------------------------------------------------------ OPA client (no server)

def test_opa_client_sends_one_batch_and_reads_the_decisions() -> None:
    """Covers: R-12-opa (client contract; the real server is in the integration job)."""
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={"result": [True, False]})

    engine = OpaPolicyEngine("http://opa.test", transport=httpx.MockTransport(handler))
    user = UserContext("S1", "staff", frozenset({"ENG-B", "ENG-A"}))
    docs = [{"doc_id": "A", "access_level": "staff", "engagement_id": None, "extra": "dropped"},
            {"doc_id": "B", "access_level": "partner"}]
    assert engine.allowed(user, docs) == [True, False]
    assert engine.allowed(user, []) == []  # no call for nothing
    path, body = seen[0]
    assert path == DECISIONS_PATH and len(seen) == 1
    assert body["input"]["user"] == {"user_id": "S1", "role": "staff", "engagement_ids": ["ENG-A", "ENG-B"]}
    assert body["input"]["documents"][0] == {"doc_id": "A", "access_level": "staff", "engagement_id": None,
                                             "source_type": None, "classification": None}


@pytest.mark.parametrize("response", [
    httpx.Response(500, text="boom"),
    httpx.Response(200, json={}),                         # policy not loaded: no result
    httpx.Response(200, json={"result": [True]}),         # wrong length
    httpx.Response(200, json={"result": [True, "yes"]}),  # not booleans
])
def test_opa_client_fails_closed_on_anything_but_a_decision_list(response) -> None:
    engine = OpaPolicyEngine("http://opa.test", transport=httpx.MockTransport(lambda r: response))
    with pytest.raises(PolicyUnavailable):
        engine.allowed(UserContext("S1", "staff", frozenset()), [{"access_level": "staff"}] * 2)


def test_rego_policy_and_tests_are_committed() -> None:
    root = Path(__file__).resolve().parents[1] / "policy"
    rego = (root / "document_access.rego").read_text(encoding="utf-8")
    assert "package fabric.document_access" in rego and "decisions :=" in rego
    assert (root / "document_access_test.rego").is_file()
