"""Export answers for the author to hand-label (judge calibration, CLAUDE.md section 11).

    .venv\\Scripts\\python.exe -m eval.export_calibration            # latest *_answers run
    .venv\\Scripts\\python.exe -m eval.export_calibration --run 20261107-180000_answers
    .venv\\Scripts\\python.exe -m eval.export_calibration --run <run A> --run <run B>   # pooled (M18b)

Writes, under eval/calibration/:
    <run>_labels.csv     30 answers (fixed seed) with an empty `human_correctness` column.
                         STRATIFIED (M18b; CLAUDE.md section 13.2): at least 8 of them are
                         answers the judge labelled partially_correct or incorrect, the rest
                         correct, in a shuffled order. A random sample of a mostly-correct
                         run gives almost only "correct" pairs, and kappa is then
                         meaningless (the M5 calibration: kappa -0.042).
                         Fill it with: correct | partially_correct | incorrect
                         (same rules as the judge: see eval/judge.py), optional notes.
    <run>_judge_key.json the judge's labels for the same rows, kept in a SEPARATE file so
                         they do not influence the human labels.
Then run: .venv\\Scripts\\python.exe -m eval.run_judge_calibration --run <run>
"""

import argparse
import csv
import json
import random
from pathlib import Path

from app.config import PROJECT_ROOT

RESULTS = PROJECT_ROOT / "eval" / "results"
CALIBRATION = PROJECT_ROOT / "eval" / "calibration"
SAMPLE_SIZE = 30
MIN_NOT_CORRECT = 8
SEED = 42
COLUMNS = ["sample", "id", "system", "category", "question", "gold_answer", "answer",
           "human_correctness", "human_notes"]


def latest_answers_run() -> Path:
    runs = sorted(p for p in RESULTS.glob("*_answers") if (p / "answers.jsonl").exists())
    if not runs:
        raise SystemExit("No *_answers run found in eval/results/. Run eval.run_answer_eval first.")
    return runs[-1]


def stratified_sample(judged: list[dict], size: int, min_not_correct: int,
                      rng: random.Random) -> list[dict]:
    """`size` answers with at least `min_not_correct` the judge did not label correct (all of
    them if there are fewer), the rest correct, shuffled."""
    not_correct = [a for a in judged if a["judge"].get("correctness") != "correct"]
    correct = [a for a in judged if a["judge"].get("correctness") == "correct"]
    share = round(size * len(not_correct) / len(judged)) if judged else 0
    k = min(len(not_correct), max(min_not_correct, share), size)
    sample = rng.sample(not_correct, k) + rng.sample(correct, min(size - k, len(correct)))
    rng.shuffle(sample)
    return sample


def export(run_dirs: Path | list[Path], size: int = SAMPLE_SIZE, seed: int = SEED,
           min_not_correct: int = MIN_NOT_CORRECT) -> tuple[Path, Path]:
    """Sample from one run, or pool several (the sample runs of M18b are 24 answers each).
    Pooled files are named <run A>+<run B>; each row says which run it came from."""
    run_dirs = [run_dirs] if isinstance(run_dirs, Path) else list(run_dirs)
    judged = []
    for run_dir in run_dirs:
        for line in (run_dir / "answers.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip() and (a := json.loads(line)).get("judge"):
                judged.append({**a, "run": run_dir.name})
    name = "+".join(d.name for d in run_dirs)
    rng = random.Random(seed)
    sample = stratified_sample(judged, size, min_not_correct, rng)
    CALIBRATION.mkdir(parents=True, exist_ok=True)
    labels = CALIBRATION / f"{name}_labels.csv"
    key = CALIBRATION / f"{name}_judge_key.json"
    if labels.exists():
        raise SystemExit(f"{labels.name} already exists; not overwriting your labels.")
    with labels.open("w", newline="", encoding="utf-8-sig") as f:  # BOM so Excel opens UTF-8
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for i, a in enumerate(sample, 1):
            w.writerow({"sample": i, "id": a["id"], "system": a["system"],
                        "category": a["category"], "question": a["question"],
                        "gold_answer": a["gold_answer"], "answer": a["answer"],
                        "human_correctness": "", "human_notes": ""})
    strata = {"not_correct": sum(a["judge"]["correctness"] != "correct" for a in sample),
              "correct": sum(a["judge"]["correctness"] == "correct" for a in sample),
              "judged_in_run": len(judged), "seed": seed, "min_not_correct": min_not_correct}
    key.write_text(json.dumps({"_strata": strata,
                               **{str(i): {"id": a["id"], "system": a["system"], "run": a["run"],
                                           "judge_correctness": a["judge"]["correctness"]}
                                  for i, a in enumerate(sample, 1)}}, indent=2), encoding="utf-8")
    if strata["not_correct"] < min_not_correct:
        print(f"WARNING: only {strata['not_correct']} answers were judged not correct; the sample "
              f"has fewer than {min_not_correct} (CLAUDE.md section 13.2).")
    return labels, key


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", action="append",
                        help="folder name under eval/results/ (repeat to pool runs; default: latest *_answers)")
    args = parser.parse_args()
    run_dirs = [RESULTS / r for r in args.run] if args.run else [latest_answers_run()]
    labels, key = export(run_dirs)
    print(f"Wrote {labels.relative_to(PROJECT_ROOT)} (fill human_correctness) and "
          f"{key.relative_to(PROJECT_ROOT)} (do not open while labelling).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
