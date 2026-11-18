"""Score adjustments for document age and superseded versions.

- Recency (only for `time_sensitive` questions): a newer document gets a larger boost.
      freshness = 0.5 ** (age_days / half_life_days)        (1.0 today, 0.5 one half-life ago)
      multiplier = 1 + recency_weight * freshness
  Documents dated in the future count as age 0.
- Superseded documents are multiplied by `superseded_weight` (e.g. 0.5). They are
  down-weighted, NOT removed, so the synthesiser can still say what changed.
"""

from datetime import date


def freshness(effective_date: str | None, as_of: date, half_life_days: float) -> float:
    """1.0 for a document effective today, halving every `half_life_days`."""
    if not effective_date:
        return 0.0
    age = max((as_of - date.fromisoformat(effective_date)).days, 0)
    return 0.5 ** (age / half_life_days)


def adjustment(*, superseded: bool, effective_date: str | None, time_sensitive: bool,
               as_of: date, superseded_weight: float, recency_weight: float,
               half_life_days: float) -> float:
    """Multiplier applied to a chunk's fused score."""
    factor = superseded_weight if superseded else 1.0
    if time_sensitive:
        factor *= 1.0 + recency_weight * freshness(effective_date, as_of, half_life_days)
    return factor
