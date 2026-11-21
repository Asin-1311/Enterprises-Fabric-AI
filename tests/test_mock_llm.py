"""M17b: LLM_MODE=mock, the keyless stand-in used by the Helm smoke test and CI.

Covers: R-14 (deployment smoke tests run without paid model calls).
"""

import pytest
from pydantic import ValidationError

from app.agents.graph import AgentDeps, AuditAssistant
from app.agents.mock_llm import MOCK_NOTE, MockLLM
from app.agents.model_router import ModelRouter
from app.agents.query import ClientMatcher
from app.config import Settings
from tests.helpers import make_indexer, make_searcher
from tests.test_agents import FakeReranker, _settings
from tests.test_clean_clone import _clean_clone


@pytest.fixture(scope="module")
def assistant(tmp_path_factory):
    indexer = make_indexer(_clean_clone(tmp_path_factory.mktemp("mock")), synthetic_fallback=True)
    indexer.sync(actor="test")
    with indexer.sessions() as s:
        clients = ClientMatcher.from_db(s)
    llm = ModelRouter(MockLLM(), MockLLM("mock-private"))
    return AuditAssistant(AgentDeps(settings=_settings(), llm=llm, searcher=make_searcher(indexer, top_k=20),
                                    reranker=FakeReranker(), sessions=indexer.sessions, clients=clients))


def test_the_mock_answers_extractively_with_a_real_citation_and_says_so(assistant) -> None:
    """Covers: R-14. The whole pipeline runs (retrieval, re-ranking, synthesis, verification)
    with no key; the answer quotes a retrieved sentence, cites it, and is marked as a mock."""
    drafts: list[str] = []
    result = assistant.ask("Within how many days must the final audit file be assembled?", "S01",
                           on_token=drafts.append)
    assert result.status == "answered", result.text
    assert MOCK_NOTE in result.text and result.citations
    assert result.confidence == 1.0 and drafts  # verified, and the draft was streamed
    cited = result.citations[0]
    assert cited.chunk_id in result.text or cited.doc_id


def test_greetings_and_deadlines_are_classified_without_a_model(assistant) -> None:
    assert assistant.ask("Hello there", "S01").status == "no_retrieval"
    data = MockLLM().call(purpose="query_understanding", role="light", system="", max_tokens=10,
                          user="Question: When is the tax audit report due?", schema={}).data
    assert data["intent"] == "time_sensitive" and data["sub_queries"] == ["When is the tax audit report due?"]


def test_only_the_mock_mode_may_run_without_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("LLM_AUTH_TOKEN", "ANTHROPIC_API_KEY", "LLM_MODE"):
        monkeypatch.delenv(name, raising=False)  # independent of the caller's environment (CI)
    base = dict(_env_file=None, model_light="l", model_synth="s", model_verify="v")
    assert Settings(**base, llm_mode="mock").llm_mode == "mock"
    with pytest.raises(ValidationError, match="LLM_AUTH_TOKEN"):
        Settings(**base)  # the default mode is live
    with pytest.raises(ValidationError, match="LLM_AUTH_TOKEN"):
        Settings(**base, llm_mode="live")
