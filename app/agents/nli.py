"""The second verification opinion: a small NLI model (spec 7.8; CLAUDE.md section 7,
step 6; Gap Register R-7.8, R-9-span; M14b).

Natural language inference (NLI) scores a pair (premise, hypothesis) as entailment,
neutral or contradiction. Here the premise is one evidence sentence of a chunk a claim
cites, and the hypothesis is the claim (its citation markers removed). For every claim
the LLM verifier accepted:

- entailment     some cited sentence entails it (probability >= NLI_ENTAILMENT_THRESHOLD)
                 -> those sentences are the claim's supporting text (the citation span);
- contradiction  no sentence entails it and some sentence contradicts it
                 (>= NLI_CONTRADICTION_THRESHOLD) -> the claim is kept but FLAGGED
                 "check this point": two independent checkers disagree;
- neutral        neither -> kept as the LLM decided (NLI models are cautious with
                 paraphrase and numbers in context; neutral is not evidence of an error).

The model runs locally on CPU (sentence-transformers CrossEncoder, like the re-ranker);
no document text leaves the machine. Model: cross-encoder/nli-deberta-v3-xsmall (author's
choice, M14b): about 70M parameters, fast enough to score every claim's sentences.
"""

import re
from dataclasses import dataclass, field
from typing import Protocol

LABELS = ("entailment", "neutral", "contradiction")
_CITATION_MARK = re.compile(r"\s*\[[^\]\s]+:[^\]\s]+\]")


class NliScorer(Protocol):
    def scores(self, pairs: list[tuple[str, str]]) -> list[dict[str, float]]:
        """One {entailment, neutral, contradiction} probability dict per (premise, hypothesis)."""
        ...


class CrossEncoderNli:
    """A sentence-transformers NLI cross-encoder on CPU."""

    def __init__(self, model_name: str) -> None:
        from sentence_transformers import CrossEncoder  # heavy import, load lazily

        self.model = CrossEncoder(model_name, device="cpu")
        # The label order differs between NLI models: read it from the model's config.
        id2label = self.model.model.config.id2label
        self.labels = [str(id2label[i]).lower() for i in range(len(id2label))]
        if set(self.labels) != set(LABELS):
            raise ValueError(f"{model_name} is not a 3-way NLI model: {self.labels}")

    def scores(self, pairs: list[tuple[str, str]]) -> list[dict[str, float]]:
        if not pairs:
            return []
        probs = self.model.predict(pairs, apply_softmax=True, show_progress_bar=False)
        return [{label: float(p) for label, p in zip(self.labels, row)} for row in probs]


@dataclass
class ClaimCheck:
    """The NLI view of one claim."""

    label: str                    # entailment | neutral | contradiction
    entailment: float             # best entailment over the cited sentences
    contradiction: float          # best contradiction over the cited sentences
    support: dict[str, list[str]] = field(default_factory=dict)  # chunk id -> entailing sentences


def claim_text(text: str) -> str:
    """The claim without its [chunk_id] citation markers."""
    return _CITATION_MARK.sub("", text).strip()


def check_claims(claims: list[tuple[int, str, list[str]]], sentences: dict[str, list[str]],
                 scorer: NliScorer, entail_at: float = 0.5,
                 contradict_at: float = 0.5) -> dict[int, ClaimCheck]:
    """NLI over every (cited sentence, claim) pair, in one batch.

    `claims` are (id, text, cited chunk ids); `sentences` are each chunk's evidence
    sentences, verbatim from the chunk. Claims without cited sentences are not checked."""
    pairs: list[tuple[str, str]] = []
    owners: list[tuple[int, str, str]] = []  # (claim id, chunk id, sentence)
    for claim_id, text, cites in claims:
        hypothesis = claim_text(text)
        for cid in cites:
            for sentence in sentences.get(cid, []):
                pairs.append((sentence, hypothesis))
                owners.append((claim_id, cid, sentence))
    results: dict[int, ClaimCheck] = {}
    for (claim_id, cid, sentence), score in zip(owners, scorer.scores(pairs)):
        check = results.setdefault(claim_id, ClaimCheck("neutral", 0.0, 0.0))
        check.entailment = max(check.entailment, score["entailment"])
        check.contradiction = max(check.contradiction, score["contradiction"])
        if score["entailment"] >= entail_at:
            check.support.setdefault(cid, []).append(sentence)
    for check in results.values():
        if check.entailment >= entail_at:
            check.label = "entailment"
        elif check.contradiction >= contradict_at:
            check.label = "contradiction"
    return results
