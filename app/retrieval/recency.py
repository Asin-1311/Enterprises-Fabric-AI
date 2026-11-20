"""Score adjustments for document age and superseded versions.

- Recency (only for `time_sensitive` questions): a newer document gets a larger boost.
      freshness = 0.5 ** (age_days / half_life_days)        (1.0 today, 0.5 one half-life ago)
      multiplier = 1 + recency_weight * freshness
  Documents dated in the future count as age 0.
- Superseded documents are multiplied by `superseded_weight` (e.g. 0.5). They are
  down-weighted, NOT removed, so the synthesiser can still say what changed.
- Source freshness (M15a; also only for `time_sensitive` questions): a document whose
  source changed recently (`documents.updated_at`) gets a boost that falls linearly to
  nothing over `freshness_window_days`:
      multiplier = 1 + freshness_weight * max(0, 1 - days_since_change / window)
  It is applied only to time-sensitive questions because, for corpus files, the change
  time is also when a file was last indexed: boosting every question by it would favour
  whatever was re-indexed last, and would make every evaluation depend on the date.
"""

from datetime import date, datetime


def freshness(effective_date: str | None, as_of: date, half_life_days: float) -> float:
    """1.0 for a document effective today, halving every `half_life_days`."""
    if not effective_date:
        return 0.0
    age = max((as_of - date.fromisoformat(effective_date)).days, 0)
    return 0.5 ** (age / half_life_days)


def source_freshness(updated_at: datetime | None, as_of: date, window_days: float) -> float:
    """1.0 for a source changed today, falling linearly to 0.0 after `window_days`."""
    if updated_at is None or window_days <= 0:
        return 0.0
    days = max((as_of - updated_at.date()).days, 0)
    return max(0.0, 1.0 - days / window_days)


def multipliers(*, superseded: bool, effective_date: str | None, time_sensitive: bool,
                as_of: date, superseded_weight: float, recency_weight: float,
                half_life_days: float, updated_at: datetime | None = None,
                freshness_weight: float = 0.0, freshness_window_days: float = 30.0) -> dict[str, float]:
    """Each multiplier applied to a chunk's fused score, by name (1.0 = no effect).
    Recorded in the retrieval trace so every adjustment can be measured (R-8-record)."""
    recency = changed = 1.0
    if time_sensitive:
        recency = 1.0 + recency_weight * freshness(effective_date, as_of, half_life_days)
        changed = 1.0 + freshness_weight * source_freshness(updated_at, as_of, freshness_window_days)
    return {"superseded": superseded_weight if superseded else 1.0, "recency": recency,
            "source_freshness": changed}


def adjustment(**kwargs) -> float:
    """The combined multiplier: the product of `multipliers(...)`."""
    factor = 1.0
    for value in multipliers(**kwargs).values():
        factor *= value
    return factor
