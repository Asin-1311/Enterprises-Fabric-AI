"""M18a: the weekly human-review sample and the judge-drift report
(scripts/sample_weekly_review.py). The labels below are test inputs, not results.

Covers: R-13.2-judge (the weekly review sampler draws a fixed share of the week's answers,
stores the reviewer's labels, and reports judge drift over time).
"""

import csv
import json
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from app.db.models import Answer, Query, User
from app.db.session import init_db, make_engine, make_session_factory
from scripts import sample_weekly_review as review


@pytest.fixture()
def sessions(tmp_path: Path):
    engine = make_engine(f"sqlite:///{(tmp_path / 'review.db').as_posix()}")
    init_db(engine)
    factory = make_session_factory(engine)
    with factory() as s:
        s.add(User(user_id="S01", name="Riya Kapoor", role="staff", synthetic=True))
        s.flush()
        week_end = datetime(2026, 11, 22, 12)
        for i in range(60):  # 40 in the week ending 22 November, 20 the week before
            asked = week_end - timedelta(days=(i % 20) / 3 if i < 40 else 8 + i % 5)
            q = Query(user_id="S01", text=f"question {i}", intent="factual", created_at=asked)
            s.add(q)
            s.flush()
            s.add(Answer(query_id=q.id, text=f"answer {i}", confidence=0.9, status="answered"))
        s.commit()
    return factory


def _rows(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_draw_takes_a_fixed_reproducible_share_of_the_week(sessions, tmp_path: Path) -> None:
    """Covers: R-13.2-judge (5% of the week's answers, the same sample every time)."""
    out = review.draw(sessions, date(2026, 11, 22), 0.05, review_dir=tmp_path / "a")
    rows = _rows(out)
    assert len(rows) == 2  # round(40 x 5%)
    assert all(r["human_label"] == "" and r["judge_label"] == "" for r in rows)
    asked = [datetime.fromisoformat(r["asked_at"]) for r in rows]
    start, end = review.week_window(date(2026, 11, 22))
    assert all(start <= t < end for t in asked)  # nothing from the week before
    again = review.draw(sessions, date(2026, 11, 22), 0.05, review_dir=tmp_path / "b")
    assert [r["answer_id"] for r in _rows(again)] == [r["answer_id"] for r in rows]
    with pytest.raises(SystemExit, match="drawn once"):  # labels in a sample are never overwritten
        review.draw(sessions, date(2026, 11, 22), 0.05, review_dir=tmp_path / "a")
    assert review.choose([], 0.05, date(2026, 11, 22)) == []
    assert len(review.choose([1, 2, 3], 0.05, date(2026, 11, 22))) == 1  # at least one


def test_drift_reports_agreement_per_week_and_never_answer_text(sessions, tmp_path: Path) -> None:
    """Covers: R-13.2-judge (stored labels give agreement and kappa per week; no guesses)."""
    labelled = review.draw(sessions, date(2026, 11, 22), 0.25, review_dir=tmp_path)  # 10 answers
    rows = _rows(labelled)
    human = ["correct"] * 6 + ["partially_correct"] * 2 + ["incorrect"] * 2
    judge = ["correct"] * 5 + ["partially_correct"] + ["partially_correct"] * 2 + ["incorrect"] * 2
    for r, h, j in zip(rows, human, judge):
        r["human_label"], r["judge_label"], r["reviewer"] = h, j, "author"
    with labelled.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=review.COLUMNS)
        w.writeheader()
        w.writerows(rows)
    review.draw(sessions, date(2026, 11, 15), 0.05, review_dir=tmp_path)  # drawn, not labelled

    out = review.drift(review_dir=tmp_path, results=tmp_path / "results")
    assert out.name.endswith("_review")
    m = json.loads((out / "metrics.json").read_text(encoding="utf-8"))
    week = m["weeks"]["2026-11-22"]
    assert (week["sampled"], week["pairs"], week["exact_agreement"]) == (10, 10, 0.9)
    assert week["cohens_kappa"] == round(review.cohens_kappa(list(zip(human, judge))), 4)
    assert m["weeks"]["2026-11-15"]["exact_agreement"] is None  # no labels: no number
    assert m["weeks_with_pairs"] == 1
    stored = "".join(p.read_text(encoding="utf-8") for p in out.iterdir())
    assert "answer 0" not in stored and "question 0" not in stored
