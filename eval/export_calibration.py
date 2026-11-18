"""Export answers for the author to hand-label (judge calibration, CLAUDE.md section 11).

    .venv\\Scripts\\python.exe -m eval.export_calibration            # latest *_answers run
    .venv\\Scripts\\python.exe -m eval.export_calibration --run 20261107-180000_answers

Writes, under eval/calibration/:
    <run>_labels.csv     30 answers (fixed seed) with an empty `human_correctness` column.
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
SEED = 42
COLUMNS = ["sample", "id", "system", "category", "question", "gold_answer", "answer",
           "human_correctness", "human_notes"]


def latest_answers_run() -> Path:
    runs = sorted(p for p in RESULTS.glob("*_answers") if (p / "answers.jsonl").exists())
    if not runs:
        raise SystemExit("No *_answers run found in eval/results/. Run eval.run_answer_eval first.")
    return runs[-1]


def export(run_dir: Path, size: int = SAMPLE_SIZE, seed: int = SEED) -> tuple[Path, Path]:
    answers = [json.loads(line) for line in (run_dir / "answers.jsonl")
               .read_text(encoding="utf-8").splitlines() if line.strip()]
    judged = [a for a in answers if a.get("judge")]
    rng = random.Random(seed)
    sample = rng.sample(judged, min(size, len(judged)))
    CALIBRATION.mkdir(parents=True, exist_ok=True)
    labels = CALIBRATION / f"{run_dir.name}_labels.csv"
    key = CALIBRATION / f"{run_dir.name}_judge_key.json"
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
    key.write_text(json.dumps({str(i): {"id": a["id"], "system": a["system"],
                                        "judge_correctness": a["judge"]["correctness"]}
                               for i, a in enumerate(sample, 1)}, indent=2), encoding="utf-8")
    return labels, key


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", help="folder name under eval/results/ (default: latest *_answers)")
    args = parser.parse_args()
    run_dir = RESULTS / args.run if args.run else latest_answers_run()
    labels, key = export(run_dir)
    print(f"Wrote {labels.relative_to(PROJECT_ROOT)} (fill human_correctness) and "
          f"{key.relative_to(PROJECT_ROOT)} (do not open while labelling).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
