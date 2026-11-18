"""Cross-encoder re-ranking with BAAI/bge-reranker-base (CPU).

A cross-encoder reads the question and the chunk TOGETHER and outputs one relevance
score, which is more accurate than comparing two separately computed embeddings, but too
slow to run over the whole corpus. So it only re-orders the ~20 hybrid candidates.
"""

from collections.abc import Sequence
from typing import Protocol


class Reranker(Protocol):
    """Scores (query, passage) pairs; higher = more relevant, roughly in [0, 1]."""

    def score(self, query: str, passages: Sequence[str]) -> list[float]: ...


class CrossEncoderReranker:
    """sentence-transformers CrossEncoder wrapper (sigmoid scores in [0, 1])."""

    def __init__(self, model_name: str) -> None:
        from sentence_transformers import CrossEncoder  # heavy import, load lazily

        self.model = CrossEncoder(model_name, device="cpu")

    def score(self, query: str, passages: Sequence[str]) -> list[float]:
        if not passages:
            return []
        return [float(s) for s in self.model.predict([(query, p) for p in passages])]
