"""M14b: the NLI second opinion, "check this point" flags, claim-level citation spans,
claim_verdict events and draft streaming.

Covers: R-7.8, R-9-span, R-5-exp (the API part; the UI streaming view is M16).
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agents.graph import AgentDeps, AuditAssistant
from app.agents.nli import ClaimCheck, check_claims, claim_text
from app.agents.verifier import FLAG_MARK, verify
from app.api.main import Services, create_app
from app.concurrency import ReadWriteLock
from app.db.models import Answer, Chunk
from tests.helpers import make_searcher
from tests.test_agents import FakeLLM, FakeReranker, _settings, world  # noqa: F401 (fixture)
from tests.test_api import PASSWORD, as_user, parse_sse, with_emails  # noqa: F401


class ScriptedNli:
    """NLI stand-in: a sentence entails a claim when the claim's key word is in it, and
    contradicts it when it holds the word listed as that word's opposite."""

    def __init__(self, entails: dict[str, str], contradicts: dict[str, str] | None = None) -> None:
        self.entails, self.contradicts = entails, contradicts or {}
        self.pairs: list[tuple[str, str]] = []

    def scores(self, pairs):
        self.pairs += pairs
        out = []
        for premise, hypothesis in pairs:
            e = any(k in hypothesis and v in premise for k, v in self.entails.items())
            c = any(k in hypothesis and v in premise for k, v in self.contradicts.items())
            out.append({"entailment": 0.9 if e else 0.05, "contradiction": 0.9 if c and not e else 0.05,
                        "neutral": 0.05 if (e or c) else 0.9})
        return out


# ------------------------------------------------------------------ the NLI rule

def test_nli_labels_support_and_contradiction() -> None:
    nli = ScriptedNli({"sixty": "sixty"}, {"ninety": "sixty"})
    checks = check_claims([(1, "Assemble within sixty days [D:1:1].", ["D:1:1"]),
                           (2, "Assemble within ninety days [D:1:1].", ["D:1:1"]),
                           (3, "Partners sign off [D:1:1].", ["D:1:1"])],
                          {"D:1:1": ["Files are assembled within sixty days.", "Other text."]}, nli)
    assert checks[1].label == "entailment" and checks[1].support == {"D:1:1": ["Files are assembled within sixty days."]}
    assert checks[2].label == "contradiction" and checks[2].support == {}
    assert checks[3].label == "neutral"
    assert nli.pairs[0] == ("Files are assembled within sixty days.", "Assemble within sixty days.")
    assert claim_text("A fact [STD-CH06:1.9:1][POL-2:0003].") == "A fact."


def test_an_nli_contradiction_flags_a_claim_the_llm_accepted(world) -> None:
    """Covers: R-7.8. LLM supported + NLI contradiction -> kept and flagged; LLM unsupported
    -> removed whatever NLI says; LLM supported + NLI entailment -> kept, not flagged."""
    indexer = world[0]
    with indexer.sessions() as s:
        cid = s.scalars(select(Chunk.chunk_id).where(Chunk.doc_id == "POL-NEW")).first()
        chunk_text = s.get(Chunk, cid).text
    chunks = {r.chunk_id: r for r in make_searcher(indexer).fetch(
        [cid], __import__("app.retrieval.acl", fromlist=["UserContext"]).UserContext("P1", "partner", frozenset()))}
    draft = (f"Assemble the file within forty-five days [{cid}]. "
             f"The file may be assembled within ninety days [{cid}]. "
             f"Late files are fined [{cid}].")
    verdicts = {"verification": lambda user: {"claims": [
        {"id": 1, "type": "factual", "verdict": "supported", "reason": "ok"},
        {"id": 2, "type": "factual", "verdict": "supported", "reason": "the LLM missed it"},
        {"id": 3, "type": "factual", "verdict": "unsupported", "reason": "not stated"}]}}
    nli = ScriptedNli({"forty-five": "forty-five", "fined": "forty-five"}, {"ninety": "forty-five"})
    v, _ = verify("q", draft, chunks, FakeLLM(**verdicts), evidence_ids=[cid], nli=nli,
                  evidence={cid: [chunk_text.split("\n", 1)[1].strip()]})
    assert v.flagged and "ninety days" in v.flagged[0]["claim"]
    assert f"ninety days [{cid}].{FLAG_MARK}" in v.text            # kept, marked in the answer
    assert "forty-five days" in v.text and "fined" not in v.text   # entailed kept; LLM-rejected removed
    assert v.confidence == pytest.approx(2 / 3)  # a flag does not overrule the verifier
    assert v.nli[1].label == "entailment" and v.nli[2].label == "contradiction"


# --------------------------------------------------- graph: verdicts, flags and spans

def assistant(world, llm: FakeLLM, nli) -> AuditAssistant:  # noqa: F811
    indexer, clients = world
    return AuditAssistant(AgentDeps(settings=_settings(), llm=llm, searcher=make_searcher(indexer, top_k=20),
                                    reranker=FakeReranker(), sessions=indexer.sessions,
                                    clients=clients, nli=nli))


def two_claims(user: str) -> str:
    import re
    cite = re.search(r'cite="([^"]+)"', user).group(1)
    return (f"Assemble the final audit file within forty-five days [{cite}]. "
            f"The file may also be assembled within ninety days [{cite}].")


