"""Agent 6: verification (MODEL_VERIFY, a different model from the synthesiser).

1. `extract_claims()` splits the draft answer into sentence-level claims (code, not the
   model), remembering where each sentence sits so the answer can be rebuilt.
2. ONE structured call sends every claim with the FULL text of the chunk(s) it cites. The
   verifier labels each claim:
     type    : factual (states something about a standard, policy, client, number, date or
               procedure) | non_factual (framing, e.g. "the evidence does not state X")
     verdict : supported | unsupported
   Rules: a factual claim is supported only if ITS cited chunk states or directly implies
   it; numbers, dates, percentages and SA references must match exactly; a factual
   sentence with no citation is unsupported (CLAUDE.md: every factual sentence must carry
   a citation). A claim the verifier does not return is treated as unsupported.
3. `apply_verdicts()` removes unsupported claims, drops lines and headings left empty,
   recomputes the citation list, and computes
       confidence = supported factual claims / factual claims   (0.0 if there are none).

4. M14b, the second opinion (app/agents/nli.py): every claim the LLM accepted is also
   checked by a small NLI model against the evidence sentences of the chunks it cites.
     LLM supported + NLI entailment     -> kept; the entailing sentences are its span
     LLM supported + NLI contradiction  -> kept but FLAGGED "check this point"
     LLM supported + NLI neutral        -> kept (the LLM's verdict stands)
     LLM unsupported                    -> removed, whatever NLI says
   A flagged claim still counts as supported in the confidence: the flag tells the reader
   to look, it does not overrule the verifier.

The graph (graph.py) loops back to retrieval once if confidence < 0.6, and abstains if it
is still low.
"""

import re
from dataclasses import dataclass, field

from app.agents.llm import LLMCaller, LLMResult
from app.agents.nli import ClaimCheck, NliScorer, check_claims
from app.agents.synthesizer import CITATION, DocInfo, document_attributes
from app.retrieval.hybrid import RetrievedChunk
from app.security.injection import UNTRUSTED_DATA_RULE, restricted_attributes, wrap_document

# Sentence boundary: . ! or ? then a space, not after a common abbreviation, and not when
# the next token is a citation (citations stay with their sentence).
# Also a boundary after a citation that follows a sentence end ("...stricter. [X:0003] It
# applies..."), but only before a capital, so a mid-sentence citation does not split.
_SENTENCE = re.compile(
    r"(?:(?<!\bvs\.)(?<!\be\.g\.)(?<!\bi\.e\.)(?<!\bpp\.)(?<!\bp\.)(?<!\bno\.)(?<!\bNo\.)(?<!\bviz\.)"
    r"(?<=[.!?])\s+(?=[^\[\s])"
    # After a citation: ":" + 1-4 digits + "]" ends both id forms (fixed-width lookbehinds).
    r"|(?:(?<=:\d\])|(?<=:\d\d\])|(?<=:\d\d\d\])|(?<=:\d{4}\]))\s+(?=[A-Z*(]))")
_BULLET = re.compile(r"^(\s*(?:[-*•]|\d+[.)])\s+)(.*)$")

SCHEMA = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "type": {"type": "string", "enum": ["factual", "non_factual"]},
                    "verdict": {"type": "string", "enum": ["supported", "unsupported"]},
                    "supporting_cites": {"type": "array", "items": {"type": "string"}},
                    "reason": {"type": "string"},
                },
                "required": ["id", "type", "verdict", "supporting_cites", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["claims"],
    "additionalProperties": False,
}

SYSTEM = f"""You verify answers written by an audit-firm knowledge assistant. For every
numbered claim, decide:
- type: "factual" if it states something about a standard, policy, circular, checklist,
  client, figure, date, deadline or procedure; "non_factual" if it is only framing (for
  example "The evidence does not state X", "This is a matter for professional judgement",
  a heading or a transition).
- verdict for a factual claim: "supported" only if the document(s) the claim cites state
  it or directly imply it. Numbers, percentages, dates, day counts and SA numbers must match
  exactly. A claim citing a document that does not support it is "unsupported", even if it
  might be true.
- A factual claim with no citation ("cites: NONE"): if one of the provided documents states
  it, mark it "supported" and put that document's cite id(s) in supporting_cites; otherwise
  "unsupported". For every other claim leave supporting_cites empty.
- verdict for a non_factual claim: "supported" if it is consistent with the evidence (for
  example, it really is not stated), otherwise "unsupported".
The attributes on each <document> tag (title, version, effective_date, status, including
which document it replaces or is replaced by and that document's effective date) are part
of the evidence for that document.
Give a one-sentence reason. Judge only against the documents provided, not your own
knowledge. Return every claim id. {UNTRUSTED_DATA_RULE}"""


