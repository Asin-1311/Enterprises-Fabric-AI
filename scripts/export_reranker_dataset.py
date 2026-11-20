"""Export labelled (query, chunk) pairs for the re-ranker, and user corrections as
candidate evaluation questions (M15c; app/learning/reranker_dataset.py, corrections.py).

    .venv\\Scripts\\python.exe -m scripts.export_reranker_dataset

Writes (both git-ignored: they hold users' masked questions and firm text):
  data/learning/reranker/<timestamp>.jsonl          pairs: query, chunk_id, text, label, label_source
  data/learning/eval_candidates/<timestamp>.jsonl   corrections, "reviewed": false
No model calls.
"""

import json
import sys
from datetime import datetime
from pathlib import Path

from app.config import PROJECT_ROOT, get_settings
from app.learning import corrections, reranker_dataset

LEARNING_DIR = PROJECT_ROOT / "data" / "learning"
QUESTIONS = PROJECT_ROOT / "eval" / "questions.jsonl"


def export(sessions, settings, out_dir: Path = LEARNING_DIR, questions: Path = QUESTIONS) -> dict:
    """Write both files; returns what was written."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    pairs, summary = reranker_dataset.export_pairs(sessions, settings)
    known = {json.loads(line)["question"] for line in questions.read_text(encoding="utf-8").splitlines()
             if line.strip()} if questions.exists() else set()
    candidates = corrections.correction_candidates(sessions, known)
    written = {"summary": summary, "pairs": None, "candidates": None}
    if pairs:
        path = out_dir / "reranker" / f"{stamp}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(reranker_dataset.to_jsonl(pairs), encoding="utf-8")
        written["pairs"] = path
    if candidates:
        path = out_dir / "eval_candidates" / f"{stamp}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(corrections.to_jsonl(candidates), encoding="utf-8")
        written["candidates"] = path
    written["candidate_count"] = len(candidates)
    return written


def main() -> int:
    from app.adapters import open_metadata_store

    settings = get_settings()
    metadata = open_metadata_store(settings)
    metadata.initialise()
    w = export(metadata.sessions, settings)
    s = w["summary"]
    print(f"Re-ranker pairs: {s.positives} positive, {s.negatives} negative, from {s.answers_used} "
          f"answers; skipped {s.skipped or 'none'}.")
    print(f"  -> {w['pairs'] or 'nothing written'}")
    print(f"Candidate evaluation questions from corrections: {w['candidate_count']} "
          f"(reviewed: false) -> {w['candidates'] or 'nothing written'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
