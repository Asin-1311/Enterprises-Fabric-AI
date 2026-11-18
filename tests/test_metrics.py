"""Metric functions checked against hand-computed values (CLAUDE.md section 13)."""

import math

import pytest

from eval.metrics import (
    dedupe_docs,
    mean,
    ndcg_at_k,
    recall_at_k,
    reciprocal_rank,
    superseded_order_ok,
)

RANKED = ["A", "X", "B", "Y", "C"]  # relevant: A (rank 1), B (rank 3)
GOLD = {"A", "B", "D"}  # D is never retrieved


def test_dedupe_keeps_best_rank() -> None:
    assert dedupe_docs(["A", "A", "B", "A", "C", "B"]) == ["A", "B", "C"]


def test_recall_at_k() -> None:
    assert recall_at_k(RANKED, GOLD, 1) == pytest.approx(1 / 3)
    assert recall_at_k(RANKED, GOLD, 5) == pytest.approx(2 / 3)
    assert recall_at_k([], GOLD, 5) == 0.0


def test_reciprocal_rank() -> None:
    assert reciprocal_rank(RANKED, GOLD) == 1.0
    assert reciprocal_rank(["X", "Y", "B"], GOLD) == pytest.approx(1 / 3)
    assert reciprocal_rank(["X", "Y"], GOLD) == 0.0


def test_ndcg_hand_computed() -> None:
    # DCG = 1/log2(2) + 1/log2(4) = 1 + 0.5 = 1.5
    # IDCG (3 gold, k=5) = 1/log2(2) + 1/log2(3) + 1/log2(4) = 1 + 0.63093 + 0.5 = 2.13093
    expected = 1.5 / (1 + 1 / math.log2(3) + 0.5)
    assert ndcg_at_k(RANKED, GOLD, 5) == pytest.approx(expected)
    assert ndcg_at_k(["A", "B", "D"], GOLD, 10) == pytest.approx(1.0)  # perfect ranking
    assert ndcg_at_k(["X"], GOLD, 10) == 0.0


def test_superseded_order_compares_the_specific_versions() -> None:
    pair = [("POL-2", "POL-1")]
    assert superseded_order_ok(["POL-2", "POL-1"], pair)
    assert superseded_order_ok(["FAQ", "POL-2"], pair)  # old version absent: fine
    assert not superseded_order_ok(["FAQ", "POL-1", "POL-2"], pair)  # FAQ first does not count
    assert not superseded_order_ok(["FAQ"], pair)  # current version missing
    assert not superseded_order_ok(["A2", "B1", "B2", "A1"], [("A2", "A1"), ("B2", "B1")])


def test_undefined_cases_raise_or_return_none() -> None:
    with pytest.raises(ValueError):
        recall_at_k(RANKED, set(), 5)
    with pytest.raises(ValueError):
        ndcg_at_k(RANKED, set(), 5)
    assert mean([]) is None
    assert mean([1.0, 0.0]) == 0.5
