"""Agent performance for the admin console (spec 7.10; CLAUDE.md section 11; M16c).

Computed from the stored traces and answers of a time window: no estimate is made up and
no model is called. (M17 adds Prometheus; the console's numbers then come from there too.)

- per agent: steps, latency p50 / p95 (ms), input and output tokens;
- answers: how many, by status; the verification failure rate (a verification step that
  removed at least one claim or ended below the confidence threshold); the share answered
  with reduced checking (degraded) and from the response cache;
- cost per answer: an ESTIMATE, token counts x the list prices in eval/costs.py, labelled
  as such. Steps whose model has no listed price (e.g. the private model) count as 0.
"""

import json
from collections import defaultdict
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Answer, Query, Trace


def percentile(values: list[float], q: float) -> float | None:
    """Nearest-rank percentile (q in 0..100) of a list, or None if empty."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, -(-len(ordered) * q // 100))  # ceil(n * q / 100)
    return ordered[int(rank) - 1]


def _output(trace: Trace) -> dict:
    try:
        out = json.loads(trace.output) if trace.output else {}
    except json.JSONDecodeError:
        return {}
    return out if isinstance(out, dict) else {}


def agent_performance(s: Session, start: datetime, end: datetime,
                      confidence_threshold: float) -> dict:
    """Figures for traces and answers of questions asked in [start, end)."""
    from eval.costs import estimate_usd

    traces = s.execute(select(Trace).join(Query, Query.id == Trace.query_id)
                       .where(Query.created_at >= start, Query.created_at < end)).scalars().all()
    answers = s.execute(select(Answer).join(Query, Query.id == Answer.query_id)
                        .where(Query.created_at >= start, Query.created_at < end)).scalars().all()

    by_agent: dict[str, dict] = defaultdict(lambda: {"steps": 0, "latencies": [], "tokens_in": 0,
                                                     "tokens_out": 0})
    cost, verifications, failed = 0.0, 0, 0
    cache_hits = 0
    for t in traces:
        a = by_agent[t.agent]
        a["steps"] += 1
        if t.latency_ms is not None:
            a["latencies"].append(t.latency_ms)
        a["tokens_in"] += t.tokens_in or 0
        a["tokens_out"] += t.tokens_out or 0
        if t.model and (t.tokens_in or t.tokens_out):
            # A step with several calls of different models is priced at its first model.
            cost += estimate_usd(t.model.split(",")[0], t.tokens_in or 0, t.tokens_out or 0)
        if t.agent == "verification":
            verifications += 1
            out = _output(t)
            if out.get("removed") or (out.get("confidence") is not None
                                      and out["confidence"] < confidence_threshold):
                failed += 1
        cache_hits += t.agent == "response_cache"

    status: dict[str, int] = defaultdict(int)
    for answer in answers:
        status[answer.status] += 1
    n = len(answers)
    return {
        "from": start.isoformat(), "to": end.isoformat(),
        "answers": n, "answers_by_status": dict(sorted(status.items())),
        "verification_steps": verifications,
        "verification_failure_rate": round(failed / verifications, 4) if verifications else None,
        "degraded_rate": round(sum(a.degraded for a in answers) / n, 4) if n else None,
        "cache_hit_rate": round(cache_hits / n, 4) if n else None,
        "estimated_cost_per_answer_usd": round(cost / n, 5) if n else None,
        "cost_basis": "estimate: token counts x eval/costs.py list prices; unpriced models count 0",
        "agents": [{"agent": name, "steps": a["steps"],
                    "latency_p50_ms": percentile(a["latencies"], 50),
                    "latency_p95_ms": percentile(a["latencies"], 95),
                    "tokens_in": a["tokens_in"], "tokens_out": a["tokens_out"]}
                   for name, a in sorted(by_agent.items())],
    }