@dataclass
class Claim:
    id: int
    text: str
    citations: list[str]


@dataclass
class _Line:
    prefix: str  # bullet marker or ""
    parts: list[str | int]  # literal text, or claim id
    is_heading: bool = False


@dataclass
class Verification:
    claims: list[Claim]
    verdicts: dict[int, dict]
    confidence: float
    text: str  # answer with unsupported claims removed
    cited_ids: list[str]
    removed: list[str] = field(default_factory=list)
    factual: int = 0
    supported: int = 0
    repaired: list[str] = field(default_factory=list)  # uncited claims given a checked citation
    # M14b: claims the LLM accepted but NLI found contradicted (kept, marked in the text),
    # every claim's NLI check, and each cited chunk's entailing sentences (its span).
    flagged: list[dict] = field(default_factory=list)
    nli: dict[int, ClaimCheck] = field(default_factory=dict)
    support: dict[str, list[str]] = field(default_factory=dict)


FLAG_MARK = " *(check this point)*"


def _is_heading(line: str) -> bool:
    s = line.strip()
    return s.startswith("#") or (s.startswith("**") and s.endswith("**") and s.count("**") == 2)


def extract_claims(text: str) -> tuple[list[_Line], list[Claim]]:
    """Split answer text into lines of claims (one claim per sentence)."""
    lines: list[_Line] = []
    claims: list[Claim] = []
    for raw in text.splitlines():
        if not raw.strip():
            lines.append(_Line("", [""]))
            continue
        if _is_heading(raw):
            lines.append(_Line("", [raw], is_heading=True))
            continue
        m = _BULLET.match(raw)
        prefix, body = (m.group(1), m.group(2)) if m else ("", raw.strip())
        parts: list[str | int] = []
        for sentence in _SENTENCE.split(body):
            if not sentence.strip():
                continue
            claims.append(Claim(len(claims) + 1, sentence.strip(), CITATION.findall(sentence)))
            parts.append(claims[-1].id)
        lines.append(_Line(prefix, parts))
    return lines, claims


def build_prompt(question: str, claims: list[Claim], chunks: dict[str, RetrievedChunk],
                 docs: dict[str, DocInfo] | None = None,
                 evidence_ids: list[str] | None = None) -> str:
    """Claims with their cited ids, then the full text of every cited chunk (plus the
    synthesiser's other evidence chunks, so uncited claims can be checked), carrying the
    same attributes (version, effective date, status) the synthesiser saw."""
    listed = "\n".join(
        f"[claim {c.id}] cites: {', '.join(c.citations) or 'NONE'}\n{CITATION.sub('', c.text).strip()}"
        for c in claims)
    cited = sorted(({cid for c in claims for cid in c.citations} | set(evidence_ids or []))
                   & set(chunks))
    blocks = []
    for cid in cited:
        c = chunks[cid]
        attrs = (document_attributes(cid, c, docs[c.doc_id]) if docs and c.doc_id in docs
                 else {"cite": cid, "title": c.title, **restricted_attributes(c.classification)})
        blocks.append(wrap_document(c.text, **attrs))
    body = "\n\n".join(blocks) or "(none)"
    return f"Question: {question}\n\nClaims:\n{listed}\n\nCited documents:\n{body}"


