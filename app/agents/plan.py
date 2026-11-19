"""How much retrieval a question gets (spec sections 3, 7.3 and 8; M14a).

Decided once per question from its analysis and routing, recorded in `traces` and in
every fused `retrievals` row, so its effect can be measured:

- light path (spec 16.1): a factual question with one sub-query, no client named and a
  routing decision made by the rules (not the LLM tie-breaker) is simple. It searches
  fewer candidates (LIGHT_TOP_K) and skips compression: the re-ranked chunks go to
  synthesis whole.
- chunk level: exploratory questions (comparison, analytical, open-ended) are expanded
  from the matching chunk to its section ("parent": small-to-big) before compression;
  the others use the matching chunks ("child").
- candidates per retriever: LIGHT_TOP_K on the light path, RETRIEVAL_TOP_K otherwise.
  Per-intent retriever weights and candidate counts are tuned in M15.
"""

from dataclasses import asdict, dataclass

from app.agents.query import EXPLORATORY, QueryAnalysis
from app.agents.router import RoutingDecision
from app.config import Settings


@dataclass(frozen=True)
class RetrievalPlan:
    intent: str
    light_path: bool
    chunk_level: str              # "child" | "parent"
    candidates_per_retriever: int
    compress: bool

    def record(self) -> dict:
        return asdict(self)


def plan_for(analysis: QueryAnalysis, routing: RoutingDecision, settings: Settings) -> RetrievalPlan:
    light = (analysis.intent == "factual" and len(analysis.sub_queries) == 1
             and not analysis.client_engagements and not routing.used_tiebreak)
    return RetrievalPlan(
        intent=analysis.intent, light_path=light,
        chunk_level="parent" if analysis.intent in EXPLORATORY else "child",
        candidates_per_retriever=settings.light_top_k if light else settings.retrieval_top_k,
        compress=not light)
