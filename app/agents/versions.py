"""Which routing policy and which prompt versions are in use (M11, CLAUDE.md section 9).

Every trace row records the routing-policy version and, for steps that call a model, the
prompt version it used, so any answer can be traced to the exact instructions behind it.

- Routing policy version 1 is the Phase 1 rule-based router (app/agents/router.py). It is
  seeded active, because it was in use before replay tests existed. Any NEW version can be
  activated only through PUT /api/v1/routing-policy with a passing replay report.
- Prompt version 1 of each agent is the prompt currently in the code. If the code's prompt
  changes, the next version is recorded and becomes active automatically, so the table
  never claims an older text than the one actually sent. (From M15 the prompts are read
  from this table and can be rolled back; until then the code is the source.)
"""

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


@dataclass(frozen=True)
class ActiveVersions:
    policy_id: int | None
    prompt_ids: dict[str, int] = field(default_factory=dict)

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


def _ensure_prompt(s: Session, agent: str, template: str) -> int:
    active = s.scalars(select(PromptVersion).where(PromptVersion.agent == agent,
                                                   PromptVersion.is_active.is_(True))).first()
    if active is not None and active.template == template:
        return active.id
    latest = s.scalar(select(func.max(PromptVersion.version)).where(PromptVersion.agent == agent))
    if active is not None:
        active.is_active = False
    version = PromptVersion(agent=agent, version=(latest or 0) + 1, template=template,
                            created_by="system (code)", created_at=_now(), is_active=True)
    s.add(version)
    s.flush()
    write_audit_log(s, "system", "prompt_version_recorded", f"{agent} v{version.version}")
    return version.id


def ensure_active_versions(sessions: sessionmaker[Session]) -> ActiveVersions:
    """The active policy and prompt versions, recording any that are missing."""
    with sessions() as s:
        policy_id = _ensure_policy(s)
        prompt_ids = {agent: _ensure_prompt(s, agent, text) for agent, text in PROMPTS.items()}
        s.commit()
    return ActiveVersions(policy_id, prompt_ids)
