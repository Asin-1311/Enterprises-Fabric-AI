"""Agent 1: query understanding (MODEL_LIGHT).

Returns the intent, entities and 1-3 search sub-queries. Two checks do not depend on
the model:
- SA numbers are also extracted with a regex (and normalised to "SA 505"),
- client names are matched in code against the engagements table (`ClientMatcher`), so a
  missed or misspelt client from the model cannot bypass the engagement check in routing.
"""

import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.llm import LLMCaller, LLMResult
from app.db.models import Engagement
from app.ingest.chunker import extract_sa_refs

INTENTS = ("no_retrieval", "factual", "procedural", "comparison", "analytical", "open_ended",
           "time_sensitive")
# Intents that need wider context: their chunks are expanded to the whole section
# (small-to-big, app/retrieval/parent_expand.py) before compression (M14a).
EXPLORATORY = ("comparison", "analytical", "open_ended")
MAX_SUB_QUERIES = 3

SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": list(INTENTS)},
        "sa_numbers": {"type": "array", "items": {"type": "string"}},
        "clients": {"type": "array", "items": {"type": "string"}},
        "fs_items": {"type": "array", "items": {"type": "string"}},
        "dates_or_years": {"type": "array", "items": {"type": "string"}},
        "sub_queries": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["intent", "sa_numbers", "clients", "fs_items", "dates_or_years", "sub_queries"],
    "additionalProperties": False,
}

SYSTEM = """You analyse questions asked by audit staff of an Indian chartered accountancy firm.
Return JSON with:
- intent: one of
  no_retrieval   (greetings, thanks, questions about what the assistant can do),
  factual        (what a standard, policy or file says),
  procedural     (how to perform a task, steps, checklists),
  comparison     (differences between two or more things),
  analytical     (why something matters, implications, evaluating a situation against
                  the standards or policy, e.g. "is this a significant risk and why?"),
  open_ended     (broad or exploratory: "tell me about", "what should I know about",
                  "explain the approach to"),
  time_sensitive (due dates, deadlines, "latest", "this year", current period).
- sa_numbers: Standards on Auditing mentioned, written like "SA 505".
- clients: client or entity names mentioned.
- fs_items: financial statement items mentioned (e.g. inventory, trade receivables, advances/NPA).
- dates_or_years: dates or financial years mentioned (e.g. "FY 2025-26").
- sub_queries: 1 to 3 short search queries that together cover the question. Keep
  standard numbers, client names and key terms. For a comparison, one query per side.
Do not answer the question."""


@dataclass
class QueryAnalysis:
    intent: str
    sa_numbers: list[str]
    client_engagements: list[str]  # engagement_ids whose client is named in the question
    fs_items: list[str] = field(default_factory=list)
    dates_or_years: list[str] = field(default_factory=list)
    sub_queries: list[str] = field(default_factory=list)


class ClientMatcher:
    """Finds engagement clients named in a question (case-insensitive, whole words)."""

    def __init__(self, aliases: dict[str, list[str]]) -> None:
        self.patterns = {eng: [re.compile(rf"\b{re.escape(a)}\b", re.I) for a in names]
                         for eng, names in aliases.items()}

    @classmethod
    def from_db(cls, session: Session) -> "ClientMatcher":
        """Aliases: the full client name, its distinctive first word, and a branch name."""
        aliases: dict[str, list[str]] = {}
        for eng in session.scalars(select(Engagement)):
            name = eng.client_name
            names = {name}
            first = name.split()[0]
            if len(first) >= 5:
                names.add(first)  # "Northwind", "Meridian", "Brightpath"
            if " - " in name:
                branch = name.split(" - ", 1)[1]
                names.add(branch)  # "Lakeview Branch"
                names.add(branch.split()[0])  # "Lakeview"
            aliases[eng.engagement_id] = sorted(names)
        return cls(aliases)

    def find(self, *texts: str) -> list[str]:
        return sorted(eng for eng, pats in self.patterns.items()
                      if any(p.search(t) for p in pats for t in texts))


def normalise_sa(values: list[str]) -> list[str]:
    """'505', 'SA505', 'sa 505' -> 'SA 505'."""
    out = set()
    for v in values:
        m = re.search(r"(\d{3})", v)
        if m:
            out.add(f"SA {m.group(1)}")
    return sorted(out)


def understand(question: str, llm: LLMCaller, clients: ClientMatcher) -> tuple[QueryAnalysis, LLMResult]:
    """Run the query-understanding call and post-process it."""
    result = llm.call(purpose="query_understanding", role="light", system=SYSTEM,
                      user=f"Question: {question}", max_tokens=600, schema=SCHEMA)
    d = result.data or {}
    intent = d.get("intent") if d.get("intent") in INTENTS else "factual"
    subs = [q.strip() for q in d.get("sub_queries", []) if q and q.strip()][:MAX_SUB_QUERIES]
    analysis = QueryAnalysis(
        intent=intent,
        sa_numbers=sorted(set(normalise_sa(d.get("sa_numbers", []))) | set(extract_sa_refs(question))),
        client_engagements=clients.find(question, *d.get("clients", [])),
        fs_items=list(d.get("fs_items", [])),
        dates_or_years=list(d.get("dates_or_years", [])),
        sub_queries=subs or [question],
    )
    return analysis, result
