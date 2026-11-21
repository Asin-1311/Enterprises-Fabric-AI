"""The evaluation questions (CLAUDE.md section 13.1; M18a).

- The general set: eval/questions.jsonl (Phase 1, at least 60 questions).
- One file per source type: eval/questions/<source_type>.jsonl, at least 10 questions each
  whose gold documents include a document of that type.

Every question starts with "reviewed": false. Claude may draft questions and gold answers;
only the author sets "reviewed" to true, and every report states how many are reviewed.

`problems` checks the sets against the manifest, the users and the engagements, using the
evaluation's own access rule (eval/run_retrieval_eval.py), so a question can never expect
a document its user is not allowed to see.
"""

import json
from collections import Counter
from pathlib import Path

from app.config import PROJECT_ROOT

EVAL = PROJECT_ROOT / "eval"
GENERAL = EVAL / "questions.jsonl"
PER_SOURCE_DIR = EVAL / "questions"
SOURCE_TYPES = ("standards", "firm_policy", "circulars", "checklists", "templates", "engagement",
                "calendar", "faq")
INTENTS = ("no_retrieval", "factual", "procedural", "comparison", "analytical", "open_ended",
           "time_sensitive")
MIN_GENERAL, MIN_PER_SOURCE, MIN_TIME_SENSITIVE = 60, 10, 10
REQUIRED = ("id", "question", "user_id", "intent", "category", "gold_doc_ids", "gold_answer", "reviewed")


def _read(path: Path, set_name: str) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [{**row, "set": set_name} for row in rows]


def load_questions(per_source: bool = True) -> list[dict]:
    """The general set, then each per-source file; every question carries its `set`."""
    questions = _read(GENERAL, "general")
    if per_source:
        for source_type in SOURCE_TYPES:
            path = PER_SOURCE_DIR / f"{source_type}.jsonl"
            if path.is_file():
                questions += _read(path, source_type)
    return questions


def problems(questions: list[dict], docs: dict, roles: dict, teams: dict) -> list[str]:
    """Why the question sets are not valid (empty list = valid)."""
    from eval.run_retrieval_eval import leak_flags  # the evaluation's independent access rule

    found = []
    ids = Counter(q.get("id") for q in questions)
    found += [f"duplicate id {i}" for i, n in ids.items() if n > 1]
    for q in questions:
        where = f"{q.get('set')}/{q.get('id')}"
        missing = [k for k in REQUIRED if k not in q]
        if missing:
            found.append(f"{where}: missing {missing}")
            continue
        if q["intent"] not in INTENTS:
            found.append(f"{where}: unknown intent {q['intent']!r}")
        if q["user_id"] not in roles:
            found.append(f"{where}: unknown user {q['user_id']}")
            continue
        if not isinstance(q["reviewed"], bool):
            found.append(f"{where}: reviewed must be true or false")
        for doc in [*q["gold_doc_ids"], *q.get("superseded_doc_ids", [])]:
            if doc not in docs:
                found.append(f"{where}: {doc} is not in the manifest")
        for doc in q["gold_doc_ids"]:
            if doc in docs and any(leak_flags(q["user_id"], doc, docs, roles, teams)):
                found.append(f"{where}: {q['user_id']} may not see gold document {doc}")
        newer = {r["supersedes"]: d for d, r in docs.items() if r.get("supersedes")}
        for old in q.get("superseded_doc_ids", []):
            if newer.get(old) not in q["gold_doc_ids"]:
                found.append(f"{where}: {old} is not superseded by one of its gold documents")
    for source_type in SOURCE_TYPES:
        in_file = [q for q in questions if q.get("set") == source_type]
        for q in in_file:
            if q.get("gold_doc_ids") and not any(docs.get(d, {}).get("source_type") == source_type
                                                  for d in q["gold_doc_ids"]):
                found.append(f"{source_type}/{q['id']}: no gold document of type {source_type}")
        scored = sum(1 for q in in_file if q.get("gold_doc_ids"))
        if scored < MIN_PER_SOURCE:
            found.append(f"{source_type}: {scored} questions with gold documents (at least {MIN_PER_SOURCE})")
    if sum(q.get("set") == "general" for q in questions) < MIN_GENERAL:
        found.append(f"general set: fewer than {MIN_GENERAL} questions")
    if sum(q.get("intent") == "time_sensitive" for q in questions) < MIN_TIME_SENSITIVE:
        found.append(f"fewer than {MIN_TIME_SENSITIVE} time-sensitive questions")
    return found


def summary(questions: list[dict]) -> dict:
    """Counts every report states: questions, reviewed, per set and per intent."""
    return {"questions": len(questions), "reviewed": sum(q.get("reviewed") is True for q in questions),
            "by_set": dict(sorted(Counter(q["set"] for q in questions).items())),
            "by_intent": dict(sorted(Counter(q["intent"] for q in questions).items()))}
