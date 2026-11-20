"""How much retrieval a question gets, and how it is ranked (spec sections 3, 7.3 and 8;
M14a, M15a).

Decided once per question from its analysis, routing and the current load, and recorded
in `traces` and on every fused `retrievals` row, so its effect can be measured:

- retriever weights and candidates per retriever, by question type (M15a; values in
  config, starting points to be tuned on the evaluation set):
      factual, procedural                  keyword 0.6 / dense 0.4, 20 candidates (the
                                           spec's 10 lost recall on the evaluation set)
      comparison, analytical, open_ended   dense 0.6 / keyword 0.4, 30 candidates
      time_sensitive (and anything else)   equal weights, RETRIEVAL_TOP_K candidates
- light path (spec 16.1): a factual question with one sub-query, no client named and a
  routing decision made by the rules (not the LLM tie-breaker) is simple. It searches
  LIGHT_TOP_K candidates and skips compression.
- chunk level: exploratory questions are expanded from the matching chunk to its section
  ("parent": small-to-big) before compression; the others use the matching chunks.
- under load (M15a, app/adaptive/degradation.py): fewer candidates and no compression.
"""

from dataclasses import asdict, dataclass, field

from app.adaptive.degradation import degraded_candidates
from app.agents.query import EXPLORATORY, QueryAnalysis
from app.agents.router import RoutingDecision
from app.config import Settings

PRECISE = ("factual", "procedural")


@dataclass(frozen=True)
class RetrievalPlan:
    intent: str
    light_path: bool
    chunk_level: str              # "child" | "parent"
    candidates_per_retriever: int
    compress: bool
    retriever_weights: dict[str, float] = field(default_factory=lambda: {"dense": 1.0, "keyword": 1.0})
    under_load: bool = False

    def record(self) -> dict:
        return asdict(self)


def intent_profile(intent: str, settings: Settings) -> tuple[dict[str, float], int]:
    """(retriever weights, candidates per retriever) for a question type."""
    if intent in PRECISE:
        return ({"dense": settings.precise_dense_weight, "keyword": settings.precise_keyword_weight},
                settings.precise_top_k)
    if intent in EXPLORATORY:
        return ({"dense": settings.exploratory_dense_weight,
                 "keyword": settings.exploratory_keyword_weight}, settings.exploratory_top_k)
    return {"dense": 1.0, "keyword": 1.0}, settings.retrieval_top_k


def plan_for(analysis: QueryAnalysis, routing: RoutingDecision, settings: Settings,
             under_load: bool = False) -> RetrievalPlan:
    light = (analysis.intent == "factual" and len(analysis.sub_queries) == 1
             and not analysis.client_engagements and not routing.used_tiebreak)
    weights, candidates = intent_profile(analysis.intent, settings)
    if light:
        candidates = min(candidates, settings.light_top_k)
    if under_load:
        candidates = degraded_candidates(candidates, settings.degraded_candidate_factor)
    return RetrievalPlan(
        intent=analysis.intent, light_path=light,
        chunk_level="parent" if analysis.intent in EXPLORATORY else "child",
        candidates_per_retriever=candidates, compress=not light and not under_load,
        retriever_weights=weights, under_load=under_load)
