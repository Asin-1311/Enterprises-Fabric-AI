"""Tests for app/retrieval/bm25.py, checked against hand-computed values."""

import math

import pytest

from app.retrieval.bm25 import Bm25Index, tokenize


def test_tokenize_keeps_numbers_and_drops_stopwords() -> None:
    assert tokenize("The SA 500 requires sufficient evidence, within 60 days.") == [
        "sa", "500", "requires", "sufficient", "evidence", "within", "60", "days"]


def test_score_matches_hand_computation() -> None:
    # Two docs of length 2 (avgdl = 2). Query "apple" appears once in doc A only.
    # Lucene IDF: ln(1 + (N - n + 0.5) / (n + 0.5)) = ln(1 + 1.5/1.5) = ln 2.
    # TF part with k1 = 1.5, b = 0.75, dl = avgdl: 1 * 2.5 / (1 + 1.5) = 1.
    # Score = ln 2.
    idx = Bm25Index()
    idx.rebuild([("A", "apple banana"), ("B", "cherry banana")])
    (cid, score), = idx.search("apple")
    assert cid == "A"
    assert score == pytest.approx(math.log(2))


def test_term_in_every_doc_still_scores_positive() -> None:
    # With rank_bm25's default Okapi IDF this term would score <= 0 and be dropped.
    idx = Bm25Index()
    idx.rebuild([("A", "banana split"), ("B", "banana bread")])
    assert {cid for cid, _ in idx.search("banana")} == {"A", "B"}


def test_allowed_ids_filter_and_empty_index() -> None:
    idx = Bm25Index()
    assert idx.search("anything") == []
    idx.rebuild([("A", "audit evidence"), ("B", "audit sampling")])
    assert [c for c, _ in idx.search("audit", allowed_ids={"B"})] == ["B"]
