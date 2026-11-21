"""Tests for the baseline, the judge's arithmetic and the answer-eval aggregation (no live LLM)."""

from pathlib import Path

import pytest

from app.baseline.single_pass_rag import SinglePassRAG
from eval.judge import judge_answer
from eval.run_answer_eval import percentile, summarise
from tests.helpers import make_indexer, make_searcher, write_doc, write_manifest, write_people
from tests.test_agents import DOCS, ENGAGEMENTS, USERS, FakeLLM


@pytest.fixture(scope="module")
def searcher(tmp_path_factory: pytest.TempPathFactory):
    root: Path = tmp_path_factory.mktemp("baseline")
    (root / "data").mkdir()
    write_people(root, USERS, ENGAGEMENTS)
    rows = []
    for doc_id, rel, st, level, eng, eff, sup, body in DOCS:
        write_doc(root, rel, body)
        rows.append(dict(doc_id=doc_id, path=f"data/corpus/{rel}", source_type=st, title=doc_id,
                         version="1", effective_date=eff, supersedes=sup, access_level=level,
                         engagement_id=eng, synthetic="true"))
    write_manifest(root, rows)
    indexer = make_indexer(root)
    indexer.sync(actor="test")
    return make_searcher(indexer, top_k=20)


def test_baseline_is_one_dense_search_and_one_call_with_acl(searcher) -> None:
    from app.retrieval.acl import UserContext

    llm = FakeLLM(baseline=lambda user: "Forty-five days [POL-NEW:1:1] and [FAKE:0001].")
    calls = []
    original = searcher.search
    searcher.search = lambda *a, **k: calls.append(k) or original(*a, **k)
    try:
        staff = UserContext("S1", "staff", frozenset({"ENG-A"}))
        result = SinglePassRAG(searcher, llm).ask("materiality benchmark managerword", staff)
    finally:
        searcher.search = original
    assert [c["mode"] for c in calls] == ["dense"] and llm.purposes() == ["baseline"]
    assert llm.calls[0]["role"] == "synth"
    assert all(h.doc_id not in ("MGR-1", "SEC-NEW") for h in result.retrieved)  # ACL applied
    assert result.invalid_citations == ["FAKE:0001"]
    assert result.seconds_to_first_token is not None


def test_judge_arithmetic() -> None:
    def judge(user: str) -> dict:
        return {"correctness": "partially_correct", "correctness_reason": "x",
                "claims": [{"id": 1, "factual": True, "supported": True},
                           {"id": 2, "factual": True, "supported": False},
                           {"id": 3, "factual": False, "supported": True}],
                "citations": [{"claim_id": 1, "cite": "A:0001", "supports": True},
                              {"claim_id": 2, "cite": "A:0001", "supports": False},
                              {"claim_id": 2, "cite": "B:0002", "supports": True}]}

    answer = "Forty-five days [A:0001]. Ninety days [A:0001][B:0002]. Not stated elsewhere."
    j, _ = judge_answer(question={"question": "q", "gold_answer": "g", "category": "standard"},
                        answer_text=answer, cited_texts={"A:0001": "a", "B:0002": "b"},
                        llm=FakeLLM(judge=judge))
    assert j.score == 0.5 and j.factual_claims == 2 and j.supported_claims == 1
    assert j.faithfulness == 0.5
    assert (j.citation_pairs, j.supporting_citations) == (3, 2)
    assert j.citation_precision == pytest.approx(2 / 3)


def test_judge_prompt_lists_forbidden_facts() -> None:
    llm = FakeLLM(judge=lambda user: {"correctness": "correct", "correctness_reason": "",
                                      "claims": [], "citations": []})
    judge_answer(question={"question": "q", "gold_answer": "Not available.",
                           "category": "role_denied", "forbidden_facts": ["18 lakh"]},
                 answer_text="Not available.", cited_texts={}, llm=llm)
    assert "FORBIDDEN facts: ['18 lakh']" in llm.calls[0]["user"]
    assert llm.calls[0]["role"] == "verify"


def test_percentile_nearest_rank() -> None:
    values = [float(v) for v in range(1, 21)]  # 1..20
    assert percentile(values, 0.5) == 10.0
    assert percentile(values, 0.95) == 19.0
    assert percentile([], 0.5) is None
    assert percentile([7.0], 0.95) == 7.0


def test_progress_file_survives_a_crash_and_spend_is_per_session(tmp_path: Path) -> None:
    from eval.run_answer_eval import append_progress, load_progress, measured_spend

    p = tmp_path / "progress.jsonl"
    append_progress(p, {"kind": "balance", "session": "s1", "balance": 12.00})
    append_progress(p, {"kind": "result", "session": "s1", "row": {"id": "q1", "system": "full"}})
    append_progress(p, {"kind": "balance", "session": "s1", "balance": 11.90})
    with p.open("a", encoding="utf-8") as f:
        f.write('{"kind": "result", "row": {"id": "q2"')  # half-written line from a crash
    # Resumed later, after a top-up: the jump in balance must not count as negative spend.
    append_progress(p, {"kind": "balance", "session": "s2", "balance": 20.00})
    append_progress(p, {"kind": "balance", "session": "s2", "balance": 19.75})
    append_progress(p, {"kind": "balance", "session": "s3", "balance": 19.75})  # cut off at once
    records = load_progress(p)
    assert [r["kind"] for r in records].count("result") == 1  # broken line skipped
    assert measured_spend(records) == (pytest.approx(0.35), 2)  # 0.10 + 0.25; s3 not counted
    assert measured_spend([]) == (None, 0)


def test_cohens_kappa_hand_computed() -> None:
    from eval.run_judge_calibration import cohens_kappa

    # 10 items. Human: 6 correct, 4 incorrect. Judge: 5 correct, 5 incorrect.
    # They agree on 9 (5 correct + 4 incorrect): observed = 0.9.
    # Chance agreement = (6*5 + 4*5) / 10^2 = 0.5, so kappa = (0.9 - 0.5) / (1 - 0.5) = 0.8.
    pairs = [("correct", "correct")] * 5 + [("correct", "incorrect")] + [("incorrect", "incorrect")] * 4
    assert len(pairs) == 10
    assert cohens_kappa(pairs) == pytest.approx(0.8)
    assert cohens_kappa([("correct", "correct")] * 3) is None  # all one class: undefined
    assert cohens_kappa([]) is None


def _row(**kw) -> dict:
    base = dict(error="", score=1.0, correctness="correct", faithfulness=1.0, factual_claims=2,
                supported_claims=2, citation_pairs=2, supporting_citations=2, access_leak=False,
                cross_engagement_leak=False, content_leak=False, category="standard",
                status="answered", ttft_s=1.0, seconds=2.0, est_cost_usd=0.01)
    base.update(kw)
    return base


def test_summarise() -> None:
    rows = [_row(), _row(score=0.0, correctness="incorrect", faithfulness=0.5, supported_claims=1,
                         supporting_citations=1, seconds=10.0),
            _row(category="role_denied", content_leak=True, factual_claims=0, supported_claims=0,
                 faithfulness=None, citation_pairs=0, supporting_citations=0, status="denied"),
            _row(error="boom")]
    s = summarise(rows)
    assert s["errors"] == 1 and s["judged"] == 3
    assert s["correctness"] == pytest.approx(2 / 3)
    assert s["faithfulness_micro"] == pytest.approx(3 / 4)
    assert s["citation_precision"] == pytest.approx(3 / 4)
    assert s["content_leak_rate"] == 1.0  # only denied categories count
    assert s["full_answer_s_p95"] == 10.0
    assert s["status_counts"] == {"answered": 2, "denied": 1}
