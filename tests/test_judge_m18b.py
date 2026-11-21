"""M18b: the evaluation judge from another model family, answer relevance, and the stratified
calibration sample. No paid call: the judge's HTTP API is mocked.

Covers: R-13.2-judge (answer relevance; a judge from a different family than the verifier;
stratified calibration), R-12-model (restricted text never reaches the external judge).
"""

import json
import random

import httpx
import pytest
from pydantic import ValidationError

from app.agents.llm import LLMResult
from app.agents.model_router import ModelRouter, PrivateModelUnavailable
from app.config import Settings, model_family
from app.security.injection import restricted_attributes, wrap_document
from eval.export_calibration import stratified_sample
from eval.judge import SCHEMA, judge_answer
from eval.judge_client import JudgeError, OpenAICompatibleJudge
from eval.run_answer_eval import summarise

BASE = dict(_env_file=None, model_light="anthropic/claude-haiku-5.5",
            model_synth="anthropic/claude-sonnet-5.5", model_verify="anthropic/claude-opus-5.5",
            llm_auth_token="test-token")


def test_the_judge_must_come_from_another_family_than_the_verifier() -> None:
    """Covers: R-13.2-judge (MODEL_JUDGE from a different model family than MODEL_VERIFY)."""
    assert model_family("anthropic/claude-opus-5.5") == model_family("claude-opus-5-5") == "anthropic"
    assert model_family("openai/gpt-5.6-terra") == "openai"
    assert model_family("google/gemini-4-carbon") == "google"
    assert Settings(**BASE, model_judge="openai/gpt-5.6-terra").model_judge == "openai/gpt-5.6-terra"
    with pytest.raises(ValidationError, match="different model family"):
        Settings(**BASE, model_judge="anthropic/claude-sonnet-5.5")
    with pytest.raises(ValidationError, match="different model family"):
        Settings(**{**BASE, "model_verify": "claude-opus-5-5"}, model_judge="claude-haiku-5-5")
    # Mock mode (CI) uses placeholder ids and calls no model, so the rule does not apply.
    assert Settings(**BASE, llm_mode="mock", model_judge="anthropic/claude-x").llm_mode == "mock"


def _judge(handler) -> OpenAICompatibleJudge:
    return OpenAICompatibleJudge("https://judge.test/api/v1", "key", "openai/gpt-5.6-terra", "medium",
                                 client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_the_judge_client_sends_a_strict_schema_and_no_sampling_parameters() -> None:
    seen = []
    verdict = {"correctness": "correct", "correctness_reason": "matches", "relevance": "relevant",
               "claims": [], "citations": []}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"model": "openai/gpt-5.6-terra-20261101",
                                         "choices": [{"message": {"content": json.dumps(verdict)}}],
                                         "usage": {"prompt_tokens": 900, "completion_tokens": 300}})

    result = _judge(handler).call(purpose="judge", role="verify", system="s", user="u",
                                  max_tokens=100, schema=SCHEMA)
    body = seen[0]
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["response_format"]["json_schema"]["schema"] == SCHEMA
    assert body["reasoning"] == {"effort": "medium"}
    assert not {"temperature", "top_p", "top_k"} & set(body)
    assert (result.data, result.tokens_in, result.tokens_out) == (verdict, 900, 300)
    assert result.model == "openai/gpt-5.6-terra"  # the configured id, which prices use


def test_judge_errors_never_echo_the_response_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="secret prompt text echoed back")

    with pytest.raises(JudgeError) as err:
        _judge(handler).call(purpose="judge", role="verify", system="s", user="u", max_tokens=10)
    assert "429" in str(err.value) and "secret" not in str(err.value)


def test_restricted_text_never_reaches_the_external_judge() -> None:
    """Covers: R-12-model (the judge sits behind ModelRouter; with no private model a
    restricted chunk stops the call before anything is sent)."""
    calls = []
    judge = ModelRouter(_judge(lambda r: calls.append(r) or httpx.Response(500)), private=None)
    block = wrap_document("Two custodians approve key recovery.", cite="POL-SEC-01:2:1",
                          **restricted_attributes("restricted"))
    with pytest.raises(PrivateModelUnavailable):
        judge_answer(question={"question": "q", "gold_answer": "g", "category": "single_source"},
                     answer_text="Two custodians approve it [POL-SEC-01:2:1].",
                     cited_texts={"POL-SEC-01:2:1": "Two custodians approve key recovery."},
                     llm=judge, restricted=frozenset({"POL-SEC-01:2:1"}))
    assert calls == [] and "restricted" in block


class FakeJudge:
    def __init__(self, data: dict) -> None:
        self.data = data

    def call(self, **_kwargs) -> LLMResult:
        return LLMResult(json.dumps(self.data), self.data, "openai/gpt-5.6-terra", "medium", 10, 5, 1.0)


