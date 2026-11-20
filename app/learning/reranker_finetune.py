"""Fine-tuning the re-ranker on exported pairs, and the gate that decides whether the result
may be used (spec 7.9; CLAUDE.md section 11; M15c).

1. Train: the cross-encoder (default BAAI/bge-reranker-base) is trained on the labelled
   pairs from app/learning/reranker_dataset.py with binary cross-entropy: it learns to
   give cited chunks a higher score than chunks it ranked but the answer did not need. A
   plain PyTorch loop (AdamW, fixed seed), so no training framework is needed.
2. Evaluate: the current model and the fine-tuned one re-rank the SAME candidates for the
   evaluation questions (hybrid search results for each question, as its user), scored by
   document recall@5, MRR and nDCG@10 (eval/metrics.py). Pairs whose query is an
   evaluation question are left out of training, so the test is not on training data.
3. Gate: the fine-tuned model "beats" the current one only if its nDCG@10 is higher and no
   metric is lower. Its folder gets promotion.json either way; the re-ranker refuses to
   load a local model whose promotion.json does not say it beat the current one
   (app/retrieval/rerank.py). Using it is then one setting: RERANKER_MODEL=<that folder>.

Nothing here claims an improvement: whether one happens is what step 3 measures, on real
exported data, and the measured numbers go to eval/results/<timestamp>_reranker_finetune/.
"""

import json
import random
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from eval.metrics import dedupe_docs, mean, ndcg_at_k, recall_at_k, reciprocal_rank

PROMOTION_FILE = "promotion.json"
METRICS = ("recall@5", "mrr", "ndcg@10")


@dataclass(frozen=True)
class EvalCase:
    """One evaluation question with the candidates its search returned."""

    id: str
    query: str
    gold_doc_ids: frozenset[str]
    candidates: tuple[tuple[str, str, str], ...]  # (chunk_id, doc_id, text) in search order


def load_pairs(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def without_eval_questions(pairs: list[dict], eval_questions: Sequence[str]) -> list[dict]:
    """Drop pairs that came from an evaluation question, as the whole question or as its
    query (a sub-query of it would also leak), so the gate never tests on training data."""
    held_out = {_norm(q) for q in eval_questions}
    return [p for p in pairs if _norm(p["query"]) not in held_out
            and _norm(p.get("question", "")) not in held_out]


def finetune(base_model: str, pairs: list[dict], out_dir: Path, *, epochs: int = 1,
             batch_size: int = 8, learning_rate: float = 2e-5, max_length: int = 384,
             seed: int = 13) -> Path:
    """Train a copy of `base_model` on the pairs and save it to `out_dir`."""
    import torch
    from sentence_transformers import CrossEncoder

    labels = {int(p["label"]) for p in pairs}
    if labels != {0, 1}:
        raise ValueError("Fine-tuning needs both positive and negative pairs.")
    random.seed(seed)
    torch.manual_seed(seed)
    encoder = CrossEncoder(base_model, device="cpu", num_labels=1)
    model, tokenizer = encoder.model, encoder.tokenizer
    optimiser = torch.optim.AdamW(model.parameters(), lr=learning_rate)
    loss_fn = torch.nn.BCEWithLogitsLoss()
    order = list(range(len(pairs)))
    model.train()
    for _ in range(epochs):
        random.shuffle(order)
        for start in range(0, len(order), batch_size):
            batch = [pairs[i] for i in order[start:start + batch_size]]
            enc = tokenizer([p["query"] for p in batch], [p["text"] for p in batch], padding=True,
                            truncation=True, max_length=max_length, return_tensors="pt")
            logits = model(**enc).logits.view(-1)
            loss = loss_fn(logits, torch.tensor([float(p["label"]) for p in batch]))
            optimiser.zero_grad()
            loss.backward()
            optimiser.step()
    model.eval()
    out_dir.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(out_dir)
    tokenizer.save_pretrained(out_dir)
    return out_dir


def rerank_eval(reranker, cases: Sequence[EvalCase]) -> tuple[dict[str, float], list[dict]]:
    """Document-level recall@5, MRR and nDCG@10 after re-ranking each case's candidates."""
    rows = []
    for case in cases:
        scores = reranker.score(case.query, [text for _, _, text in case.candidates])
        order = sorted(range(len(case.candidates)), key=lambda i: -scores[i])
        docs = dedupe_docs(case.candidates[i][1] for i in order)
        rows.append({"id": case.id, "recall@5": recall_at_k(docs, case.gold_doc_ids, 5),
                     "mrr": reciprocal_rank(docs, case.gold_doc_ids),
                     "ndcg@10": ndcg_at_k(docs, case.gold_doc_ids, 10)})
    return {m: round(mean([r[m] for r in rows]) or 0.0, 4) for m in METRICS}, rows


def beats(current: dict[str, float], candidate: dict[str, float]) -> tuple[bool, str]:
    """The gate: higher nDCG@10 and no metric lower."""
    lower = [m for m in METRICS if candidate[m] < current[m]]
    if lower:
        return False, f"lower {', '.join(lower)} than the current model"
    if candidate["ndcg@10"] <= current["ndcg@10"]:
        return False, "nDCG@10 is not higher than the current model's"
    return True, "higher nDCG@10 and no metric lower"


def write_promotion(model_dir: Path, *, base_model: str, dataset: str, pairs: int, cases: int,
                    current: dict, candidate: dict) -> dict:
    """Record the gate's decision next to the model (read by app/retrieval/rerank.py)."""
    ok, reason = beats(current, candidate)
    record = {"base_model": base_model, "dataset": dataset, "training_pairs": pairs,
              "eval_cases": cases, "current": current, "candidate": candidate,
              "beats_current": ok, "reason": reason,
              "decided_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    (model_dir / PROMOTION_FILE).write_text(json.dumps(record, indent=2), encoding="utf-8")
    return record


def build_eval_cases(searcher, records: Sequence[dict], limit: int = 20) -> list[EvalCase]:
    """The evaluation questions' search candidates, as their users (no model calls).
    `records` are eval/replay/routing_replay.jsonl rows (questions with gold documents)."""
    from app.retrieval.acl import load_user

    cases = []
    with searcher.sessions() as s:
        users = {r["user_id"]: load_user(s, r["user_id"]) for r in records}
    for r in records:
        found = searcher.search(r["question"], users[r["user_id"]], limit=limit)
        cases.append(EvalCase(r["id"], r["question"], frozenset(r["gold_doc_ids"]),
                              tuple((c.chunk_id, c.doc_id, c.text) for c in found)))
    return cases
