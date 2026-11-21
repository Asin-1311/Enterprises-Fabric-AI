"""Sentence-level context compression (CLAUDE.md section 7.4).

Each kept chunk is split into numbered sentences. MODEL_LIGHT returns only the NUMBERS of
the sentences relevant to the question; the code then copies those sentences verbatim.
The model never rewrites evidence, so compression cannot introduce new wording, and the
citation still points to the original chunk.

Safety nets: the best-ranked chunk is never dropped entirely (if nothing is selected,
its first sentences are kept), and the heading line of every chunk is always kept so the
synthesiser knows which section the sentences come from.
"""

import re
from dataclasses import dataclass

from app.agents.llm import LLMCaller, LLMResult
from app.security.injection import UNTRUSTED_DATA_RULE, restricted_attributes, wrap_document

_SENTENCE_END = re.compile(r"(?<=[.;:?!])\s+(?=[A-Z(\-\d₹\"'])")
FALLBACK_SENTENCES = 3

SCHEMA = {
    "type": "object",
    "properties": {
        "chunks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"chunk_id": {"type": "string"},
                               "keep": {"type": "array", "items": {"type": "integer"}}},
                "required": ["chunk_id", "keep"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["chunks"],
    "additionalProperties": False,
}

SYSTEM = (
    "You select evidence for an audit-firm knowledge assistant. For each document, return "
    "the numbers of the sentences that help answer ANY part of the question: definitions, "
    "requirements, conditions, exceptions, procedures, figures, dates, and sentences that "
    "explain how two things relate (e.g. 'the higher the risk, the more evidence'). Keep "
    "every item of a relevant list. When in doubt, keep the sentence: a missing fact is "
    "worse than an extra one. Return an empty list only for a document with no relevant "
    "sentence at all. When a document names a sub_query (one part of the question, e.g. "
    "one side of a comparison), keep the sentences relevant to that sub-query, so every "
    "part of the question keeps its evidence. Do not answer the question. " + UNTRUSTED_DATA_RULE
)
# Chunks this short (checklist groups, FAQ answers, short policy sections) are kept whole:
# dropping one of their few sentences risks losing a key condition (found in M4 and in the
# M7 pilot, where borrower-wise NPA classification was dropped from a checklist group).
KEEP_WHOLE_WORDS = 90  # roughly 120 tokens


@dataclass
class CompressedChunk:
    chunk_id: str
    heading: str
    sentences: list[str]  # verbatim from the chunk

    @property
    def text(self) -> str:
        return "\n".join([self.heading, *self.sentences]).strip()


def split_sentences(body: str) -> list[str]:
    """Split chunk body text into sentences; list items stay separate."""
    out: list[str] = []
    for block in re.split(r"\n(?=- |\(\w\)|\d+\.\s|\|)", body):
        flat = " ".join(block.split())
        out.extend(s.strip() for s in _SENTENCE_END.split(flat) if s.strip())
    return out


def _parts(text: str) -> tuple[str, list[str]]:
    heading, _, body = text.partition("\n")
    return heading, split_sentences(body)


def compress(question: str, chunks: list[tuple[str, str]], llm: LLMCaller,
             restricted: frozenset[str] = frozenset(),
             sub_queries: dict[str, str] | None = None) -> tuple[list[CompressedChunk], LLMResult]:
    """Compress (chunk_id, text) pairs, in rank order. Returns kept chunks and the LLM result.
    Chunks in `restricted` are marked, so only the private model reads them (M13a). A chunk
    with an entry in `sub_queries` names the sub-query it was found for (M14a)."""
    sub_queries = sub_queries or {}
    parsed = {cid: _parts(text) for cid, text in chunks}
    blocks = []
    for cid, (heading, sentences) in parsed.items():
        numbered = "\n".join(f"[{i}] {s}" for i, s in enumerate(sentences, 1))
        extra = {"sub_query": sub_queries[cid]} if cid in sub_queries else {}
        blocks.append(wrap_document(f"{heading}\n{numbered}", chunk_id=cid, **extra,
                                    **restricted_attributes("restricted" if cid in restricted else None)))
    user = f"Question: {question}\n\n" + "\n\n".join(blocks)
    result = llm.call(purpose="compress", role="light", system=SYSTEM, user=user,
                      max_tokens=2000, schema=SCHEMA)
    keep = {c["chunk_id"]: c["keep"] for c in (result.data or {}).get("chunks", [])}

    out: list[CompressedChunk] = []
    for rank, (cid, (heading, sentences)) in enumerate(parsed.items()):
        idx = sorted({i for i in keep.get(cid, []) if 1 <= i <= len(sentences)})
        chosen = [sentences[i - 1] for i in idx]
        if chosen and sum(len(s.split()) for s in sentences) <= KEEP_WHOLE_WORDS:
            chosen = sentences  # short and relevant: keep the whole chunk
        if not chosen and rank == 0:  # never lose the best-ranked chunk entirely
            chosen = sentences[:FALLBACK_SENTENCES]
        if chosen:
            out.append(CompressedChunk(cid, heading, chosen))
    return out, result
