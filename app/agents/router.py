"""Agent 2: source routing (rule-based; MODEL_LIGHT only as a tie-breaker).

Rules (CLAUDE.md section 7.2), applied to the question text; all matching rules are
combined:
- SA number mentioned                          -> standards + firm_policy
- "how do we / steps / checklist / procedure"  -> checklists + standards (+ faq)
- letter, report format, wording, template     -> templates + standards
- due date, deadline, "latest", "this year"    -> calendar + circulars
- client name (user on that engagement)        -> engagement + firm_policy
- firm practice ("our", "firm", "policy")      -> firm_policy + circulars (+ faq)
(faq is added to the two practical rules because the helpdesk FAQ answers exactly those
questions; a small, documented extension.)

If NO rule fires, MODEL_LIGHT picks the source types (the tie-breaker). If that fails,
standards + firm_policy is used.

Engagement check: if the question names a client whose engagement the user is not on
(and the user is not a partner), routing returns `denied=True`. The pipeline then
answers with the generic "not available" message without retrieving anything.
"""

import re
from dataclasses import dataclass

from app.agents.llm import LLMCaller, LLMResult
from app.agents.query import QueryAnalysis
from app.ingest.manifest import SOURCE_TYPES
from app.retrieval.acl import UserContext

RULES: list[tuple[str, re.Pattern[str], tuple[str, ...]]] = [
    ("procedure", re.compile(r"\bhow (do|should|can|to)\b|\bsteps?\b|\bchecklists?\b|\bprocedures?\b"
                             r"|\bprogrammes?\b|\bwhat should (i|we) do\b", re.I),
     ("checklists", "standards", "faq")),
    ("template", re.compile(r"\bletters?\b|\breport formats?\b|\bformat of\b|\bwording\b"
                            r"|\btemplates?\b|\bdraft(ing)?\b", re.I),
     ("templates", "standards")),
    ("deadline", re.compile(r"\bdue\b|\bdeadlines?\b|\blatest\b|\bthis year\b|\bcalendar\b"
                            r"|\blast date\b", re.I),
     ("calendar", "circulars")),
    ("firm_practice", re.compile(r"\bour\b|\bfirm'?s?\b|\bwe\b|\bpolic(y|ies)\b|\bcirculars?\b", re.I),
     ("firm_policy", "circulars", "faq")),
]
DEFAULT = ("standards", "firm_policy")

TIEBREAK_SCHEMA = {
    "type": "object",
    "properties": {"source_types": {"type": "array", "items": {"type": "string",
                                                                "enum": [s for s in SOURCE_TYPES
                                                                         if s != "engagement"]}}},
    "required": ["source_types"],
    "additionalProperties": False,
}
TIEBREAK_SYSTEM = """Choose which document collections of an audit firm best answer the question
(pick 1 to 3): standards (ICAI study material on the Standards on Auditing, ethics, bank and
entity audits), firm_policy (firm methodology and policies), circulars (firm technical
circulars and regulatory updates), checklists (audit programmes), templates (letters and
report formats), calendar (compliance due dates), faq (helpdesk answers for article assistants)."""


@dataclass
class RoutingDecision:
    source_types: list[str]
    rules_fired: list[str]
    used_tiebreak: bool
    denied: bool = False
    denied_engagements: tuple[str, ...] = ()


def route(question: str, analysis: QueryAnalysis, user: UserContext,
          llm: LLMCaller) -> tuple[RoutingDecision, LLMResult | None]:
    """Decide which source types to search (and whether to deny outright)."""
    named = analysis.client_engagements
    forbidden = tuple(e for e in named if not user.sees_all_engagements and e not in user.engagement_ids)
    if forbidden:
        return RoutingDecision([], ["client_not_assigned"], False, True, forbidden), None

    sources: list[str] = []
    fired: list[str] = []

    def add(name: str, types: tuple[str, ...]) -> None:
        fired.append(name)
        sources.extend(t for t in types if t not in sources)

    if analysis.sa_numbers:
        add("sa_number", DEFAULT)
    for name, pattern, types in RULES:
        if pattern.search(question):
            add(name, types)
    if analysis.intent == "time_sensitive" and "deadline" not in fired:
        add("deadline", ("calendar", "circulars"))
    if named:
        add("client", ("engagement", "firm_policy"))

    if sources:
        return RoutingDecision(sources, fired, False), None

    # Tie-breaker: no rule fired.
    try:
        picked, result = tiebreak(question, llm)
    except Exception:  # noqa: BLE001 - routing must not fail the request
        result, picked = None, []
    chosen = picked or list(DEFAULT)
    return RoutingDecision(chosen, ["tiebreak" if picked else "default"], True), result


def tiebreak(question: str, llm: LLMCaller) -> tuple[list[str], LLMResult]:
    """The light model's choice of up to three source types (known types only)."""
    result = llm.call(purpose="routing_tiebreak", role="light", system=TIEBREAK_SYSTEM,
                      user=f"Question: {question}", max_tokens=200, schema=TIEBREAK_SCHEMA)
    picked = [s for s in (result.data or {}).get("source_types", []) if s in SOURCE_TYPES]
    return list(dict.fromkeys(picked))[:3], result
