"""Tests for scripts/make_report.py: tables and figures must reproduce the results files exactly."""

import csv
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from scripts import make_report as mr

CATS = ("standard", "role_denied")


def _overall(**kw) -> dict:
    base = dict(questions=2, errors=0, judged=2, correctness=0.75, correct_rate=0.5,
                faithfulness_macro=0.9, faithfulness_micro=0.875, citation_precision=0.9,
                access_leak_rate=0.0, cross_engagement_leak_rate=0.0, content_leak_rate=0.0,
                status_counts={"answered": 2}, ttft_s_p50=2.0, ttft_s_p95=12.25,
                full_answer_s_p50=3.0, full_answer_s_p95=12.25, estimated_answer_cost_usd=0.1)
    base.update(kw)
    return base


def _answers_metrics(complete: bool = True) -> dict:
    full = _overall(correctness=0.8125, faithfulness_micro=0.954)
    base = _overall(correctness=0.5, faithfulness_micro=None, ttft_s_p95=1.25, full_answer_s_p95=7.5)
    return {"complete": complete, "n_questions": 4, "n_reviewed": 1, "judge_model": "judge-x",
            "targets": {"ttft_p95_s": 1.5, "full_answer_p95_s": 8.0},
            "systems": {"full": {"overall": full, "by_category": {c: full for c in CATS}},
                        "baseline": {"overall": base, "by_category": {c: base for c in CATS}}},
            "estimated_judge_cost_usd": 0.2, "measured_spend_usd": 0.42,
            "measured_spend_sessions": 1}


def _retrieval_metrics() -> dict:
    def mode(mrr: float) -> dict:
        return {"questions_scored": 3, "recall@5": 0.9, "recall@10": 1.0, "mrr": mrr,
                "ndcg@10": 0.8, "access_leak_rate": 0.0, "cross_engagement_leak_rate": 0.0,
                "superseded_order_accuracy": 0.5, "superseded_questions": 2,
                "per_source_type": {"faq": {"gold_docs": 1, "recall@5": 1.0, "recall@10": 1.0}},
                "latency_ms_p50": 10.0, "latency_ms_p95": 20.0}
    return {"n_questions": 4, "n_reviewed": 1, "as_of": "2026-11-14", "k_docs": 10,
            "by_mode": {m: mode(0.111 * (i + 1)) for i, m in enumerate(mr.RETRIEVAL_MODES)}}


def _write(run: Path, metrics: dict) -> None:
    run.mkdir(parents=True)
    (run / "metrics.json").write_text(json.dumps(metrics), encoding="utf-8")


def _answers_run(run: Path, complete: bool = True, edited: bool = False) -> None:
    _write(run, _answers_metrics(complete))
    rows = [{"id": "q1", "system": "full", "category": "standard", "correctness": "correct"},
            {"id": "q2", "system": "baseline", "category": "role_denied", "correctness": "incorrect"}]
    with (run / "per_question.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    answers = [{"id": r["id"], "system": r["system"], "judge": {"correctness": r["correctness"]}}
               for r in rows]
    if edited:
        answers[1]["judge"]["correctness"] = "correct"
    (run / "answers.jsonl").write_text("\n".join(json.dumps(a) for a in answers) + "\n", encoding="utf-8")
    (run / "model_ids.json").write_text(json.dumps({"synth": "m-synth", "judge": "judge-x"}), encoding="utf-8")
    (run / "config.json").write_text(json.dumps({"effort_synth": "medium"}), encoding="utf-8")


