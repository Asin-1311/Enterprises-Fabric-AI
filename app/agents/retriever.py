"""Agent 3: retrieval.

- Calls the ACL-filtered hybrid searcher (role + engagement filter inside the search).
- Recency boost when the intent is time_sensitive; superseded documents down-weighted.
- Scans every retrieved chunk for prompt-injection patterns (hits are logged to traces).
- `reformulate()` produces the ONE follow-up search used when too few chunks are good
  (the decision is taken after re-ranking, see graph.py).
"""

from dataclasses import dataclass, field

from app.agents.llm import LLMCaller, LLMResult
from app.retrieval.acl import UserContext
from app.retrieval.hybrid import HybridSearcher, RetrievedChunk
from app.security.injection import scan

REFORMULATE_SCHEMA = {
    "type": "object",
    "properties": {"queries": {"type": "array", "items": {"type": "string"}}},
    "required": ["queries"],
    "additionalProperties": False,
}
REFORMULATE_SYSTEM = (
    "A search over an audit firm's documents (ICAI Standards on Auditing study material, firm "
    "policies, circulars, checklists, templates, calendar, FAQ) found too little. Write 2 "
    "alternative search queries for the same question, using different wording: synonyms, "
    "the relevant SA number if obvious, or the formal term an auditing text would use. Do not "
    "answer the question."
)


@dataclass
class RetrievalOutcome:
    candidates: list[RetrievedChunk]
    injection_hits: dict[str, list[str]]  # chunk_id -> pattern names
    steps: list[dict] = field(default_factory=list)  # for the `retrievals` table


def retrieve(searcher: HybridSearcher, queries: list[str], user: UserContext, *,
             source_types: list[str] | None, time_sensitive: bool, limit: int) -> RetrievalOutcome:
    """Run hybrid search and scan the results for injection attempts."""
    steps: list[dict] = []
    hits = searcher.search(queries, user, source_types=source_types or None,
                           time_sensitive=time_sensitive, limit=limit, record=steps)
    flagged = {h.chunk_id: found for h in hits if (found := scan(h.text))}
    return RetrievalOutcome(hits, flagged, steps)


def reformulate(question: str, llm: LLMCaller) -> tuple[list[str], LLMResult]:
    """Two alternative wordings of the question for the follow-up search."""
    result = llm.call(purpose="reformulate", role="light", system=REFORMULATE_SYSTEM,
                      user=f"Question: {question}", max_tokens=300, schema=REFORMULATE_SCHEMA)
    queries = [q.strip() for q in (result.data or {}).get("queries", []) if q.strip()][:2]
    return queries or [question], result


def merge(old: list[RetrievedChunk], new: list[RetrievedChunk]) -> list[RetrievedChunk]:
    """Union of two candidate lists by chunk_id (keeps the first occurrence)."""
    seen: dict[str, RetrievedChunk] = {}
    for c in [*old, *new]:
        seen.setdefault(c.chunk_id, c)
    return list(seen.values())


@dataclass
class SubQueryResult:
    """One sub-query's search in one retrieval round (M14a fan-out)."""

    round: int
    query: str
    candidates: list[RetrievedChunk]
    injection_hits: dict[str, list[str]]
    steps: list[dict]
    started: float  # time.monotonic(), so tests and traces can see the searches overlap
    ended: float


def retrieve_one(searcher: HybridSearcher, query: str, user: UserContext, *, round: int,
                 source_types: list[str] | None, time_sensitive: bool, limit: int,
                 top_k: int | None = None, plan: dict | None = None) -> SubQueryResult:
    """Search ONE sub-query (the graph runs one of these per sub-query, in parallel)."""
    import time

    started = time.monotonic()
    steps: list[dict] = []
    hits = searcher.search([query], user, source_types=source_types or None,
                           time_sensitive=time_sensitive, limit=limit, record=steps,
                           top_k=top_k, plan=plan)
    flagged = {h.chunk_id: found for h in hits if (found := scan(h.text))}
    return SubQueryResult(round, query, hits, flagged, steps, started, time.monotonic())