def apply_verdicts(lines: list[_Line], claims: list[Claim], verdicts: dict[int, dict],
                   allowed_cites: set[str] | None = None) -> Verification:
    """Repair verified-but-uncited claims, remove unsupported ones, compute confidence.

    An uncited factual claim counts as supported only if the verifier named a supporting
    chunk from `allowed_cites`; that citation is then attached to the sentence."""
    by_id = {c.id: c for c in claims}
    allowed = allowed_cites or set()
    repaired: list[str] = []
    for c in claims:
        v = verdicts.get(c.id, {})
        if c.citations or v.get("verdict") != "supported" or v.get("type") == "non_factual":
            continue
        cites = [x for x in v.get("supporting_cites", []) if x in allowed]
        if cites:
            c.text = c.text.rstrip() + " " + "".join(f"[{x}]" for x in dict.fromkeys(cites))
            c.citations = list(dict.fromkeys(cites))
            repaired.append(c.text)
        else:
            v["verdict"] = "unsupported"  # an uncited fact needs a named, valid source

    def keep(cid: int) -> bool:
        return verdicts.get(cid, {}).get("verdict") == "supported"

    factual = [c for c in claims if verdicts.get(c.id, {}).get("type", "factual") == "factual"]
    supported = [c for c in factual if keep(c.id)]
    removed = [c.text for c in claims if not keep(c.id)]

    rebuilt: list[tuple[str, bool]] = []  # (line text, is_heading)
    for ln in lines:
        if ln.is_heading:
            rebuilt.append((str(ln.parts[0]), True))
            continue
        ids = [p for p in ln.parts if isinstance(p, int)]
        if not ids:
            rebuilt.append(("", False))
            continue
        kept = [by_id[p].text for p in ids if keep(p)]
        if kept:
            rebuilt.append((ln.prefix + " ".join(kept), False))

    # Drop headings that no longer have content under them, then tidy blank lines.
    final: list[str] = []
    for i, (txt, heading) in enumerate(rebuilt):
        if heading:
            following = next((t for t in rebuilt[i + 1:] if t[0].strip()), None)
            if following is None or following[1]:
                continue
        final.append(txt)
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(final)).strip()

    cited: list[str] = []
    for cid in CITATION.findall(text):
        if cid not in cited:
            cited.append(cid)
    confidence = len(supported) / len(factual) if factual else 0.0
    return Verification(claims, verdicts, confidence, text, cited, removed,
                        len(factual), len(supported), repaired)


def _second_opinion(claims: list[Claim], verdicts: dict[int, dict],
                    chunks: dict[str, RetrievedChunk], evidence: dict[str, list[str]],
                    nli: NliScorer, entail_at: float, contradict_at: float
                    ) -> tuple[dict[int, ClaimCheck], list[dict]]:
    """NLI over the claims the LLM accepted; mark the contradicted ones (see module doc)."""
    from app.retrieval.compress import split_sentences

    accepted = [c for c in claims if c.citations
                and verdicts.get(c.id, {}).get("verdict") == "supported"
                and verdicts.get(c.id, {}).get("type", "factual") == "factual"]
    sentences: dict[str, list[str]] = {}
    for cid in {cid for c in accepted for cid in c.citations}:
        if evidence.get(cid):
            sentences[cid] = evidence[cid]
        elif cid in chunks:  # cited without compressed evidence: the chunk's own sentences
            sentences[cid] = split_sentences(chunks[cid].text.partition("\n")[2])
    checks = check_claims([(c.id, c.text, c.citations) for c in accepted], sentences, nli,
                          entail_at, contradict_at)
    flagged = []
    for c in accepted:
        check = checks.get(c.id)
        if check is not None and check.label == "contradiction":
            flagged.append({"claim": c.text, "entailment": round(check.entailment, 3),
                            "contradiction": round(check.contradiction, 3)})
            c.text = c.text.rstrip() + FLAG_MARK
    return checks, flagged


def verify(question: str, answer_text: str, chunks: dict[str, RetrievedChunk],
           llm: LLMCaller, docs: dict[str, DocInfo] | None = None,
           evidence_ids: list[str] | None = None, *, nli: NliScorer | None = None,
           evidence: dict[str, list[str]] | None = None, entail_at: float = 0.5,
           contradict_at: float = 0.5) -> tuple[Verification, LLMResult | None]:
    """Verify every claim of `answer_text` (without the judgement note). `evidence_ids` are
    the chunks the synthesiser was given; only these may be attached by citation repair.
    With `nli`, the accepted claims get the NLI second opinion (M14b); `evidence` holds the
    compressed sentences of each evidence chunk."""
    lines, claims = extract_claims(answer_text)
    allowed = set(evidence_ids or []) & set(chunks)
    if not claims:
        return apply_verdicts(lines, claims, {}, allowed), None
    result = llm.call(purpose="verification", role="verify", system=SYSTEM,
                      user=build_prompt(question, claims, chunks, docs, sorted(allowed)),
                      max_tokens=4000, schema=SCHEMA)
    verdicts = {int(v["id"]): v for v in (result.data or {}).get("claims", [])
                if isinstance(v.get("id"), int)}
    checks, flagged = ({}, []) if nli is None else _second_opinion(
        claims, verdicts, chunks, evidence or {}, nli, entail_at, contradict_at)
    v = apply_verdicts(lines, claims, verdicts, allowed)
    v.flagged, v.nli = flagged, checks
    kept = {c.id for c in claims if verdicts.get(c.id, {}).get("verdict") == "supported"}
    for claim_id, check in checks.items():
        if claim_id in kept:
            for cid, found in check.support.items():
                v.support.setdefault(cid, [])
                v.support[cid] += [x for x in found if x not in v.support[cid]]
    return v, result
