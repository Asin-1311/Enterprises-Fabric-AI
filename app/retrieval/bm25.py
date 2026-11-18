"""Lexical (BM25) index over chunk text, using rank_bm25.

The index lives in memory and is rebuilt from the SQLite `chunks` table after every
change (CLAUDE.md section 6). At this corpus size (about 1,000 chunks) a rebuild takes
well under a second, so there is no need for incremental BM25 updates.
"""

import math
import re
from collections.abc import Iterable

from rank_bm25 import BM25Okapi
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Chunk

_TOKEN = re.compile(r"[a-z0-9]+")
# Very small stop-word list: common function words only. Numbers are kept ("sa 500",
# "60 days"), because section and standard numbers are what auditors search for.
STOPWORDS = frozenset(
    "a an and are as at be by for from has have in is it its of on or that the this to "
    "was were will with which".split()
)


def tokenize(text: str) -> list[str]:
    """Lower-case word and number tokens, without stop words."""
    return [t for t in _TOKEN.findall(text.lower()) if t not in STOPWORDS]


class LuceneIdfBM25(BM25Okapi):
    """BM25Okapi with Lucene's always-positive IDF: log(1 + (N - n + 0.5) / (n + 0.5)).

    rank_bm25's default Okapi IDF, log((N - n + 0.5) / (n + 0.5)), is zero or negative
    for a term found in half or more of the chunks, so a perfectly relevant chunk can score
    0 (found by the M2 tests on a small corpus). The Lucene form keeps every matching
    term's contribution positive while preserving the ranking idea (rarer = stronger).
    """

    def _calc_idf(self, nd: dict[str, int]) -> None:
        self.idf = {
            word: math.log(1 + (self.corpus_size - freq + 0.5) / (freq + 0.5))
            for word, freq in nd.items()
        }


class Bm25Index:
    """In-memory BM25 index of chunk texts."""

    def __init__(self) -> None:
        self.chunk_ids: list[str] = []
        self._id_set: set[str] = set()
        self._bm25: LuceneIdfBM25 | None = None

    def __len__(self) -> int:
        return len(self.chunk_ids)

    def __contains__(self, chunk_id: object) -> bool:
        return chunk_id in self._id_set

    def rebuild(self, rows: Iterable[tuple[str, str]]) -> None:
        """Rebuild from (chunk_id, text) pairs."""
        rows = sorted(rows)  # deterministic order
        self.chunk_ids = [cid for cid, _ in rows]
        self._id_set = set(self.chunk_ids)
        corpus = [tokenize(text) for _, text in rows]
        self._bm25 = LuceneIdfBM25(corpus) if corpus else None

    def rebuild_from_db(self, session: Session) -> None:
        """Rebuild from every chunk in SQLite."""
        rows = session.execute(select(Chunk.chunk_id, Chunk.text)).all()
        self.rebuild((cid, text) for cid, text in rows)

    def search(self, query: str, top_k: int = 20,
               allowed_ids: set[str] | None = None) -> list[tuple[str, float]]:
        """Top-k (chunk_id, score) pairs.

        With `allowed_ids` (the ACL candidate set) only those chunks are scored at all;
        forbidden chunks are skipped before scoring, not filtered out afterwards.
        """
        if self._bm25 is None:
            return []
        terms = tokenize(query)
        if allowed_ids is None:
            positions = list(range(len(self.chunk_ids)))
            scores = list(self._bm25.get_scores(terms))
        else:
            positions = [i for i, cid in enumerate(self.chunk_ids) if cid in allowed_ids]
            scores = list(self._bm25.get_batch_scores(terms, positions)) if positions else []
        ranked = sorted(((self.chunk_ids[i], float(s)) for i, s in zip(positions, scores)),
                        key=lambda pair: (-pair[1], pair[0]))
        return [(cid, s) for cid, s in ranked[:top_k] if s > 0]
