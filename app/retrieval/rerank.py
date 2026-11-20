"""Cross-encoder re-ranking with BAAI/bge-reranker-base (CPU).

A cross-encoder reads the question and the chunk TOGETHER and outputs one relevance
score, which is more accurate than comparing two separately computed embeddings, but too
slow to run over the whole corpus. So it only re-orders the ~20 hybrid candidates.

A fine-tuned re-ranker (M15c) is a local folder; it is loaded only if it passed the gate
recorded in its promotion.json (`check_promoted`).
"""

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol


class Reranker(Protocol):
    """Scores (query, passage) pairs; higher = more relevant, roughly in [0, 1]."""

    def score(self, query: str, passages: Sequence[str]) -> list[float]: ...


class RerankerNotPromoted(RuntimeError):
    """A local fine-tuned re-ranker that has not passed the gate (M15c)."""


def check_promoted(model_name: str) -> None:
    """A model in a local folder (a fine-tuned re-ranker) is used only if its
    promotion.json says it beat the current model on the evaluation
    (app/learning/reranker_finetune.py). Hub model names are not affected."""
    folder = Path(model_name)
    if not folder.is_dir():
        return
    record = folder / "promotion.json"
    try:
        decision = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RerankerNotPromoted(f"{folder} has no readable promotion.json: run "
                                  "scripts/finetune_reranker.py to evaluate it first.") from exc
    if decision.get("beats_current") is not True:
        raise RerankerNotPromoted(f"{folder} did not beat the current re-ranker "
                                  f"({decision.get('reason', 'no reason recorded')}).")


class CrossEncoderReranker:
    """sentence-transformers CrossEncoder wrapper (sigmoid scores in [0, 1])."""

    def __init__(self, model_name: str, *, gate: bool = True) -> None:
        from sentence_transformers import CrossEncoder  # heavy import, load lazily

        if gate:
            check_promoted(model_name)
        self.model = CrossEncoder(model_name, device="cpu")

    def score(self, query: str, passages: Sequence[str]) -> list[float]:
        if not passages:
            return []
        return [float(s) for s in self.model.predict([(query, p) for p in passages])]
