"""LLM_MODE=mock: a deterministic stand-in for Claude (M17b; CLAUDE.md section 14).

For smoke tests of a deployment (the Helm chart on kind, CI) without API keys and without
paid calls. It answers every agent with valid, schema-shaped output built from its input:

  query_understanding  intent "no_retrieval" for a greeting, "time_sensitive" for a
                       deadline word, else "factual"; the question is its one sub-query
  routing_tiebreak     standards and firm policy
  reformulate          the question itself
  compress             every sentence of every chunk kept
  synthesis            EXTRACTIVE: the first sentence of the first evidence block, cited,
                       and marked "[Mock model]" so no one mistakes it for an AI answer
  verification         every claim "supported"

It never calls a network. It is selected only by LLM_MODE=mock; the real model needs a
key (LLM_AUTH_TOKEN or ANTHROPIC_API_KEY) as before.
"""

import json
import re
import time
from collections.abc import Callable

from app.agents.llm import LLMResult

MOCK_NOTE = "[Mock model: retrieval and citations are real, the wording is not AI-generated.]"
_GREETING = re.compile(r"^\s*(hi|hello|hey|thanks|thank you|good (morning|afternoon|evening))\b", re.I)
_DEADLINE = re.compile(r"\b(due|deadline|last date|this year)\b", re.I)
_BLOCK = re.compile(r'<document [^>]*cite="([^"]+)"[^>]*>\n(.*?)\n</document>', re.S)


def _question(user: str) -> str:
    first = user.split("\n", 1)[0]
    return first.removeprefix("Question: ").strip()


def _first_sentence(text: str) -> str | None:
    body = text.split("\n", 1)[1] if "\n" in text else text
    for sentence in re.split(r"(?<=[.!?])\s+", " ".join(body.split())):
        if len(sentence) >= 20 and "[" not in sentence:
            return sentence.rstrip(".")
    return None


class MockLLM:
    """The LLMCaller used when LLM_MODE=mock."""

    def __init__(self, name: str = "mock") -> None:
        self.name = name

    def _answer(self, purpose: str, user: str) -> object:
        q = _question(user)
        if purpose == "query_understanding":
            intent = ("no_retrieval" if _GREETING.match(q) else
                      "time_sensitive" if _DEADLINE.search(q) else "factual")
            return {"intent": intent, "sa_numbers": [], "clients": [], "fs_items": [],
                    "dates_or_years": [], "sub_queries": [q]}
        if purpose == "routing_tiebreak":
            return {"source_types": ["standards", "firm_policy"]}
        if purpose == "reformulate":
            return {"queries": [q]}
        if purpose == "compress":
            return {"chunks": [{"chunk_id": cid, "keep": list(range(1, 60))}
                               for cid in re.findall(r'chunk_id="([^"]+)"', user)]}
        if purpose == "synthesis":
            for cite, text in _BLOCK.findall(user):
                sentence = _first_sentence(text)
                if sentence:
                    return f"{MOCK_NOTE}\n\n{sentence} [{cite}]."
            return "NOT_FOUND"
        if purpose == "verification":
            # A claim that cites nothing (the mock note) is not a factual claim.
            return {"claims": [{"id": int(i), "type": "non_factual" if cites == "NONE" else "factual",
                                "verdict": "supported", "supporting_cites": [], "reason": "mock"}
                               for i, cites in re.findall(r"\[claim (\d+)\] cites: ([^\n]*)", user)]}
        return ""

    def call(self, *, purpose: str, role: str, system: str, user: str, max_tokens: int,
             schema: dict | None = None, on_text: Callable[[str], None] | None = None) -> LLMResult:
        start = time.perf_counter()
        out = self._answer(purpose, user)
        text = json.dumps(out) if schema is not None else str(out)
        if on_text is not None:
            for piece in re.findall(r"\S+\s*", text):
                on_text(piece)
        return LLMResult(text=text, data=out if schema is not None else None,
                         model=f"{self.name}-{role}", effort="n/a",
                         tokens_in=len(user.split()), tokens_out=len(text.split()),
                         latency_ms=(time.perf_counter() - start) * 1000)
