"""M17c: the CI quality gates (eval/gates.py). The metric values below are TEST INPUTS
built from the committed baseline, not results; no model is called.

Covers: R-13.1-sets (the regression gate fails on an injected regression),
R-13.3-replay (CI replay gates for routing and prompts).
"""

import copy
import json
from pathlib import Path

import pytest

from eval import gates
from eval.run_retrieval_eval import load_ground_truth, synthetic_questions

TOL = 0.02


def _baseline() -> dict:
    return json.loads(gates.RETRIEVAL_BASELINE.read_text(encoding="utf-8"))


def _source_metrics(baseline: dict) -> dict:
    return json.loads((gates.RESULTS / baseline["source_run"] / "metrics.json").read_text(encoding="utf-8"))


def test_committed_baselines_match_the_runs_they_were_accepted_from() -> None:
    """Covers: R-13.1-sets (baselines are copied from real runs in eval/results/, never typed)."""
    retrieval = _baseline()
    assert retrieval["corpus"] == "synthetic"
    assert gates.gated_values(_source_metrics(retrieval)) == retrieval["values"]
    assert gates._question_ids(gates.RESULTS / retrieval["source_run"]) == retrieval["question_ids"]
    replay = json.loads(gates.REPLAY_BASELINE.read_text(encoding="utf-8"))
    assert gates._replay_values(gates.RESULTS / replay["source_run"]) == replay["values"]


def test_the_retrieval_gate_fails_on_an_injected_regression() -> None:
    """Covers: R-13.1-sets (CI gate fails on an injected regression, per source type)."""
    baseline = _baseline()
    metrics = _source_metrics(baseline)
    ids = baseline["question_ids"]
    assert gates.retrieval_failures(metrics, ids, baseline, TOL) == []

    within = copy.deepcopy(metrics)
    within["by_mode"]["adaptive"]["mrr"] -= TOL / 2
    assert gates.retrieval_failures(within, ids, baseline, TOL) == []

    overall = copy.deepcopy(metrics)
    overall["by_mode"]["adaptive"]["ndcg@10"] -= 0.05
    assert any("adaptive.ndcg@10" in f for f in gates.retrieval_failures(overall, ids, baseline, TOL))

    per_type = copy.deepcopy(metrics)
    source_type = sorted(per_type["by_mode"]["adaptive"]["per_source_type"])[0]
    per_type["by_mode"]["adaptive"]["per_source_type"][source_type]["recall@5"] -= 0.25
    fails = gates.retrieval_failures(per_type, ids, baseline, TOL)
    assert fails == [f"adaptive.{source_type}.recall@5: "
                     f"{baseline['values'][f'adaptive.{source_type}.recall@5'] - 0.25:.3f} is below the "
                     f"baseline {baseline['values'][f'adaptive.{source_type}.recall@5']:.3f} - {TOL}."]


def test_the_retrieval_gate_fails_on_any_leak_or_a_changed_question_set() -> None:
    baseline = _baseline()
    metrics = _source_metrics(baseline)
    leaky = copy.deepcopy(metrics)
    leaky["by_mode"]["bm25"]["cross_engagement_leak_rate"] = 0.01
    assert any("leaked" in f for f in gates.retrieval_failures(leaky, baseline["question_ids"], baseline, TOL))
    fewer = baseline["question_ids"][1:]
    assert any("question set" in f for f in gates.retrieval_failures(metrics, fewer, baseline, TOL))
    full = {**metrics, "corpus": "full"}
    assert any("corpus" in f for f in gates.retrieval_failures(full, baseline["question_ids"], baseline, TOL))


def test_an_edited_baseline_is_refused(tmp_path: Path) -> None:
    baseline = _baseline()
    edited = copy.deepcopy(baseline)
    key = next(iter(edited["values"]))
    edited["values"][key] += 0.1
    values_of = lambda r: gates.gated_values(json.loads((r / "metrics.json").read_text(encoding="utf-8")))  # noqa: E731
    assert gates._baseline_matches_its_run("x", baseline, values_of, gates.RESULTS) == []
    assert "never edited" in gates._baseline_matches_its_run("x", edited, values_of, gates.RESULTS)[0]
    assert "not in" in gates._baseline_matches_its_run("x", baseline, values_of, tmp_path)[0]


