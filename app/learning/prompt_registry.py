"""The prompt registry (spec 7.9 and 7.10; CLAUDE.md sections 7, 11 and 13.3; M15c).

Each agent's prompt lives in the prompt_versions table. Exactly one version per agent is
active, and that is the text sent to the model (`PromptedLLM`). The code holds only the
seed (app/agents/versions.py).

The life of a new version:
1. created (by an administrator, POST /api/v1/admin/prompts): inactive, no report;
2. replayed (app/learning/prompt_replay.py, paid model calls): its report is stored;
3. activated (POST /api/v1/admin/prompts/{id}/activate): only with a PASSING replay report.
A version that has been active before may be activated again without a replay: that is a
rollback, and it is logged as one. Every change writes audit_log.
"""

from collections.abc import Callable
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.llm import LLMCaller, LLMResult
from app.agents.versions import PROMPTS
from app.db.models import PromptVersion
from app.db.session import write_audit_log
from app.security.injection import UNTRUSTED_DATA_RULE

MAX_TEMPLATE_CHARS = 20_000
# Agents whose requests carry retrieved document text: their prompt must keep the
# untrusted-data rule (CLAUDE.md section 7, "Rules for all agents").
READS_DOCUMENTS = frozenset({"compress", "synthesis", "verification"})


class PromptRegistryError(ValueError):
    """A request the registry refuses; the message is safe to show to an administrator."""


class PromptedLLM:
    """Sends each call with the ACTIVE prompt for its purpose instead of the code's text.

    `templates` maps purpose (query_understanding, synthesis, ...) to prompt text; a
    purpose without an entry (e.g. the baseline) keeps the system text it was given."""

    def __init__(self, llm: LLMCaller, templates: dict[str, str]) -> None:
        self.llm = llm
        self.templates = templates

    def call(self, *, purpose: str, role: str, system: str, user: str, max_tokens: int,
             schema: dict | None = None, on_text: Callable[[str], None] | None = None) -> LLMResult:
        return self.llm.call(purpose=purpose, role=role, system=self.templates.get(purpose, system),
                             user=user, max_tokens=max_tokens, schema=schema, on_text=on_text)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def template_problems(agent: str, template: str) -> list[str]:
    """Why a prompt text cannot be stored (empty list = it can)."""
    problems = []
    if agent not in PROMPTS:
        problems.append(f"Unknown agent {agent!r}; known: {', '.join(sorted(PROMPTS))}.")
    if not template.strip():
        problems.append("The prompt is empty.")
    if len(template) > MAX_TEMPLATE_CHARS:
        problems.append(f"The prompt is longer than {MAX_TEMPLATE_CHARS} characters.")
    if agent in READS_DOCUMENTS and UNTRUSTED_DATA_RULE not in template:
        problems.append("This agent reads retrieved documents: its prompt must keep the "
                        "untrusted-data rule word for word.")
    return problems


def replay_passed(version: PromptVersion) -> bool:
    replay = (version.eval_report or {}).get("replay")
    return isinstance(replay, dict) and replay.get("passed") is True


def create_version(s: Session, agent: str, template: str, actor: str) -> PromptVersion:
    """Store a new, inactive version of `agent`'s prompt."""
    problems = template_problems(agent, template)
    if problems:
        raise PromptRegistryError(" ".join(problems))
    latest = s.scalars(select(PromptVersion).where(PromptVersion.agent == agent)
                       .order_by(PromptVersion.version.desc())).first()
    version = PromptVersion(agent=agent, version=(latest.version + 1) if latest else 1,
                            template=template, created_by=actor, created_at=_now(), is_active=False)
    s.add(version)
    s.flush()
    write_audit_log(s, actor, "prompt_version_created", f"{agent} v{version.version}")
    return version


def activate(s: Session, version_id: int, actor: str) -> tuple[PromptVersion, str]:
    """Make a version the active one for its agent. Returns (version, action), where action
    is "unchanged", "prompt_activated" (passing replay) or "prompt_rolled_back"."""
    version = s.get(PromptVersion, version_id)
    if version is None:
        raise LookupError(f"There is no prompt version {version_id}.")
    if version.is_active:
        return version, "unchanged"
    current = s.scalars(select(PromptVersion).where(PromptVersion.agent == version.agent,
                                                    PromptVersion.is_active.is_(True))).all()
    if replay_passed(version):
        against = version.eval_report["replay"].get("baseline_version_id")
        if against is not None and against not in {c.id for c in current}:
            raise PromptRegistryError(f"{version.agent} v{version.version} was replayed against a "
                                      "version that is no longer active: replay it again.")
        action = "prompt_activated"
    elif version.activated_at is not None:
        action = "prompt_rolled_back"
    else:
        raise PromptRegistryError(f"{version.agent} v{version.version} has no passing replay "
                                  "report, so it cannot be activated.")
    for other in current:
        other.is_active = False
    version.is_active, version.activated_at = True, _now()
    s.flush()
    was = ", ".join(f"v{o.version}" for o in current) or "none"
    write_audit_log(s, actor, action, f"{version.agent} v{version.version} (was {was})")
    return version, action
