"""Labelled (query, chunk) pairs for the re-ranker, from verification results and feedback
(spec 7.9; CLAUDE.md section 11; Gap Register R-7.9; M15c).

An answer is used once it is REWARD_WINDOW_HOURS old (its feedback is in), was answered
and verified (not degraded), and does not draw on restricted text (restricted text never
leaves the system's private path, so it is never written to a training file).

    positive (label 1)  a chunk the verified answer cites, when the answer reached the
                        confidence threshold and got no "not helpful" feedback
                        (label_source "verified", or "verified_helpful" with a "helpful")
    negative (label 0)  a chunk the re-ranker put in its top list for that answer but the
                        verified answer did not cite ("ranked_not_cited": a hard negative)

Answers rated "not helpful", below the confidence threshold or abstained are counted and
skipped: such an answer may have failed for reasons other than its evidence, so it says
nothing reliable about any one chunk.

Each pair is (the sub-query the chunk was ranked for, the chunk text), as the re-ranker
saw it. Chunk ids recorded before M12b are mapped to today's ids (chunks.legacy_chunk_id);
an answer whose cited chunks are no longer in the index is skipped. Questions and sub-queries were masked when stored (CLAUDE.md section 12).
"""

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db.models import Answer, Chunk, Citation, Document, Feedback, Query, Trace


@dataclass(frozen=True)
class Pair:
    query: str
    chunk_id: str
    text: str
    label: int
    label_source: str
    answer_id: int
    question: str  # the user's whole question (masked), for leaving evaluation questions out


@dataclass
class ExportSummary:
    answers_used: int = 0
    positives: int = 0
    negatives: int = 0
    skipped: dict[str, int] = field(default_factory=dict)

    def skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1


def _naive(moment: datetime) -> datetime:
    return moment.astimezone(timezone.utc).replace(tzinfo=None) if moment.tzinfo else moment


def _reranked(s: Session, query_id: int) -> list[tuple[str, str]]:
    """(chunk_id, sub-query) of the re-ranker's last top list for a question."""
    trace = s.scalars(select(Trace).where(Trace.query_id == query_id, Trace.agent == "rerank")
                      .order_by(Trace.id.desc())).first()
    if trace is None or not trace.output:
        return []
    out = json.loads(trace.output) if isinstance(trace.output, str) else trace.output
    return [(row[0], row[2] if len(row) > 2 and row[2] else "") for row in out.get("top", [])]


def _chunks(s: Session, ids: set[str]) -> dict[str, tuple[str, str, str]]:
    """id -> (current chunk id, text, classification). Answers from before M12b name chunks
    by their old ids, kept in chunks.legacy_chunk_id, so both are looked up."""
    if not ids:
        return {}
    rows = s.execute(select(Chunk, Document.classification)
                     .join(Document, Document.doc_id == Chunk.doc_id)
                     .where(or_(Chunk.chunk_id.in_(ids), Chunk.legacy_chunk_id.in_(ids)))).all()
    out = {}
    for chunk, classification in rows:
        for key in (chunk.chunk_id, chunk.legacy_chunk_id):
            if key in ids:
                out[key] = (chunk.chunk_id, chunk.text, classification or "")
    return out


def export_pairs(sessions: sessionmaker[Session], settings: Settings,
                 now: datetime | None = None) -> tuple[list[Pair], ExportSummary]:
    """Every labelled pair the logs give (see the module docstring)."""
    now = now or datetime.now(timezone.utc)
    cutoff = _naive(now) - timedelta(hours=settings.reward_window_hours)
    pairs: list[Pair] = []
    summary = ExportSummary()
    with sessions() as s:
        rows = s.execute(select(Answer, Query).join(Query, Query.id == Answer.query_id)
                         .where(Query.created_at <= cutoff, Answer.degraded.is_(False))
                         .order_by(Answer.id)).all()
        for answer, query in rows:
            if answer.status != "answered":
                summary.skip(f"status_{answer.status}")
                continue
            ratings = set(s.scalars(select(Feedback.rating).where(Feedback.answer_id == answer.id)))
            if "not_helpful" in ratings:
                summary.skip("not_helpful")
                continue
            if (answer.confidence or 0.0) < settings.confidence_threshold:
                summary.skip("low_confidence")
                continue
            cited = set(s.scalars(select(Citation.chunk_id).where(Citation.answer_id == answer.id)))
            ranked = _reranked(s, query.id)
            if not cited or not ranked:
                summary.skip("no_citations_or_ranking")
                continue
            meta = _chunks(s, {cid for cid, _ in ranked} | cited)
            if any(m[2] == "restricted" for m in meta.values()):
                summary.skip("restricted_text")
                continue
            sub_query = dict(ranked)
            positive = "verified_helpful" if "helpful" in ratings else "verified"
            found = [Pair(sub_query.get(cid) or query.text, meta[cid][0], meta[cid][1], 1, positive,
                          answer.id, query.text) for cid in sorted(cited) if cid in meta]
            found += [Pair(sq or query.text, meta[cid][0], meta[cid][1], 0, "ranked_not_cited", answer.id,
                           query.text) for cid, sq in ranked if cid not in cited and cid in meta]
            if not any(p.label for p in found):
                summary.skip("cited_chunks_not_in_index")
                continue
            pairs += found
            summary.positives += sum(p.label for p in found)
            summary.negatives += sum(1 - p.label for p in found)
            summary.answers_used += 1
    return pairs, summary


def to_jsonl(pairs: list[Pair]) -> str:
    return "".join(json.dumps(asdict(p), ensure_ascii=False) + "\n" for p in pairs)
