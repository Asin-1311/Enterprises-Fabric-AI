"""Which routing policy and which prompt versions are in use (M11, CLAUDE.md section 9).

Every trace row records the routing-policy version and, for steps that call a model, the
prompt version it used, so any answer can be traced to the exact instructions behind it.

- Routing policy version 1 is the Phase 1 rule-based router (app/agents/router.py). It is
  seeded active, because it was in use before replay tests existed. Any NEW version can be
  activated only through PUT /api/v1/routing-policy with a passing replay report.
- Prompts (M15c): the agents use the ACTIVE version in prompt_versions; the code holds
  only the seed. On a new database the seed becomes version 1, active. If the code's
  prompt later changes, that text is recorded as a NEW, INACTIVE candidate version: like
  any other new version it goes live only through POST /api/v1/admin/prompts/{id}/activate
  with a passing replay report (app/learning/prompt_replay.py). The text actually sent is
  always the active version's, so a trace's prompt_version_id is exact.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.agents import query, retriever, router, synthesizer, verifier
from app.db.models import PromptVersion, RoutingPolicy
from app.db.session import write_audit_log
from app.retrieval import compress

SEED_POLICY = {
    "kind": "rules",
    "description": "Phase 1 rule-based source routing (app/agents/router.py), with the "
                   "light model as a tie-breaker only. Engagement access checks always "
                   "run first and are never learned.",
    "retriever_weights": {"dense": 1.0, "keyword": 1.0},
}
SEED_POLICY_REPORT = {
    "replay": "not required",
    "reason": "baseline policy in use since Phase 1, before replay tests existed "
              "(CLAUDE.md section 13.3 applies to new versions)",
}

# Model-calling purpose -> its prompt in the code.
PROMPTS = {
    "query_understanding": query.SYSTEM,
    "routing_tiebreak": router.TIEBREAK_SYSTEM,
    "reformulate": retriever.REFORMULATE_SYSTEM,
    "compress": compress.SYSTEM,
    "synthesis": synthesizer.SYSTEM,
    "verification": verifier.SYSTEM,
}

# Trace step -> the prompt it uses (steps without a model call have none).
PROMPT_FOR_STEP = {
    "query_understanding": "query_understanding",
    "source_routing": "routing_tiebreak",
    "followup_retrieval": "reformulate",
    "verification_retry": "reformulate",
    "compress": "compress",
    "synthesis": "synthesis",
    "verification": "verification",
}


SEED_PROMPT_REPORT = {
    "replay": "not required",
    "reason": "seed prompt from the code, in use before prompt replay existed "
              "(CLAUDE.md section 13.3 applies to new versions)",
}

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ActiveVersions:
    policy_id: int | None
    prompt_ids: dict[str, int] = field(default_factory=dict)
    templates: dict[str, str] = field(default_factory=dict)  # purpose -> active text

    def for_step(self, step: str) -> int | None:
        return self.prompt_ids.get(PROMPT_FOR_STEP.get(step, ""))


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _ensure_policy(s: Session) -> int:
    active = s.scalars(select(RoutingPolicy).where(RoutingPolicy.is_active.is_(True))).first()
    if active is not None:
        return active.id
    policy = RoutingPolicy(version=(s.scalar(select(func.max(RoutingPolicy.version))) or 0) + 1,
                           weights=SEED_POLICY, created_by="system (M11 seed)",
                           eval_report=SEED_POLICY_REPORT, activated_at=_now(), is_active=True)
    s.add(policy)
    s.flush()
    write_audit_log(s, "system", "routing_policy_seeded", f"version {policy.version}")
    return policy.id


def _ensure_prompt(s: Session, agent: str, seed: str) -> PromptVersion:
    """The agent's active prompt version. The seed is recorded (active) on a new database;
    a changed code prompt is recorded once as an inactive candidate."""
    versions = s.scalars(select(PromptVersion).where(PromptVersion.agent == agent)
                         .order_by(PromptVersion.version)).all()
    active = next((v for v in versions if v.is_active), None)
    if active is None:  # a new database (or no active version): the seed goes live
        active = next((v for v in versions if v.template == seed), None)
        if active is None:
            active = PromptVersion(agent=agent, version=versions[-1].version + 1 if versions else 1,
                                   template=seed, created_by="system (seed)", created_at=_now(),
                                   eval_report=SEED_PROMPT_REPORT)
            s.add(active)
        active.is_active, active.activated_at = True, _now()
        s.flush()
        write_audit_log(s, "system", "prompt_version_seeded", f"{agent} v{active.version}")
    elif all(v.template != seed for v in versions):
        candidate = PromptVersion(agent=agent, version=versions[-1].version + 1, template=seed,
                                  created_by="system (code change)", created_at=_now(), is_active=False)
        s.add(candidate)
        s.flush()
        log.warning("The %s prompt in the code differs from every recorded version: recorded as "
                    "inactive candidate v%s (replay it, then activate it to use it).",
                    agent, candidate.version)
        write_audit_log(s, "system", "prompt_candidate_recorded", f"{agent} v{candidate.version}")
    return active


def ensure_active_versions(sessions: sessionmaker[Session]) -> ActiveVersions:
    """The active policy and prompt versions (ids and prompt texts), recording any that
    are missing."""
    with sessions() as s:
        policy_id = _ensure_policy(s)
        prompts = {agent: _ensure_prompt(s, agent, text) for agent, text in PROMPTS.items()}
        s.commit()
    return ActiveVersions(policy_id, {a: v.id for a, v in prompts.items()},
                          {a: v.template for a, v in prompts.items()})
