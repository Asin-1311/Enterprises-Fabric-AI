"""Agent 5: synthesis (MODEL_SYNTH).

Writes the answer from compressed evidence only. Rules are in SYSTEM; the code then
enforces what it can check:
- citations must be `[chunk_id]` of a chunk that was actually given as evidence; any
  other citation is removed and reported;
- a reply of exactly NOT_FOUND becomes the generic not-available message (the same text
  as an access denial, so the system never confirms that a restricted document exists);
- the professional-judgement line is appended by code, so it is always present.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from app.agents.llm import LLMCaller, LLMResult
from app.retrieval.compress import CompressedChunk
from app.retrieval.hybrid import RetrievedChunk
from app.security.injection import UNTRUSTED_DATA_RULE, restricted_attributes, wrap_document

NOT_FOUND_TOKEN = "NOT_FOUND"
NOT_AVAILABLE_MESSAGE = (
    "I could not find this in the material available to your role and engagement "
    "assignments. If you believe you need it, please ask your engagement manager."
)
JUDGEMENT_NOTE = (
    "_This answer supports, and does not replace, the professional judgement of the "
    "engagement team; sign-off remains with the engagement partner._"
)
# A cited chunk id in square brackets. Two forms (M12b): the stable id
# "<doc>:<section>:<window>" ("[STD-CH06:1.9:1]", "[FAQ-01:q-3f9a2c:1]") and the Phase 1
# position id "<doc>:0012", which answers written before M12b contain. The document part
# follows the doc_id rule in app/ingest/enrich.py.
CITATION = re.compile(r"\[([A-Za-z0-9][A-Za-z0-9_.-]*:(?:\d{4}|[A-Za-z0-9][A-Za-z0-9.-]*:\d+))\]")

SYSTEM = f"""You are the knowledge assistant of Example & Associates, Chartered Accountants
(a fictional firm). You answer questions from audit staff using ONLY the evidence provided.

Rules:
1. Use only facts stated in the <document> blocks. Do not use outside knowledge.
2. End every factual sentence with its citation in square brackets, using the document's
   cite id exactly, e.g. [STD-CH06:0012]. Several sources: [A:0001][B:0002]. This includes
   summary sentences, comparisons ("the firm rule is stricter than the SA") and version
   history: dates and versions come from a document's attributes, so cite that document.
   A factual sentence without a citation will be deleted.
3. Never invent SA paragraph numbers, section numbers, percentages or due dates. If a detail
   is not in the evidence, say it was not found in the available material.
4. If a document's status is "superseded", cite the current document instead and state that
   the earlier policy was revised, giving both effective dates.
5. If ICAI guidance (source_type standards) and a firm policy differ, present both and say
   that they differ (e.g. a firm deadline stricter than the SA limit).
6. Quote only short phrases; do not reproduce whole sections.
7. You support professional judgement; never issue or draft an audit opinion as final.
8. If the evidence does not answer the question at all, reply with exactly: {NOT_FOUND_TOKEN}
9. Be concise: a direct answer first, then supporting points. Do not add a closing
   disclaimer; one is added automatically.
10. {UNTRUSTED_DATA_RULE}"""


@dataclass
class DocInfo:
    """Document-level facts the synthesiser needs about each evidence document."""

    title: str
    version: str | None
    effective_date: str | None
    source_type: str
    superseded_by: tuple[str, str | None] | None = None  # (doc_id, effective_date)
    supersedes: tuple[str, str | None] | None = None


@dataclass
class Synthesis:
    status: str  # "answered" | "not_found"
    text: str
    cited_chunk_ids: list[str] = field(default_factory=list)
    invalid_citations: list[str] = field(default_factory=list)
    superseded_involved: bool = False


def document_attributes(chunk_id: str, c: RetrievedChunk, d: DocInfo) -> dict[str, str]:
    """Attributes shown on a <document> block. Shared by the synthesiser and the verifier
    so the verifier can check exactly what the synthesiser was given (incl. dates)."""
    status = "current"
    if d.superseded_by:
        status = f"superseded (replaced by {d.superseded_by[0]}, effective {d.superseded_by[1]})"
    elif d.supersedes:
        status = f"current (replaces {d.supersedes[0]}, effective {d.supersedes[1]})"
    return dict(cite=chunk_id, title=d.title, version=d.version or "", source_type=d.source_type,
                section=f"{c.section or ''} {c.section_title or ''}".strip(),
                pages=f"{c.page_start}-{c.page_end}" if c.page_start else "",
                effective_date=d.effective_date or "", status=status,
                **restricted_attributes(c.classification))


def build_prompt(question: str, evidence: list[CompressedChunk],
                 chunks: dict[str, RetrievedChunk], docs: dict[str, DocInfo]) -> str:
    """The user message: question plus delimited, attributed evidence blocks."""
    blocks = [wrap_document(ev.text, **document_attributes(
        ev.chunk_id, chunks[ev.chunk_id], docs[chunks[ev.chunk_id].doc_id])) for ev in evidence]
    return f"Question: {question}\n\nEvidence:\n" + "\n\n".join(blocks)


def check_citations(text: str, allowed: set[str]) -> tuple[str, list[str], list[str]]:
    """Remove citations not in `allowed`. Returns (clean text, valid ids, invalid ids)."""
    valid, invalid = [], []

    def keep(m: re.Match[str]) -> str:
        cid = m.group(1)
        if cid in allowed:
            if cid not in valid:
                valid.append(cid)
            return m.group(0)
        invalid.append(cid)
        return ""

    return CITATION.sub(keep, text), valid, invalid


def synthesize(question: str, evidence: list[CompressedChunk], chunks: dict[str, RetrievedChunk],
               docs: dict[str, DocInfo], llm: LLMCaller,
               on_text: Callable[[str], None] | None = None) -> tuple[Synthesis, LLMResult]:
    """Write the cited answer."""
    result = llm.call(purpose="synthesis", role="synth", system=SYSTEM,
                      user=build_prompt(question, evidence, chunks, docs),
                      max_tokens=2000, on_text=on_text)
    raw = result.text.strip()
    involved = any(docs[chunks[e.chunk_id].doc_id].superseded_by or
                   docs[chunks[e.chunk_id].doc_id].supersedes for e in evidence)
    if raw == NOT_FOUND_TOKEN or raw.startswith(NOT_FOUND_TOKEN):
        return Synthesis("not_found", NOT_AVAILABLE_MESSAGE), result
    text, valid, invalid = check_citations(raw, {e.chunk_id for e in evidence})
    if not valid:  # an answer with no valid citation is not grounded
        return Synthesis("not_found", NOT_AVAILABLE_MESSAGE, invalid_citations=invalid), result
    return Synthesis("answered", f"{text.rstrip()}\n\n{JUDGEMENT_NOTE}", valid, invalid,
                     involved), result
