"""Turning the logs into a new routing policy version (spec 7.4, 7.9 and 8; CLAUDE.md
sections 8 and 11; Gap Register R-7.4, R-7.9, R-8-bandit; M15b).

    .venv\\Scripts\\python.exe -m app.adaptive.routing_policy --propose
    .venv\\Scripts\\python.exe -m app.adaptive.routing_policy --replay <version>

Rewards. An answer counts once it is REWARD_WINDOW_HOURS (24) old, so its feedback is in:
- the answer: reward 1 if it was answered and verified with confidence >= 0.6 and got no
  "not helpful" within the window; otherwise 0 (weight 1);
- implicit signals from the UI (weaker, CLAUDE.md section 11):
      copy            reward 1, weight 0.5   (the user took the answer away)
      citation_click  reward 1, weight 0.25  (the sources were worth opening)
      follow_up       reward 0, weight 0.25  (the answer may have been incomplete)
      rephrase        reward 0, weight 0.5   (the answer missed the question)
- degraded answers (not verified, M15a), denials and greetings are not learned from.
Credit goes to the arms (source type x retriever) that found the answer's cited chunks;
an answer without citations counts against every arm that was searched.

Proposing. `propose` starts from the active policy (or an empty prior if the active one
is the Phase 1 rules), adds the observations made since that policy was built, replays
the result against the active policy (app/adaptive/replay.py) and saves it as a NEW,
INACTIVE version with its replay report. It goes live only through PUT
/api/v1/routing-policy, by an administrator, and only if the replay passed.
"""

import argparse
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.adaptive.bandit import BanditState, arm_key
from app.config import Settings
from app.db.models import (
    Answer,
    Chunk,
    Citation,
    Document,
    Feedback,
    FeedbackSignal,
    Query,
    Retrieval,
    RoutingPolicy,
)
from app.db.session import write_audit_log

SIGNAL_REWARDS = {"copy": (1.0, 0.5), "citation_click": (1.0, 0.25),
                  "follow_up": (0.0, 0.25), "rephrase": (0.0, 0.5)}
LEARNED_STATUSES = ("answered", "abstained", "not_found")


@dataclass(frozen=True)
class Observation:
    intent: str
    arms: tuple[str, ...]
    reward: float
    weight: float
    answer_id: int
    kind: str  # answer | copy | citation_click | follow_up | rephrase


def _naive(moment: datetime) -> datetime:
    return moment.astimezone(timezone.utc).replace(tzinfo=None) if moment.tzinfo else moment


def arms_for(s: Session, query_id: int, answer_id: int) -> tuple[str, ...]:
    """The arms credited for an answer: those whose searches found its cited chunks, or
    every arm that found anything if it cites nothing."""
    cited = set(s.scalars(select(Citation.chunk_id).where(Citation.answer_id == answer_id)))
    rows = s.scalars(select(Retrieval).where(Retrieval.query_id == query_id,
                                             Retrieval.method.in_(("dense", "keyword")))).all()
    found = {(row.method, e["chunk_id"]) for row in rows for e in (row.scores or [])}
    ids = {cid for _, cid in found}
    source_of = dict(s.execute(select(Chunk.chunk_id, Document.source_type).join(
        Document, Document.doc_id == Chunk.doc_id).where(Chunk.chunk_id.in_(ids))).all()) if ids else {}
    target = cited or ids
    return tuple(sorted({arm_key(source_of[cid], method) for method, cid in found
                         if cid in target and cid in source_of}))


def observations(sessions: sessionmaker[Session], settings: Settings, now: datetime,
                 since: datetime | None = None) -> list[Observation]:
    """Every reward the logs give for answers asked in (since, now - window]."""
    window = timedelta(hours=settings.reward_window_hours)
    cutoff = _naive(now) - window
    out: list[Observation] = []
    with sessions() as s:
        stmt = (select(Answer, Query).join(Query, Query.id == Answer.query_id)
                .where(Query.created_at <= cutoff, Answer.status.in_(LEARNED_STATUSES),
                       Answer.degraded.is_(False)))
        if since is not None:
            stmt = stmt.where(Query.created_at > _naive(since))
        for answer, query in s.execute(stmt).all():
            arms = arms_for(s, query.id, answer.id)
            if not arms:
                continue
            intent = query.intent or "factual"
            unhelpful = s.scalars(select(Feedback).where(
                Feedback.answer_id == answer.id, Feedback.rating == "not_helpful")).all()
            unhelpful_in_window = any(f.created_at is None or f.created_at <= query.created_at + window
                                      for f in unhelpful)
            success = (answer.status == "answered" and (answer.confidence or 0.0) >= settings.confidence_threshold
                       and not unhelpful_in_window)
            out.append(Observation(intent, arms, 1.0 if success else 0.0, 1.0, answer.id, "answer"))
            for kind in s.scalars(select(FeedbackSignal.kind).where(FeedbackSignal.answer_id == answer.id)):
                if kind in SIGNAL_REWARDS:
                    reward, weight = SIGNAL_REWARDS[kind]
                    out.append(Observation(intent, arms, reward, weight, answer.id, kind))
    return out


