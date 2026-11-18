"""Single-pass RAG baseline for the ablation (CLAUDE.md section 11).

Same as the full system:   the synthesis model (MODEL_SYNTH), the ACL filter applied inside
                           retrieval (so leak rates are comparable), delimited evidence
                           blocks and the untrusted-data rule.
Removed:                   query understanding, routing, BM25 and RRF (dense retrieval
                           only), re-ranking, compression, follow-up retrieval, the
                           engagement denial step, verification and abstention.

One dense search on the raw question (top 5), one model call.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from app.agents.llm import LLMCaller, LLMResult
from app.agents.synthesizer import CITATION
from app.retrieval.acl import UserContext
from app.retrieval.hybrid import HybridSearcher, RetrievedChunk
from app.security.injection import UNTRUSTED_DATA_RULE, wrap_document

TOP_K = 5
SYSTEM = (
    "You are a knowledge assistant for audit staff at Example & Associates, Chartered "
    "Accountants (a fictional firm). Answer the question using the context documents. Cite "
    "the documents you use with their cite id in square brackets, e.g. [STD-CH06:0012]. If "
    "the context does not contain the answer, say so. " + UNTRUSTED_DATA_RULE
)


@dataclass
class BaselineResult:
    text: str
    cited_chunk_ids: list[str]
    retrieved: list[RetrievedChunk]
    llm: LLMResult
    seconds: float
    seconds_to_first_token: float | None
    invalid_citations: list[str] = field(default_factory=list)


class SinglePassRAG:
    """Dense retrieval + one synthesis call."""

    def __init__(self, searcher: HybridSearcher, llm: LLMCaller, top_k: int = TOP_K) -> None:
        self.searcher, self.llm, self.top_k = searcher, llm, top_k

    def ask(self, question: str, user: UserContext,
            on_text: Callable[[str], None] | None = None) -> BaselineResult:
        start = time.perf_counter()
        first: list[float] = []

        def on_chunk(text: str) -> None:
            if not first:
                first.append(time.perf_counter() - start)
            if on_text:
                on_text(text)

        hits = self.searcher.search(question, user, mode="dense", limit=self.top_k)
        blocks = [wrap_document(h.text, cite=h.chunk_id, title=h.title,
                                effective_date=h.effective_date or "") for h in hits]
        user_msg = f"Question: {question}\n\nContext:\n" + ("\n\n".join(blocks) or "(none)")
        result = self.llm.call(purpose="baseline", role="synth", system=SYSTEM, user=user_msg,
                               max_tokens=2000, on_text=on_chunk)
        allowed = {h.chunk_id for h in hits}
        cited, invalid = [], []
        for cid in CITATION.findall(result.text):
            target = cited if cid in allowed else invalid
            if cid not in target:
                target.append(cid)
        return BaselineResult(result.text.strip(), cited, hits, result,
                              time.perf_counter() - start, first[0] if first else None, invalid)