def test_the_routing_replay_gate_uses_the_tolerance() -> None:
    """Covers: R-13.3-replay (CI replays the code's routing and gates on the baseline)."""
    baseline = json.loads(gates.REPLAY_BASELINE.read_text(encoding="utf-8"))
    ids = baseline["values"]["record_ids"]
    base = baseline["values"]["score"]
    assert gates.replay_failures({"score": base - 0.01, "record_ids": ids}, baseline, TOL) == []
    assert gates.replay_failures({"score": base - 0.03, "record_ids": ids}, baseline, TOL)
    assert gates.replay_failures({"score": base, "record_ids": ids[1:]}, baseline, TOL)


def test_a_changed_seed_prompt_needs_a_passing_replay_report(tmp_path: Path) -> None:
    """Covers: R-13.3-replay (a prompt change reaches production only after replay passes)."""
    accepted = json.loads(gates.PROMPT_BASELINE.read_text(encoding="utf-8"))
    prompts = gates._code_prompts()
    assert gates.prompt_failures(prompts, accepted, tmp_path) == []  # the committed code passes

    changed = {**prompts, "synthesis": prompts["synthesis"] + "\nBe brief."}
    fails = gates.prompt_failures(changed, accepted, tmp_path)
    assert len(fails) == 1 and fails[0].startswith("synthesis: the seed prompt changed")

    def report(name: str, passed: bool, text: str) -> None:
        run = tmp_path / name
        run.mkdir()
        (run / "metrics.json").write_text(json.dumps(
            {"agent": "synthesis", "passed": passed, "template_sha256": gates.sha256(text)}), encoding="utf-8")

    report("20261121-100000_prompt_replay", False, changed["synthesis"])
    assert gates.prompt_failures(changed, accepted, tmp_path)  # a FAILED replay does not count
    report("20261121-100100_prompt_replay", True, changed["synthesis"] + " (other text)")
    assert gates.prompt_failures(changed, accepted, tmp_path)  # a pass for another text does not count
    report("20261121-100200_prompt_replay", True, changed["synthesis"])
    assert gates.prompt_failures(changed, accepted, tmp_path) == []


def test_synthetic_mode_scores_only_what_the_committed_corpus_can_answer() -> None:
    docs, _, _ = load_ground_truth()
    questions = [{"id": "a", "gold_doc_ids": []},
                 {"id": "b", "gold_doc_ids": [next(d for d, r in docs.items() if r["synthetic"] == "true")]},
                 {"id": "c", "gold_doc_ids": [next(d for d, r in docs.items() if r["synthetic"] != "true")]}]
    kept, excluded = synthetic_questions(questions, docs)
    assert [q["id"] for q in kept] == ["a", "b"] and excluded == 1


def test_a_run_becomes_the_baseline_of_its_own_corpus(tmp_path: Path, monkeypatch) -> None:
    """A synthetic run can only replace the CI baseline, a full-corpus run only the laptop's
    (eval/baselines/retrieval.json); a run from before the per-source sets is refused."""
    targets = {"synthetic": tmp_path / "ci.json", "full": tmp_path / "laptop.json"}
    monkeypatch.setattr(gates, "RETRIEVAL_BASELINES", targets)
    source = _source_metrics(_baseline())
    old = tmp_path / "20261121-000000_retrieval"
    old.mkdir()
    (old / "metrics.json").write_text(json.dumps({"corpus": "full"}), encoding="utf-8")
    with pytest.raises(SystemExit, match="current question sets"):
        gates.accept_retrieval(old)

    full = tmp_path / "20261121-000100_retrieval"
    full.mkdir()
    (full / "metrics.json").write_text(json.dumps({**source, "corpus": "full", "questions_by_set": {"general": 1}}),
                                       encoding="utf-8")
    (full / "per_question.csv").write_text("id\nq001\n", encoding="utf-8")
    gates.accept_retrieval(full)
    assert json.loads(targets["full"].read_text(encoding="utf-8"))["corpus"] == "full"
    assert not targets["synthetic"].exists()
