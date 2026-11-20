"""The response cache (spec 5 serving plane; CLAUDE.md section 10; Gap Register R-5-serve;
M16a).

A verified answer is reused only when ALL of these match:
- the normalised question (case, spacing and closing punctuation ignored);
- the asker's ACCESS FINGERPRINT: role plus sorted engagement ids. These are exactly the
  facts the access policy decides on (policy/document_access.rego), so two users with the
  same fingerprint may see exactly the same documents;
- the index version (app/ingest/index_version.py): any index write makes older entries
  unreachable;
- the active routing-policy and prompt versions: activating or rolling back one does too;
- the day (UTC): time-sensitive answers ("this year", "due next") never outlive their day.
The key is a SHA-256 of those parts, so neither the question nor the user is readable
from the cache store.

What is cached: only "answered", fully verified (not degraded) answers that cite no
restricted text. Abstentions, denials, "not found" and reduced-checking answers always
run the full pipeline again.

A hit is served as the asker's OWN answer: it writes the asker's query, answer and
citation rows and one `response_cache` trace naming the answer it came from, so history,
feedback and the audit trail work exactly as for a fresh answer. Before serving, every
cited document is checked again against the asker's access; a mismatch is logged as a
security error and the cache is bypassed.
"""

import hashlib
import json
import logging
import re
from dataclasses import asdict
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.agents.graph import AnswerResult, CitationInfo
from app.agents.versions import ensure_active_versions
from app.db.models import Answer, Citation, Document, Query, Trace
from app.ingest import index_version
from app.ports import Cache
from app.retrieval.acl import UserContext, can_see
from app.security.injection import scan as scan_for_injection
from app.security.masking import mask_text

log = logging.getLogger(__name__)


def normalise_question(text: str) -> str:
    """Lower case, single spaces, without closing punctuation."""
    return re.sub(r"[\s?.!]+$", "", " ".join(text.casefold().split()))


def access_fingerprint(user: UserContext) -> str:
    """Role plus sorted engagement ids: everything the access policy reads about a user."""
    return f"{user.role}|{','.join(sorted(user.engagement_ids))}"


class ResponseCache:
    """Verified answers, keyed so that they never cross an access boundary or a change."""

    def __init__(self, cache: Cache, sessions: sessionmaker[Session], ttl_seconds: float) -> None:
        self.cache, self.sessions, self.ttl = cache, sessions, ttl_seconds

    def key(self, question: str, user: UserContext) -> str:
        versions = ensure_active_versions(self.sessions)
        parts = {
            "q": normalise_question(question),
            "access": access_fingerprint(user),
            "index": index_version.current(self.sessions),
            "policy": versions.policy_id,
            "prompts": sorted(versions.prompt_ids.items()),
            "day": datetime.now(timezone.utc).date().isoformat(),
        }
        return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()

    # ------------------------------------------------------------------ storing

    def _cites_restricted(self, result: AnswerResult) -> bool:
        doc_ids = {c.doc_id for c in result.citations}
        if not doc_ids:
            return False
        with self.sessions() as s:
            return any(c == "restricted" for c in s.scalars(
                select(Document.classification).where(Document.doc_id.in_(doc_ids))))

    def store(self, key: str, result: AnswerResult, intent: str | None = None) -> bool:
        """Cache `result` under `key` if it may be reused. Returns True if stored."""
        if result.status != "answered" or result.degraded or not result.citations \
                or self._cites_restricted(result):
            return False
        entry = {"source_answer_id": result.answer_id, "intent": intent,
                 "result": {k: v for k, v in asdict(result).items()
                            if k not in ("query_id", "answer_id")}}
        self.cache.set(key, json.dumps(entry).encode(), self.ttl)
        return True

    # ------------------------------------------------------------------ serving

    def serve(self, key: str, question: str, user: UserContext) -> AnswerResult | None:
        """The cached answer as the asker's own (new rows), or None on a miss."""
        raw = self.cache.get(key)
        if raw is None:
            return None
        entry = json.loads(raw)
        data = dict(entry["result"])
        citations = [CitationInfo(**c) for c in data.pop("citations")]
        with self.sessions() as s:
            docs = {d.doc_id: d for d in s.scalars(select(Document).where(
                Document.doc_id.in_({c.doc_id for c in citations})))}
            if any(c.doc_id not in docs or not can_see(user, docs[c.doc_id].access_level,
                                                       docs[c.doc_id].engagement_id)
                   for c in citations):
                log.error("SECURITY: a cached answer cites a document user %s may not see; "
                          "the cache entry is dropped and the question answered afresh.", user.user_id)
                self.cache.delete(key)
                return None
            versions = ensure_active_versions(self.sessions)
            q = Query(user_id=user.user_id, text=mask_text(question), intent=entry.get("intent"),
                      injection_hits=scan_for_injection(question))
            s.add(q)
            s.flush()
            answer = Answer(query_id=q.id, text=data["text"], confidence=data["confidence"],
                            status=data["status"], flagged_claims=data["flagged_claims"] or None,
                            degraded=False)
            s.add(answer)
            s.flush()
            s.add_all(Citation(answer_id=answer.id, chunk_id=c.chunk_id, span_start=c.span_start,
                               span_end=c.span_end) for c in citations)
            s.add(Trace(query_id=q.id, agent="response_cache", input=json.dumps({"key": key[:16]}),
                        output=json.dumps({"source_answer_id": entry["source_answer_id"]}),
                        policy_version_id=versions.policy_id, latency_ms=0.0))
            s.commit()
            return AnswerResult(query_id=q.id, answer_id=answer.id, citations=citations, **data)
