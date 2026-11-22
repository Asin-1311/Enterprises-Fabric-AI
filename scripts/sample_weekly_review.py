"""Weekly sample of answers for human review, and judge drift over time (CLAUDE.md section
13.2; M18a).

    python -m scripts.sample_weekly_review draw [--week-ending 2026-11-22] [--fraction 0.05]
    python -m scripts.sample_weekly_review drift

draw
    Picks a fixed share (REVIEW_SAMPLE_FRACTION, default 5%) of the answers given in the
    seven days ending on --week-ending (default: today), at least one if there were any.
    The random choice is seeded by the week, so the same week always gives the same sample.
    Writes data/review/<week-ending>/sample.csv. That folder is git-ignored because it holds
    answer text. The reviewer fills `human_label` (correct | partially_correct | incorrect),
    `reviewer` and `notes`. `judge_label` is filled by the answer-evaluation judge (M18b), a
    paid call that never runs automatically. An existing sample is never overwritten, so
    labels cannot be lost.

drift
    Reads every week's sample and writes eval/results/<timestamp>_review/: per week, the
    answers sampled, how many a person and the judge labelled, their exact agreement and
    Cohen's kappa. Only aggregate numbers, never answer text. A week without labels from
    both sides reports None: nothing is estimated.
"""

import argparse
import csv
import json
import random
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

from sqlalchemy import select

from app.config import PROJECT_ROOT, get_settings
from app.security.redaction import snapshot_json
from eval.run_judge_calibration import LABELS, cohens_kappa

REVIEW_DIR = PROJECT_ROOT / "data" / "review"
RESULTS = PROJECT_ROOT / "eval" / "results"
COLUMNS = ["answer_id", "asked_at", "user_id", "status", "confidence", "degraded", "question",
           "answer", "human_label", "reviewer", "notes", "judge_label"]


def week_window(week_ending: date) -> tuple[datetime, datetime]:
    """[start, end) of the seven days ending on week_ending (inclusive), naive UTC as stored."""
    end = datetime.combine(week_ending + timedelta(days=1), time.min)
    return end - timedelta(days=7), end


def choose(answer_ids: list[int], fraction: float, week_ending: date) -> list[int]:
    """A reproducible sample of round(n x fraction) answers, at least one when there are any."""
    if not answer_ids:
        return []
    k = max(1, round(len(answer_ids) * fraction))
    rng = random.Random(f"weekly-review-{week_ending.isoformat()}")
    return sorted(rng.sample(sorted(answer_ids), k))


def draw(sessions, week_ending: date, fraction: float, review_dir: Path = REVIEW_DIR) -> Path:
    from app.db.models import Answer, Query

    out = review_dir / week_ending.isoformat() / "sample.csv"
    if out.exists():
        raise SystemExit(f"{out} exists; a week's sample is drawn once (labels may be in it).")
    start, end = week_window(week_ending)
    with sessions() as s:
        rows = s.execute(select(Answer, Query).join(Query, Answer.query_id == Query.id)
                         .where(Query.created_at >= start, Query.created_at < end)).all()
        by_id = {a.id: (a, q) for a, q in rows}
        picked = choose(list(by_id), fraction, week_ending)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=COLUMNS)
            w.writeheader()
            for answer_id in picked:
                a, q = by_id[answer_id]
                w.writerow({"answer_id": a.id, "asked_at": q.created_at.isoformat(timespec="seconds"),
                            "user_id": q.user_id, "status": a.status, "confidence": a.confidence,
                            "degraded": a.degraded, "question": q.text, "answer": a.text,
                            "human_label": "", "reviewer": "", "notes": "", "judge_label": ""})
    print(f"{len(picked)} of {len(by_id)} answers from {start:%Y-%m-%d} to "
          f"{end - timedelta(days=1):%Y-%m-%d} sampled to {out}")
    return out


def week_drift(rows: list[dict]) -> dict:
    """Agreement between the human and the judge on one week's sample."""
    human = [r for r in rows if r["human_label"] in LABELS]
    judged = [r for r in rows if r["judge_label"] in LABELS]
    pairs = [(r["human_label"], r["judge_label"]) for r in human if r["judge_label"] in LABELS]
    return {"sampled": len(rows), "human_labelled": len(human), "judge_labelled": len(judged),
            "pairs": len(pairs),
            "exact_agreement": round(sum(h == j for h, j in pairs) / len(pairs), 4) if pairs else None,
            "cohens_kappa": None if not pairs or (k := cohens_kappa(pairs)) is None else round(k, 4)}


def drift(review_dir: Path = REVIEW_DIR, results: Path = RESULTS) -> Path:
    weeks = {}
    for sample in sorted(review_dir.glob("*/sample.csv")):
        with sample.open(newline="", encoding="utf-8") as f:
            weeks[sample.parent.name] = week_drift(list(csv.DictReader(f)))
    out = results / f"{datetime.now():%Y%m%d-%H%M%S}_review"
    out.mkdir(parents=True)
    labelled = [w for w in weeks.values() if w["pairs"]]
    metrics = {"weeks": weeks, "weeks_with_pairs": len(labelled),
               "fraction": get_settings().review_sample_fraction,
               "note": "Human labels against the judge's on the weekly samples; None where a week "
                       "has no label pairs. No answer text is stored here."}
    (out / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    with (out / "per_question.csv").open("w", newline="", encoding="utf-8") as f:  # one row per week
        w = csv.DictWriter(f, fieldnames=["week_ending", *next(iter(weeks.values()), {}).keys()]
                           if weeks else ["week_ending"])
        w.writeheader()
        for week, row in weeks.items():
            w.writerow({"week_ending": week, **row})
    (out / "config.json").write_text(snapshot_json({"review_dir": "data/review (git-ignored)"}),
                                     encoding="utf-8")
    print(f"Judge drift over {len(weeks)} week(s), {len(labelled)} with label pairs: {out}")
    return out


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Weekly answer sample for human review.")
    sub = parser.add_subparsers(dest="command", required=True)
    d = sub.add_parser("draw")
    d.add_argument("--week-ending", type=date.fromisoformat, default=date.today())
    d.add_argument("--fraction", type=float, default=settings.review_sample_fraction)
    sub.add_parser("drift")
    args = parser.parse_args(argv)
    if args.command == "draw":
        from app.adapters import open_metadata_store

        metadata = open_metadata_store(settings)
        metadata.initialise()
        draw(metadata.sessions, args.week_ending, args.fraction)
    else:
        drift()
    return 0


if __name__ == "__main__":
    sys.exit(main())
