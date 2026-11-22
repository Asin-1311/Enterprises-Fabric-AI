"""Fine-tune the re-ranker on an exported dataset and decide whether it may be used (M15c;
app/learning/reranker_finetune.py).

    .venv\\Scripts\\python.exe -m scripts.finetune_reranker data\\learning\\reranker\\<ts>.jsonl

Steps: drop pairs whose query is an evaluation question; train a copy of the current
re-ranker (RERANKER_MODEL) into models/reranker/<ts>/; re-rank the evaluation questions'
search candidates with both models; write the decision to the model's promotion.json and
the measured numbers to eval/results/<ts>_reranker_finetune/. The model is used only if it
beat the current one AND RERANKER_MODEL is then pointed at its folder by the author.
CPU only; no LLM calls.
"""

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

from app.config import PROJECT_ROOT, get_settings
from app.learning import reranker_finetune as ft
from app.security.redaction import snapshot_json

MODELS_DIR = PROJECT_ROOT / "models" / "reranker"
RESULTS_DIR = PROJECT_ROOT / "eval" / "results"
QUESTIONS = PROJECT_ROOT / "eval" / "questions.jsonl"
REPLAY = PROJECT_ROOT / "eval" / "replay" / "routing_replay.jsonl"
MIN_PAIRS = 20


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main(argv: list[str] | None = None) -> int:
    from app.retrieval.dense import QdrantInUseError
    from app.retrieval.hybrid import build_searcher
    from app.retrieval.rerank import CrossEncoderReranker

    parser = argparse.ArgumentParser(description="Fine-tune and gate the re-ranker.")
    parser.add_argument("dataset", type=Path, help="data/learning/reranker/<ts>.jsonl")
    parser.add_argument("--epochs", type=int, default=1)
    args = parser.parse_args(argv)
    settings = get_settings()
    pairs = ft.without_eval_questions(ft.load_pairs(args.dataset),
                                      [q["question"] for q in _jsonl(QUESTIONS)])
    if len(pairs) < MIN_PAIRS or {p["label"] for p in pairs} != {0, 1}:
        print(f"Not enough training data: {len(pairs)} usable pairs (need {MIN_PAIRS}, both labels). "
              "Nothing trained.")
        return 1
    try:
        searcher = build_searcher(settings)
    except QdrantInUseError as exc:
        print(exc)
        return 2
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    model_dir = ft.finetune(settings.reranker_model, pairs, MODELS_DIR / stamp, epochs=args.epochs)
    cases = ft.build_eval_cases(searcher, _jsonl(REPLAY))
    current, current_rows = ft.rerank_eval(CrossEncoderReranker(settings.reranker_model), cases)
    candidate, candidate_rows = ft.rerank_eval(CrossEncoderReranker(str(model_dir), gate=False), cases)
    decision = ft.write_promotion(model_dir, base_model=settings.reranker_model,
                                  dataset=str(args.dataset), pairs=len(pairs), cases=len(cases),
                                  current=current, candidate=candidate)
    out = RESULTS_DIR / f"{stamp}_reranker_finetune"
    out.mkdir(parents=True)
    (out / "metrics.json").write_text(json.dumps(decision, indent=2), encoding="utf-8")
    with (out / "per_question.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", *(f"current_{m}" for m in ft.METRICS), *(f"candidate_{m}" for m in ft.METRICS)])
        for c, n in zip(current_rows, candidate_rows, strict=True):
            w.writerow([c["id"], *(c[m] for m in ft.METRICS), *(n[m] for m in ft.METRICS)])
    (out / "config.json").write_text(snapshot_json({"base_model": settings.reranker_model,
                                                 "epochs": args.epochs, "model_dir": str(model_dir)}), encoding="utf-8")
    verdict = "BEATS the current model" if decision["beats_current"] else "does NOT beat the current model"
    print(f"Fine-tuned model {verdict}: {decision['reason']}.\n  current   {current}\n"
          f"  candidate {candidate}\n  model: {model_dir}\n  results: {out.relative_to(PROJECT_ROOT)}")
    if decision["beats_current"]:
        print(f"To use it, set RERANKER_MODEL={model_dir} in .env.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