def learn(state: BanditState, observed: list[Observation]) -> BanditState:
    """A copy of `state` with every observation added to its arms."""
    new = BanditState({i: {k: list(v) for k, v in arms.items()} for i, arms in state.arms.items()})
    for o in observed:
        for arm in o.arms:
            new.update(o.intent, arm, o.reward, o.weight)
    return new


def active_policy(s: Session) -> RoutingPolicy | None:
    return s.scalars(select(RoutingPolicy).where(RoutingPolicy.is_active.is_(True))).first()


def propose(sessions: sessionmaker[Session], searcher, settings: Settings, actor: str = "learning",
            now: datetime | None = None, records: list[dict] | None = None) -> RoutingPolicy:
    """Build, replay and save a new inactive policy version from the logs."""
    from app.adaptive.replay import replay_report

    from app.agents.versions import ensure_active_versions

    now = now or datetime.now(timezone.utc)
    ensure_active_versions(sessions)  # the baseline (seed rules v1) exists before any proposal
    with sessions() as s:
        active = active_policy(s)
        base_json = (active.weights or {}) if active else {}
        base_version = active.version if active else None
    built_until = base_json.get("built_until") if base_json.get("kind") == "bandit" else None
    since = datetime.fromisoformat(built_until) if built_until else None
    observed = observations(sessions, settings, now, since)
    state = learn(BanditState.from_json(base_json), observed)
    cutoff = _naive(now) - timedelta(hours=settings.reward_window_hours)
    weights = state.to_json() | {
        "built_until": cutoff.isoformat(timespec="seconds"), "based_on_version": base_version,
        "observations": {"answers": sum(o.kind == "answer" for o in observed),
                         "signals": sum(o.kind != "answer" for o in observed)},
        "description": "Thompson-sampling allocation over (source type, retriever) per intent, "
                       "learned from verified answers, feedback and implicit signals."}
    report = replay_report(searcher, weights, base_json or None, settings, records, base_version)
    with sessions() as s:
        version = (s.scalar(select(func.max(RoutingPolicy.version))) or 0) + 1
        policy = RoutingPolicy(version=version, weights=weights, created_by=actor,
                               eval_report={"replay": report}, is_active=False)
        s.add(policy)
        s.flush()
        write_audit_log(s, actor, "routing_policy_proposed",
                        f"version {version}: replay {'passed' if report['passed'] else 'FAILED'} "
                        f"({report['score']} vs {report['baseline_score']})")
        s.refresh(policy)
        s.expunge(policy)
    return policy


def main(argv: list[str] | None = None) -> int:
    from app.config import get_settings
    from app.retrieval.dense import QdrantInUseError
    from app.retrieval.hybrid import build_searcher

    parser = argparse.ArgumentParser(description="Learn and replay routing policies.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--propose", action="store_true", help="learn a new version from the logs")
    group.add_argument("--replay", type=int, metavar="VERSION", help="replay an existing version")
    args = parser.parse_args(argv)
    settings = get_settings()
    try:
        searcher = build_searcher(settings)
    except QdrantInUseError as exc:
        print(exc)
        return 2
    if args.propose:
        policy = propose(searcher.sessions, searcher, settings)
        r = policy.eval_report["replay"]
        print(f"Routing policy version {policy.version} proposed (inactive). Replay "
              f"{'passed' if r['passed'] else 'FAILED'}: {r['score']} vs baseline {r['baseline_score']}.")
        return 0
    from app.adaptive.replay import replay_report

    with searcher.sessions() as s:
        policy = s.scalars(select(RoutingPolicy).where(RoutingPolicy.version == args.replay)).first()
        active = active_policy(s)
        if policy is None:
            print(f"No routing policy version {args.replay}.")
            return 1
        report = replay_report(searcher, policy.weights, active.weights if active else None, settings,
                               baseline_version=active.version if active else None)
        policy.eval_report = {**(policy.eval_report or {}), "replay": report}
        s.commit()
        write_audit_log(s, "learning", "routing_policy_replayed", f"version {args.replay}: {report['passed']}")
    print(f"Version {args.replay}: replay {'passed' if report['passed'] else 'FAILED'} "
          f"({report['score']} vs {report['baseline_score']}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
