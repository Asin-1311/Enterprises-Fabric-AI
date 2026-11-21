"""Small-to-big: widen matching chunks to their section for exploratory questions
(CLAUDE.md section 7, step 4; spec 7.6; M14a).

Small chunks match a question precisely; an analytical or comparison answer also needs
the surrounding text. For each re-ranked chunk, in rank order, this adds:
1. its parent chunk (the section's lead: the section heading and its opening text), and
2. the other windows of the same section, in reading order,
right after it, until `max_extra` chunks have been added. Every added chunk is a real,
citable chunk, fetched with the same access checks as a search (HybridSearcher.fetch),
so expansion can never show text the user could not have retrieved.
"""

from dataclasses import replace

from sqlalchemy import select

from app.agents.reranker import Ranked
from app.db.models import Chunk
from app.retrieval.acl import UserContext
from app.retrieval.hybrid import HybridSearcher


def _section_ids(searcher: HybridSearcher, chunk_id: str) -> list[str]:
    """The parent chunk, then the windows of the same section, in order."""
    with searcher.sessions() as s:
        chunk = s.get(Chunk, chunk_id)
        if chunk is None:
            return []
        ids = [chunk.parent_id] if chunk.parent_id else []
        section_key = chunk_id.rsplit(":", 1)[0] + ":"  # "<doc>:<section>:" (stable ids, M12b)
        siblings = s.scalars(select(Chunk.chunk_id).where(Chunk.doc_id == chunk.doc_id,
                                                          Chunk.chunk_id.startswith(section_key, autoescape=True)))
        ids += sorted(siblings, key=lambda cid: int(cid.rsplit(":", 1)[1]) if cid.rsplit(":", 1)[1].isdigit() else 0)
    return [i for i in ids if i != chunk_id]


def expand_to_sections(ranked: list[Ranked], searcher: HybridSearcher, user: UserContext,
                       max_extra: int) -> tuple[list[Ranked], list[str]]:
    """`ranked` with each chunk's section added after it (at most `max_extra` chunks).
    Returns the new list and the ids that were added. Added chunks keep the score of
    the chunk they came from, so the "good chunk" counts are unchanged."""
    present = {r.chunk.chunk_id for r in ranked}
    out: list[Ranked] = []
    added: list[str] = []
    for r in ranked:
        out.append(r)
        if len(added) >= max_extra:
            continue
        wanted = [cid for cid in _section_ids(searcher, r.chunk.chunk_id) if cid not in present]
        for chunk in searcher.fetch(wanted[: max_extra - len(added)], user):
            out.append(replace(r, chunk=chunk))
            present.add(chunk.chunk_id)
            added.append(chunk.chunk_id)
    return out, added
