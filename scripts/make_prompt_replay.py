"""Record the prompt replay set (M15c): eval/replay/prompt_replay.jsonl.

    .venv\\Scripts\\python.exe -m scripts.make_prompt_replay

No LLM calls. From the evaluation questions (eval/questions.jsonl):
- every "no retrieval" question, and every question whose gold documents are all
  SYNTHETIC and not restricted, gives one record with its labelled intent;
- the retrieval questions also get the agents' fixed inputs, found once with the hybrid
  search (no model): the best-ranked gold chunk and up to two synthetic distractor chunks
  (chunk ids only: the text is read from the index at replay time);
- and two claims for the verifier, both citing the gold chunk: one sentence copied from
  the gold chunk (expected "supported") and one copied from a distractor chunk and not
  found in the gold chunk (expected "unsupported").

Only synthetic documents are used, so the set runs from a clean clone and the committed
file quotes no licensed text. The intents come from the evaluation questions and the
claim labels are drafted automatically (a heuristic: an "unsupported" sentence shares at
most 30% of its longer words with the gold chunk), so every label starts "reviewed":
false until the author checks it; the replay report states how many are reviewed.
"""

import csv
import json
import re
import sys

from app.config import PROJECT_ROOT, get_settings
from app.retrieval.acl import load_user
from app.retrieval.compress import split_sentences

QUESTIONS = PROJECT_ROOT / "eval" / "questions.jsonl"
SYNTHETIC_MANIFEST = PROJECT_ROOT / "data" / "manifest.synthetic.csv"
OUT = PROJECT_ROOT / "eval" / "replay" / "prompt_replay.jsonl"
DISTRACTORS = 2


MAX_SHARED_WORDS = 0.3  # an "unsupported" claim shares at most this share of its words
BOILERPLATE = ("SYNTHETIC", "Example & Associates", "Not real firm or client data")
LIST_MARKER = re.compile(r"^(- |\(\w\) |\d+\.\s)")


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) >= 4}


def claim_sentence(text: str, avoid: str | None = None) -> str | None:
    """A whole sentence of the chunk body that reads as one claim: 40-300 characters, no
    brackets, list marker or banner text. With `avoid` (the gold chunk), it must also share
    at most MAX_SHARED_WORDS of its longer words with it, so the gold chunk cannot support
    it by accident."""
    _, _, body = text.partition("\n")
    for sentence in split_sentences(body):
        s = " ".join(sentence.split())
        if not (40 <= len(s) <= 300 and s.endswith(".")) or any(c in s for c in "[]|#") \
                or LIST_MARKER.match(s) or any(b in s for b in BOILERPLATE):
            continue
        if avoid is not None:
            words = _words(s)
            if not words or len(words & _words(avoid)) / len(words) > MAX_SHARED_WORDS:
                continue
        return s
    return None


def build_records(questions: list[dict], synthetic: dict[str, dict], searcher) -> list[dict]:
    """The replay records (see the module docstring)."""
    records = []
    with searcher.sessions() as s:
        users = {q["user_id"]: load_user(s, q["user_id"]) for q in questions}
    for q in questions:
        base = {"id": q["id"], "question": q["question"], "user_id": q["user_id"],
                "intent": q["intent"], "reviewed": bool(q.get("reviewed"))}
        if q["intent"] == "no_retrieval":
            records.append(base)
            continue
        gold = set(q.get("gold_doc_ids") or [])
        if not gold or not gold <= set(synthetic) or any(
                synthetic[d].get("classification") == "restricted" for d in gold):
            continue
        found = [c for c in searcher.search(q["question"], users[q["user_id"]], limit=20)
                 if c.doc_id in synthetic and synthetic[c.doc_id].get("classification") != "restricted"]
        gold_chunk = next((c for c in found if c.doc_id in gold), None)
        if gold_chunk is None:
            continue
        distractors = [c for c in found if c.doc_id not in gold][:DISTRACTORS]
        # Unsupported claims come from other documents only (another chunk of the gold
        # document is too likely to say something the gold chunk also says).
        supported = claim_sentence(gold_chunk.text)
        unsupported = next((s for c in distractors
                            if (s := claim_sentence(c.text, avoid=gold_chunk.text))), None)
        claims = []
        if supported:
            claims.append({"text": supported, "expected": "supported", "reviewed": False})
        if unsupported:
            claims.append({"text": unsupported, "expected": "unsupported", "reviewed": False})
        ranked = sorted([gold_chunk, *distractors], key=found.index)
        records.append(base | {
            "gold_doc_ids": sorted(gold),
            "gold_source_types": sorted({synthetic[d]["source_type"] for d in gold}),
            "chunk_ids": [c.chunk_id for c in ranked], "gold_chunk_id": gold_chunk.chunk_id,
            "claims": claims})
    return records


def main() -> int:
    from app.retrieval.dense import QdrantInUseError
    from app.retrieval.hybrid import build_searcher

    try:
        searcher = build_searcher(get_settings())
    except QdrantInUseError as exc:
        print(exc)
        return 2
    with SYNTHETIC_MANIFEST.open(encoding="utf-8", newline="") as f:
        synthetic = {r["doc_id"]: r for r in csv.DictReader(f)}
    questions = [json.loads(line) for line in QUESTIONS.read_text(encoding="utf-8").splitlines()
                 if line.strip()]
    records = build_records(questions, synthetic, searcher)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    retrieval = sum("chunk_ids" in r for r in records)
    print(f"Wrote {OUT.relative_to(PROJECT_ROOT)}: {len(records)} records ({retrieval} with "
          f"agent inputs) from {len(questions)} questions.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