def test_claim_verdicts_flags_and_claim_level_spans_are_recorded(world) -> None:
    """Covers: R-9-span. The citation's span is the sentence NLI found to entail the claim,
    not the whole evidence region; flagged claims are stored with the answer and every
    claim gets a claim_verdict event."""
    events: list[tuple[str, dict]] = []
    nli = ScriptedNli({"forty-five": "forty-five"}, {"ninety": "forty-five"})
    bot = assistant(world, FakeLLM(synthesis=two_claims), nli)
    result = bot.ask("Within how many days must the final audit file be assembled?", "S1",
                     on_event=lambda step, data: events.append((step, data)))
    assert result.status == "answered"
    verdicts = [d for step, d in events if step == "claim_verdict"]
    assert [d["verdict"] for d in verdicts] == ["supported", "flagged"]
    assert "ninety days" in result.flagged_claims[0]["claim"] and FLAG_MARK in result.text
    with world[0].sessions() as s:
        stored = s.get(Answer, result.answer_id).flagged_claims
        citation = result.citations[0]
        text = s.get(Chunk, citation.chunk_id).text
    assert stored and "ninety days" in stored[0]["claim"]
    span = text[citation.span_start:citation.span_end]
    assert "forty-five days" in span
    assert span.strip() == next(x for p, _ in nli.pairs if "forty-five" in (x := p))  # one sentence


def test_without_nli_spans_fall_back_to_the_evidence_region(world) -> None:
    result = assistant(world, FakeLLM(), None).ask(
        "Within how many days must the final audit file be assembled?", "S1")
    assert result.status == "answered" and result.flagged_claims == []
    assert result.citations[0].span_start is not None


# ------------------------------------------------------------------ draft streaming

@pytest.fixture(scope="module")
def client(tmp_path_factory):
    from app.agents.query import ClientMatcher
    from tests.helpers import make_indexer, write_doc, write_manifest, write_people
    from tests.test_agents import DOCS, ENGAGEMENTS, USERS

    root = tmp_path_factory.mktemp("stream")
    (root / "data").mkdir()
    write_people(root, with_emails(USERS), ENGAGEMENTS)
    rows = []
    for doc_id, rel, st, level, eng, eff, sup, body in DOCS:
        write_doc(root, rel, body)
        rows.append(dict(doc_id=doc_id, path=f"data/corpus/{rel}", source_type=st, title=doc_id,
                         version="1", effective_date=eff, supersedes=sup, access_level=level,
                         engagement_id=eng, synthetic="true"))
    write_manifest(root, rows)
    indexer = make_indexer(root)
    indexer.sync(actor="test")
    with indexer.sessions() as s:
        clients = ClientMatcher.from_db(s)
    settings = _settings().model_copy(update={"demo_password": PASSWORD, "auth_rate_per_minute": 1000})
    nli = ScriptedNli({"forty-five": "forty-five"}, {"ninety": "forty-five"})
    bot = AuditAssistant(AgentDeps(settings=settings, llm=FakeLLM(synthesis=two_claims),
                                   searcher=make_searcher(indexer, top_k=20), reranker=FakeReranker(),
                                   sessions=indexer.sessions, clients=clients, nli=nli))
    from tests import test_api

    test_api._tokens.clear()
    with TestClient(create_app(Services(settings, bot, indexer, ReadWriteLock()))) as c:
        yield c
    test_api._tokens.clear()


def test_the_first_draft_token_arrives_before_verification_starts(client) -> None:
    """Covers: R-5-exp (API). Draft tokens stream while the model writes; verification
    starts after; then one claim_verdict per claim; then the verified text and final."""
    r = client.post("/api/v1/query", json={"text": "Within how many days must the final audit file be assembled?"},
                    headers=as_user(client, "S1"))
    events = parse_sse(r.text)
    kinds = [k for k, _ in events]
    first_draft = kinds.index("draft_token")
    verifying = next(i for i, (k, d) in enumerate(events) if k == "progress" and d["step"] == "verifying")
    assert first_draft < verifying < kinds.index("claim_verdict") < kinds.index("token") < kinds.index("final")
    draft = "".join(d["text"] for k, d in events if k == "draft_token")
    assert "ninety days" in draft and FLAG_MARK not in draft          # the raw draft
    verdicts = [d["verdict"] for k, d in events if k == "claim_verdict"]
    assert verdicts == ["supported", "flagged"]
    final = events[-1][1]
    assert final["flagged_claims"] and "ninety days" in final["flagged_claims"][0]
    assert FLAG_MARK in final["text"]
    reopened = client.get(f"/api/v1/history/{final['query_id']}", headers=as_user(client, "S1")).json()
    assert reopened["flagged_claims"] == final["flagged_claims"]


# -------------------------------------------------------------- the real NLI model

def _model_cached(name: str) -> bool:
    try:
        from huggingface_hub import try_to_load_from_cache

        return isinstance(try_to_load_from_cache(name, "config.json"), str)
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.skipif(not _model_cached("cross-encoder/nli-deberta-v3-xsmall"),
                    reason="NLI model not downloaded (it is fetched on first use of the API)")
def test_the_real_nli_model_separates_entailment_and_contradiction() -> None:
    from app.agents.nli import CrossEncoderNli

    nli = CrossEncoderNli("cross-encoder/nli-deberta-v3-xsmall")
    premise = "The final audit file must be assembled within sixty days of the report date."
    entail, contra = nli.scores([(premise, "The audit file is assembled within sixty days."),
                                 (premise, "The audit file may be assembled after a year.")])
    assert entail["entailment"] > 0.5 and contra["contradiction"] > 0.5
    assert isinstance(check_claims([(1, "x", [])], {}, nli), dict)
    assert ClaimCheck("neutral", 0, 0).support == {}
