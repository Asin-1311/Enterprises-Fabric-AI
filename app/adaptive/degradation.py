"""Graceful degradation under load (spec section 8; CLAUDE.md section 8, dimension 4;
Gap Register R-8-degrade; M15a).

When the p95 of full-answer time (app/adaptive/latency_window.py) is above the target
(LATENCY_TARGET_SECONDS, the spec's 8 s), the next questions are made cheaper:
1. fewer candidates per retriever (DEGRADED_CANDIDATE_FACTOR of the plan's count, at
   least 5), and compression is skipped;
2. verification is skipped ONLY for low-risk questions. Low risk means all of:
     - the intent is factual;
     - every evidence chunk comes from the ICAI standards, the FAQ or the calendar;
     - no evidence chunk is from an engagement file, superseded, or above staff level.
   Verification is never skipped for client files, superseded material, or manager- or
   partner-level content: errors there cost the most.
A question answered without verification says so first ("Reduced checking: ..."), has no
confidence score, and is stored as `degraded`, so it can be counted and reviewed. The UI
shows a badge (M16).
"""

from collections.abc import Iterable

from app.retrieval.hybrid import RetrievedChunk

REDUCED_CHECKING_NOTICE = ("Reduced checking: this answer was not fully verified because "
                           "the system is under load.")
LOW_RISK_SOURCES = ("standards", "faq", "calendar")
MIN_DEGRADED_CANDIDATES = 5


def under_load(p95_seconds: float | None, target_seconds: float, enabled: bool = True) -> bool:
    """True when the window's p95 is known and above the target."""
    return enabled and p95_seconds is not None and p95_seconds > target_seconds


def degraded_candidates(count: int, factor: float) -> int:
    return max(MIN_DEGRADED_CANDIDATES, round(count * factor))


def low_risk_reasons(intent: str, evidence: Iterable[RetrievedChunk]) -> list[str]:
    """Why a question is NOT low-risk (an empty list means it is low-risk)."""
    chunks = list(evidence)
    reasons = []
    if intent != "factual":
        reasons.append(f"intent {intent}")
    if not chunks:
        reasons.append("no evidence")
    for c in chunks:
        if c.source_type == "engagement":
            reasons.append(f"{c.chunk_id}: engagement file")
        elif c.source_type not in LOW_RISK_SOURCES:
            reasons.append(f"{c.chunk_id}: source {c.source_type}")
        if c.superseded:
            reasons.append(f"{c.chunk_id}: superseded")
        if c.access_level != "staff":
            reasons.append(f"{c.chunk_id}: {c.access_level}-level")
    return reasons
