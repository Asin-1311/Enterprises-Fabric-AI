"""Judge calibration: agreement between the LLM judge and the author's hand labels.

    .venv\\Scripts\\python.exe -m eval.run_judge_calibration --run <answers run folder name>

Reads eval/calibration/<run>_labels.csv (filled by the author) and <run>_judge_key.json.
Reports, over the rows the author has labelled:
- exact agreement on the 3 classes (correct / partially_correct / incorrect),
- Cohen's kappa (agreement corrected for chance),
- binary agreement (correct vs not correct),
- the confusion matrix (rows = human, columns = judge).
Writes eval/results/<timestamp>_calibration/. No LLM calls.
"""

import argparse
import csv
import json
from collections import Counter
from datetime import datetime

from app.config import PROJECT_ROOT

LABELS = ("correct", "partially_correct", "incorrect")
CALIBRATION = PROJECT_ROOT / "eval" / "calibration"


def cohens_kappa(pairs: list[tuple[str, str]], labels=LABELS) -> float | None:
    """Cohen's kappa for (human, judge) label pairs; None if undefined."""
    n = len(pairs)
    if n == 0:
        return None
    observed = sum(1 for h, j in pairs if h == j) / n
    hc, jc = Counter(h for h, _ in pairs), Counter(j for _, j in pairs)
    expected = sum(hc[label] * jc[label] for label in labels) / (n * n)
    return None if expected == 1 else (observed - expected) / (1 - expected)


def main() -> int:
    parser = argparse.ArgumentParser(description="Judge vs human agreement.")
    parser.add_argument("--run", required=True, help="answers run folder name, e.g. 20261107-180000_answers")
    args = parser.parse_args()
    labels_path = CALIBRATION / f"{args.run}_labels.csv"
    key = json.loads((CALIBRATION / f"{args.run}_judge_key.json").read_text(encoding="utf-8"))
    with labels_path.open(newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    pairs, items, bad = [], [], []
    for r in rows:
        human = r["human_correctness"].strip().lower()
        if not human:
            continue
        if human not in LABELS:
            bad.append(r["sample"])
            continue
        judge = key[r["sample"]]["judge_correctness"]
        pairs.append((human, judge))
        items.append({"sample": r["sample"], "id": r["id"], "system": r["system"],
                      "human": human, "judge": judge, "agree": human == judge})
    if bad:
        raise SystemExit(f"Rows {bad} have an invalid label; use one of {LABELS}.")
    if not pairs:
        raise SystemExit(f"No human labels yet in {labels_path.name}.")
    n = len(pairs)
    metrics = {
        "answers_run": args.run, "labelled": n, "exported": len(rows),
        "exact_agreement": sum(1 for h, j in pairs if h == j) / n,
        "cohens_kappa": cohens_kappa(pairs),
        "binary_agreement_correct_vs_not": sum(
            1 for h, j in pairs if (h == "correct") == (j == "correct")) / n,
        "confusion_human_rows_judge_cols": {
            h: {j: sum(1 for a, b in pairs if a == h and b == j) for j in LABELS} for h in LABELS},
    }
    out = PROJECT_ROOT / "eval" / "results" / f"{datetime.now():%Y%m%d-%H%M%S}_calibration"
    out.mkdir(parents=True)
    (out / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    with (out / "per_question.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(items[0]))
        w.writeheader()
        w.writerows(items)
    (out / "config.json").write_text(json.dumps({"labels_file": labels_path.name}, indent=2),
                                     encoding="utf-8")
    (out / "model_ids.json").write_text(json.dumps({"note": "see the answers run's model_ids.json"}),
                                        encoding="utf-8")
    print(f"labelled={n}  exact agreement={metrics['exact_agreement']:.2f}  "
          f"kappa={metrics['cohens_kappa']}  -> {out.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