def test_answer_relevance_is_judged_and_reported() -> None:
    """Covers: R-13.2-judge (answer relevance, separate from correctness)."""
    data = {"correctness": "partially_correct", "correctness_reason": "misses one fact",
            "relevance": "partially_relevant", "claims": [], "citations": []}
    j, _ = judge_answer(question={"question": "q", "gold_answer": "g"}, answer_text="An answer.",
                        cited_texts={}, llm=FakeJudge(data))
    assert (j.correctness, j.relevance, j.relevance_score) == ("partially_correct", "partially_relevant", 0.5)
    odd, _ = judge_answer(question={"question": "q", "gold_answer": "g"}, answer_text="x",
                          cited_texts={}, llm=FakeJudge({**data, "relevance": "maybe"}))
    assert odd.relevance == "not_relevant"  # an invalid label never counts in the system's favour

    def row(score, relevance, ttft):
        return {"error": "", "score": score, "correctness": "correct", "relevance_score": relevance,
                "faithfulness": None, "factual_claims": 0, "supported_claims": 0, "citation_pairs": 0,
                "supporting_citations": 0, "access_leak": False, "cross_engagement_leak": False,
                "content_leak": False, "category": "x", "status": "answered", "ttft_s": ttft,
                "seconds": 2.0, "est_cost_usd": 0.0, "judge_skipped": ""}

    s = summarise([row(1.0, 1.0, 0.8), row(1.0, 0.5, None), row(0.5, 0.0, 1.2)])
    assert s["answer_relevance"] == 0.5
    assert s["ttft_measured"] == 2  # answers that stream no draft have no time to first token


def test_the_calibration_sample_is_stratified() -> None:
    """Covers: R-13.2-judge (at least 8 of 30 judged partially correct or incorrect)."""
    def answers(n_correct, n_other):
        labels = ["correct"] * n_correct + ["partially_correct", "incorrect"] * (n_other // 2)
        return [{"id": f"q{i}", "system": "full", "judge": {"correctness": c}} for i, c in enumerate(labels)]

    sample = stratified_sample(answers(270, 20), 30, 8, random.Random(42))
    others = sum(a["judge"]["correctness"] != "correct" for a in sample)
    assert len(sample) == 30 and others == 8  # a random 30 would expect about 2
    assert stratified_sample(answers(270, 20), 30, 8, random.Random(42)) == sample  # fixed seed
    few = stratified_sample(answers(100, 4), 30, 8, random.Random(1))
    assert len(few) == 30 and sum(a["judge"]["correctness"] != "correct" for a in few) == 4
    many = stratified_sample(answers(40, 60), 30, 8, random.Random(1))
    assert sum(a["judge"]["correctness"] != "correct" for a in many) == 18  # proportional above the floor


def test_the_evaluation_sample_is_fixed_and_stratified() -> None:
    """Covers: R-3-eval (the paid answer runs use a fixed, stratified sample of the
    questions, the same in every run, so their spread can be compared)."""
    from eval.question_sets import (SAMPLE_GENERAL_CATEGORIES, SOURCE_TYPES, evaluation_sample,
                                    load_questions)

    questions = load_questions()
    sample = evaluation_sample(questions)
    assert sample == evaluation_sample(questions)  # fixed seed: every run asks the same questions
    assert len(sample) == len(SOURCE_TYPES) + len(SAMPLE_GENERAL_CATEGORIES) == 12
    assert {q["set"] for q in sample} == {"general", *SOURCE_TYPES}
    assert {q["category"] for q in sample if q["set"] == "general"} == set(SAMPLE_GENERAL_CATEGORIES)
    assert all(q["gold_doc_ids"] for q in sample if q["set"] != "general")


def test_calibration_can_pool_the_sample_runs(tmp_path, monkeypatch) -> None:
    from eval import export_calibration as ec

    monkeypatch.setattr(ec, "CALIBRATION", tmp_path / "calibration")
    runs = []
    for name, labels in (("20261121-000001_answers", ["correct"] * 20 + ["incorrect"] * 4),
                         ("20261121-000002_answers", ["correct"] * 18 + ["partially_correct"] * 6)):
        run = tmp_path / name
        run.mkdir()
        (run / "answers.jsonl").write_text("".join(json.dumps(
            {"id": f"q{i}", "system": "full", "category": "x", "question": "q", "gold_answer": "g",
             "answer": "a", "judge": {"correctness": c}}) + "\n" for i, c in enumerate(labels)),
            encoding="utf-8")
        runs.append(run)
    labels_file, key_file = ec.export(runs)
    assert labels_file.name == "20261121-000001_answers+20261121-000002_answers_labels.csv"
    key = json.loads(key_file.read_text(encoding="utf-8"))
    rows = [v for k, v in key.items() if k != "_strata"]
    assert len(rows) == 30 and key["_strata"]["not_correct"] >= 8
    assert {r["run"] for r in rows} == {r.name for r in runs}