def _calibration_run(run: Path, answers_run: str) -> None:
    confusion = {"correct": {"correct": 2, "partially_correct": 1, "incorrect": 0},
                 "partially_correct": {"correct": 0, "partially_correct": 0, "incorrect": 0},
                 "incorrect": {"correct": 1, "partially_correct": 0, "incorrect": 0}}
    _write(run, {"answers_run": answers_run, "labelled": 4, "exported": 4, "exact_agreement": 0.5,
                 "cohens_kappa": -0.2, "binary_agreement_correct_vs_not": 0.5,
                 "confusion_human_rows_judge_cols": confusion})
    with (run / "per_question.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["sample", "id", "system", "human", "judge", "agree"])
        w.writeheader()
        w.writerow({"sample": 1, "id": "q1", "system": "full", "human": "correct",
                    "judge": "correct", "agree": True})
        w.writerow({"sample": 2, "id": "q2", "system": "baseline", "human": "incorrect",
                    "judge": "correct", "agree": False})


@pytest.fixture()
def results(tmp_path: Path) -> Path:
    r = tmp_path / "results"
    _answers_run(r / "20260101-000000_answers")
    _answers_run(r / "20260102-000000_answers", complete=False)  # newer but incomplete
    _write(r / "20260101-000000_retrieval", _retrieval_metrics())
    _write(r / "20261107-125802_retrieval", _retrieval_metrics())  # newer but excluded
    _calibration_run(r / "20260101-000001_calibration", "20260101-000000_answers")
    _calibration_run(r / "20260103-000000_calibration", "some_other_answers")  # wrong source
    return r


def test_run_selection_skips_incomplete_excluded_and_unrelated_runs(results: Path) -> None:
    runs = mr.choose_runs(results)
    assert runs.answers.name == "20260101-000000_answers"
    assert runs.retrieval.name == "20260101-000000_retrieval"
    assert runs.calibration.name == "20260101-000001_calibration"
    with pytest.raises(SystemExit):
        mr.choose_runs(results, retrieval="20261107-125802_retrieval")


def test_tables_reproduce_the_metrics_files(results: Path) -> None:
    text, _ = mr.build_report(mr.choose_runs(results))
    assert "| Correctness (correct = 1, partial = 0.5) | 0.812 | 0.500 |" in text  # 0.8125 formatted
    assert "| Faithfulness, micro (supported factual claims) | 0.954 | n/a |" in text
    assert "| **hybrid (system)** | 0.900 | 1.000 | 0.444 | 0.800 |" in text
    assert "| Full system | 2.0 s | 12.2 s | **not met** |" in text
    assert "| Baseline | 2.0 s | 1.2 s | met | 3.0 s | 7.5 s | met |" in text
    assert "| Cohen's kappa | -0.200 |" in text
    assert "| 2 | q2 | baseline | role_denied | incorrect | correct |" in text
    assert "sessions measured: 1) | 0.42 |" in text
    assert "**1 of 4 evaluation questions have been reviewed" in text
    assert "No differences found" in text


def test_figures_are_valid_svg_with_one_bar_per_value(results: Path) -> None:
    _, figs = mr.build_report(mr.choose_runs(results))
    assert set(figs) == {"answer_quality.svg", "correctness_by_category.svg",
                         "retrieval_modes.svg", "latency.svg"}
    ns = "{http://www.w3.org/2000/svg}"
    for name, svg in figs.items():
        root = ET.fromstring(svg)  # well-formed XML
        bars = [p for p in root.iter(f"{ns}path") if (p.get("class") or "").startswith("s")]
        expected = {"answer_quality.svg": 7,          # 4 metrics x 2 systems, baseline faithfulness None
                    "correctness_by_category.svg": 4,  # 2 categories x 2 systems
                    "retrieval_modes.svg": 16,         # 4 metrics x 4 modes
                    "latency.svg": 8}[name]
        assert len(bars) == expected, name
    latency = ET.fromstring(figs["latency.svg"])
    assert len([line for line in latency.iter(f"{ns}line") if line.get("class") == "target"]) == 2


def test_an_undocumented_edit_blocks_the_report(results: Path) -> None:
    """Covers: R-ops-integrity. A run whose files disagree is refused, unless the
    difference is a documented known edit."""
    answers = results / "20260101-000000_answers"
    assert mr.check_integrity(answers) == mr.Integrity([], [])
    _answers_run(edited := results / "20260105-000000_answers", edited=True)
    integrity = mr.check_integrity(edited)
    assert integrity.documented == [] and len(integrity.unexplained) == 1
    assert "q2 (baseline)" in integrity.unexplained[0]
    with pytest.raises(mr.IntegrityError, match="files disagree"):
        mr.build_report(mr.choose_runs(results, answers=edited.name))


def test_a_documented_edit_is_listed_and_the_report_still_builds(results: Path, monkeypatch) -> None:
    """Covers: R-ops-integrity. The report uses only the untouched files and lists the edit."""
    _answers_run(edited := results / "20260105-000000_answers", edited=True)
    monkeypatch.setitem(mr.KNOWN_EDITS, (edited.name, "q2", "baseline"), ("incorrect", "correct"))
    integrity = mr.check_integrity(edited)
    assert integrity.unexplained == [] and len(integrity.documented) == 1
    text, _ = mr.build_report(mr.choose_runs(results, answers=edited.name))
    assert "changed by hand after the run" in text and "q2 (baseline)" in text
    # A documented edit only matches its exact values: a different change is unexplained.
    monkeypatch.setitem(mr.KNOWN_EDITS, (edited.name, "q2", "baseline"), ("incorrect", "partially_correct"))
    assert len(mr.check_integrity(edited).unexplained) == 1


def test_chance_agreement_and_target_status_hand_computed() -> None:
    # Human: 28 correct, 2 incorrect. Judge: 27 correct, 3 partially correct.
    # Chance = (28 * 27 + 0 * 3 + 2 * 0) / 30^2 = 756 / 900 = 0.84.
    confusion = {"correct": {"correct": 25, "partially_correct": 3, "incorrect": 0},
                 "partially_correct": {"correct": 0, "partially_correct": 0, "incorrect": 0},
                 "incorrect": {"correct": 2, "partially_correct": 0, "incorrect": 0}}
    assert mr.chance_agreement(confusion) == pytest.approx(0.84)
    assert mr.target_status(8.0, 8.0) == "met"
    assert mr.target_status(8.01, 8.0) == "**not met**"
    assert mr.target_status(None, 8.0) == "n/a"
    assert mr.nice_max(46.1) == (50, 10)
    assert mr.nice_max(1.0) == (1.0, 0.2)


def test_real_report_matches_real_metrics() -> None:
    """The committed report numbers equal the real metrics.json (skipped without results)."""
    try:
        runs = mr.choose_runs(mr.RESULTS_DIR)
    except SystemExit:
        pytest.skip("no evaluation results on this machine")
    am = json.loads((runs.answers / "metrics.json").read_text(encoding="utf-8"))
    text, _ = mr.build_report(runs)
    full, base = am["systems"]["full"]["overall"], am["systems"]["baseline"]["overall"]
    for key in ("correctness", "faithfulness_micro", "citation_precision"):
        assert f"| {mr.f3(full[key])} | {mr.f3(base[key])} |" in text, key
