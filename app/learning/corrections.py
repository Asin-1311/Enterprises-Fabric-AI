"""User corrections become CANDIDATE evaluation questions (spec 7.9; CLAUDE.md sections 11
and 13; M15c).

Each "not helpful" or "helpful" rating that came with a correction gives one candidate:
the question as stored (masked), the correction as a draft gold answer, and
"reviewed": false. Candidates are written outside the evaluation set
(data/learning/eval_candidates/); only the author reviews them and moves the good ones
into eval/questions.jsonl, so no user's words become a gold answer unchecked.
"""

import json

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Answer, Feedback, Query


def correction_candidates(sessions: sessionmaker[Session], known_questions: set[str] = frozenset()
                          ) -> list[dict]:
    """One candidate per feedback row with a correction, skipping questions already in the
    evaluation set (compared case- and space-insensitively)."""
    def norm(text: str) -> str:
        return " ".join(text.lower().split())

    known = {norm(q) for q in known_questions}
    out, seen = [], set()
    with sessions() as s:
        rows = s.execute(select(Feedback, Answer, Query)
                         .join(Answer, Answer.id == Feedback.answer_id)
                         .join(Query, Query.id == Answer.query_id)
                         .where(Feedback.correction.is_not(None)).order_by(Feedback.id)).all()
        for fb, answer, query in rows:
            if not (fb.correction or "").strip() or norm(query.text) in known:
                continue
            key = (norm(query.text), norm(fb.correction))
            if key in seen:
                continue
            seen.add(key)
            out.append({"id": f"fb{fb.id}", "question": query.text, "user_id": query.user_id,
                        "intent": query.intent, "gold_doc_ids": [], "gold_answer": fb.correction,
                        "source": "feedback_correction", "rating": fb.rating,
                        "answer_id": answer.id, "reviewed": False})
    return out


def to_jsonl(rows: list[dict]) -> str:
    return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
