"""Replay a routing policy on recorded questions before it may go live (CLAUDE.md section
13.3; Gap Register R-13.3-replay, R-10-policy; M15b).

eval/replay/routing_replay.jsonl holds recorded inputs: each evaluation question with its
user, its labelled intent, the source types the rule router chose for it (recorded once
by scripts/make_routing_replay.py, without LLM calls) and its gold documents.

`replay_report` re-runs retrieval for every record twice, with the candidate policy and
with the baseline (the active one), and scores each by document recall@10 against the
gold documents. Bandit allocations use the posterior means (not random draws), so a
policy's score is the same every time. The candidate passes if it loses at most
REPLAY_TOLERANCE (0.02) against the baseline. PUT /api/v1/routing-policy refuses any
version without a passing report.

No LLM is called: routing replay costs nothing and runs whenever a policy is proposed.
Replaying a new PROMPT does need the model; that is M15c.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from app.adaptive.bandit import BanditState, allocate
from app.agents.plan import intent_profile
from app.config import PROJECT_ROOT, Settings
from app.retrieval.acl import load_user
from app.retrieval.hybrid import HybridSearcher

REPLAY_FILE = PROJECT_ROOT / "eval" / "replay" / "routing_replay.jsonl"
CHUNK_LIMIT, K_DOCS = 30, 10


def load_records(path: Path = REPLAY_FILE) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _doc_recall(searcher: HybridSearcher, weights_json: dict | None, record: dict, user,
                settings: Settings) -> float:
    bandit = BanditState.from_json(weights_json) if (weights_json or {}).get("kind") == "bandit" else None
    weights, top_k = intent_profile(record["intent"], settings)
    allocation = (allocate(bandit, record["intent"], record["source_types"], top_k, greedy=True)
                  if bandit is not None and record["source_types"] else None)
    hits = searcher.search(record["sub_queries"], user, source_types=record["source_types"] or None,
                           time_sensitive=record["intent"] == "time_sensitive", limit=CHUNK_LIMIT,
                           top_k=top_k, weights=weights, allocation=allocation)
    docs: list[str] = []
    for h in hits:
        if h.doc_id not in docs:
            docs.append(h.doc_id)
    gold = set(record["gold_doc_ids"])
    return len(gold & set(docs[:K_DOCS])) / len(gold)


def score(searcher: HybridSearcher, weights_json: dict | None, records: list[dict],
          settings: Settings) -> float:
    """Mean document recall@10 of a policy over the replay records."""
    if not records:
        return 0.0
    with searcher.sessions() as s:
        users = {r["user_id"]: load_user(s, r["user_id"]) for r in records}
    return sum(_doc_recall(searcher, weights_json, r, users[r["user_id"]], settings)
               for r in records) / len(records)


def replay_report(searcher: HybridSearcher, candidate: dict | None, baseline: dict | None,
                  settings: Settings, records: list[dict] | None = None,
                  baseline_version: int | None = None) -> dict:
    """Score the candidate and the baseline policy; pass if the candidate is no worse
    than the baseline by more than the tolerance."""
    records = records if records is not None else load_records()
    cand, base = score(searcher, candidate, records, settings), score(searcher, baseline, records, settings)
    return {"passed": cand >= base - settings.replay_tolerance, "score": round(cand, 4),
            "baseline_score": round(base, 4), "baseline_version": baseline_version,
            "tolerance": settings.replay_tolerance, "metric": "document recall@10",
            "questions": len(records),
            "ran_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
