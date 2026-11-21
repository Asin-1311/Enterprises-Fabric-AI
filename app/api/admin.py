"""Administration endpoints (M11, CLAUDE.md section 10). Routes under /api/v1.

Only users with the admin flag (`users.is_admin`) may call them. The flag is a capability
for managing the system: it gives NO extra document access (access still comes from role
and engagement team, enforced in retrieval).

- POST /sources                registers a source; connector settings are checked and any
                               secret written into them is refused (use "env:NAME").
- GET  /sources/{id}/status    ingestion status, last change, counts, freshness lag.
- PUT  /routing-policy         activates a routing-policy version, only with a passing replay
                               report (or a version that was active before: a rollback).
- GET  /admin/prompts          every prompt version (optionally of one agent) (M15c).
- POST /admin/prompts          stores a new, inactive prompt version.
- POST /admin/prompts/{id}/activate
                               makes it the agent's active prompt, only with a passing replay
                               report (or a version that was active before: a rollback).
Every change is written to audit_log. (POST /documents/reindex is in main.py.)
"""

import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi import Query as Q
from sqlalchemy import func, select

from app.api.auth import current_user
from app.api.schemas import (
    AdminQueryItem,
    AgentPerformanceOut,
    ClassificationChangeOut,
    ClassificationPolicyCreate,
    ClassificationPolicyOut,
    PromptActivationOut,
    PromptCreate,
    PromptVersionOut,
    RoutingPolicyActivate,
    RoutingPolicyOut,
    SourceCreate,
    SourceOut,
    SourceStatusOut,
)
from app.db.models import (
    Answer,
    Chunk,
    ClassificationPolicy,
    Document,
    PromptVersion,
    Query,
    RoutingPolicy,
    Source,
    User,
)
from app.db.session import write_audit_log
from app.learning import prompt_registry

router = APIRouter(prefix="/api/v1", tags=["admin"])
log = logging.getLogger("uvicorn.error")

ADMIN_ONLY = "This action is restricted to administrators."

# Settings each connector type needs (connectors: app/connectors/, M12c). Polling types
# also need "defaults" (checked in app/connectors/config_problems).
REQUIRED_SETTINGS: dict[str, tuple[str, ...]] = {
    "filesystem": ("path",),
    "git": ("url", "branch"),
    "s3": ("bucket",),
    "webhook": ("secret",),  # must be a reference: "env:NAME"
    "dms": ("table",),
    "confluence": ("base_url", "space"),
    "jira": ("base_url", "project"),
    "sharepoint": ("base_url", "site"),
}
# A setting whose NAME looks like a credential must hold a reference, never the value.
_SECRET_NAME = re.compile(r"pass(word|wd)?|secret|token|api[_-]?key|access[_-]?key|"
                          r"credential|private[_-]?key", re.I)


def require_admin(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(403, ADMIN_ONLY)
    return user


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def secret_problems(config: Any, where: str = "connector_config") -> list[str]:
    """Settings that would store a secret in the database (empty list = none)."""
    problems = []
    if isinstance(config, dict):
        for key, value in config.items():
            path = f"{where}.{key}"
            if _SECRET_NAME.search(str(key)) and not (isinstance(value, str)
                                                       and value.startswith("env:")):
                problems.append(f'{path} looks like a secret: store it in .env and write '
                                f'"env:NAME" here instead.')
            problems += secret_problems(value, path)
    elif isinstance(config, list):
        for i, item in enumerate(config):
            problems += secret_problems(item, f"{where}[{i}]")
    return problems


def _setting_problems(req: SourceCreate, corpus_dir: Path, project_root: Path) -> list[str]:
    problems = [f'connector_config needs "{key}" for a {req.type} source'
                for key in REQUIRED_SETTINGS[req.type] if not req.connector_config.get(key)]
    if req.type == "filesystem" and req.connector_config.get("path"):
        raw = str(req.connector_config["path"])
        folder = (project_root / raw).resolve()
        if Path(raw).is_absolute() or ".." in Path(raw).parts \
                or corpus_dir.resolve() not in (folder, *folder.parents):
            problems.append("a filesystem source must be a folder inside data/corpus/")
        elif not folder.is_dir():
            problems.append(f"folder not found: {raw}")
    from app.connectors import config_problems

    problems += config_problems(req.type, req.connector_config, project_root)
    return problems + secret_problems(req.connector_config)


def _source_out(source: Source) -> SourceOut:
    return SourceOut(id=source.id, type=source.type, connector_config=source.connector_config,
                     owner=source.owner, classification=source.classification,
                     status=source.status, last_event_at=source.last_event_at,
                     created_at=source.created_at)


# --------------------------------------------------------------------------- sources

@router.post("/sources", response_model=SourceOut, status_code=201)
def register_source(req: SourceCreate, request: Request,
                    admin: User = Depends(require_admin)) -> SourceOut:
    """Register a source of documents. A polling source (git, s3, confluence, jira,
    sharepoint) is picked up by the connector runner on its next round."""
    settings = request.app.state.services.settings
    from app.config import PROJECT_ROOT

    problems = _setting_problems(req, settings.corpus_dir, PROJECT_ROOT)
    if problems:
        raise HTTPException(422, problems)
    with request.app.state.services.sessions() as s:
        if s.get(Source, req.id) is not None:
            raise HTTPException(409, f"A source with id {req.id} already exists.")
        if req.owner is not None and s.get(User, req.owner) is None:
            raise HTTPException(422, [f"owner {req.owner} is not a known user"])
        source = Source(id=req.id, type=req.type, connector_config=req.connector_config,
                        owner=req.owner, classification=req.classification, status="active",
                        created_at=_now())
        s.add(source)
        s.flush()
        write_audit_log(s, admin.user_id, "source_registered",
                        f"{req.id} ({req.type}, {req.classification})")
        return _source_out(source)


@router.get("/sources/{source_id}/status", response_model=SourceStatusOut)
def source_status(source_id: str, request: Request,
                  _: User = Depends(require_admin)) -> SourceStatusOut:
    """How up to date one source is in the indexes."""
    with request.app.state.services.sessions() as s:
        source = s.get(Source, source_id)
        if source is None:
            raise HTTPException(404, "unknown source")
        documents, last_indexed = s.execute(
            select(func.count(Document.doc_id), func.max(Document.indexed_at))
            .where(Document.source_id == source_id)).one()
        chunks = s.scalar(select(func.count(Chunk.chunk_id))
                          .join(Document, Document.doc_id == Chunk.doc_id)
                          .where(Document.source_id == source_id))
    last_event = source.last_event_at.replace(tzinfo=None) if source.last_event_at else None
    lag = (_now() - last_event).total_seconds() if last_event else None
    return SourceStatusOut(id=source.id, type=source.type, status=source.status,
                           classification=source.classification, documents=documents,
                           chunks=chunks or 0, last_event_at=last_event,
                           last_indexed_at=last_indexed, freshness_lag_seconds=lag)


# --------------------------------------------------------------------------- routing policy

def _policy_out(p: RoutingPolicy) -> RoutingPolicyOut:
    return RoutingPolicyOut(id=p.id, version=p.version, weights=p.weights, created_by=p.created_by,
                            eval_report=p.eval_report, activated_at=p.activated_at,
                            is_active=p.is_active)


def replay_passed(policy: RoutingPolicy) -> bool:
    """A new version may go live only with a passing replay report (CLAUDE.md 13.3)."""
    replay = (policy.eval_report or {}).get("replay")
    return isinstance(replay, dict) and replay.get("passed") is True


@router.put("/routing-policy", response_model=RoutingPolicyOut)
def activate_routing_policy(req: RoutingPolicyActivate, request: Request,
                            admin: User = Depends(require_admin)) -> RoutingPolicyOut:
    """Make one routing-policy version the active one.

    Allowed when the version has a passing replay report, or was active before (rolling
    back to a version that already served users). Everything else is refused."""
    with request.app.state.services.sessions() as s:
        policy = s.scalars(select(RoutingPolicy).where(RoutingPolicy.version == req.version)).first()
        if policy is None:
            raise HTTPException(404, f"There is no routing policy version {req.version}.")
        if not policy.is_active:
            if not (replay_passed(policy) or policy.activated_at is not None):
                raise HTTPException(409, f"Version {req.version} has no passing replay report, "
                                         "so it cannot be activated.")
            for other in s.scalars(select(RoutingPolicy).where(RoutingPolicy.is_active.is_(True))):
                other.is_active = False
            policy.is_active, policy.activated_at = True, _now()
            s.flush()
            write_audit_log(s, admin.user_id, "routing_policy_activated", f"version {req.version}")
        return _policy_out(policy)


# --------------------------------------------------------------------------- prompt registry

def _prompt_out(p: PromptVersion) -> PromptVersionOut:
    return PromptVersionOut(id=p.id, agent=p.agent, version=p.version, template=p.template,
                            eval_score=p.eval_score, eval_report=p.eval_report,
                            created_by=p.created_by, created_at=p.created_at,
                            activated_at=p.activated_at, is_active=p.is_active)


@router.get("/admin/prompts", response_model=list[PromptVersionOut])
def list_prompts(request: Request, agent: str | None = None,
                 admin: User = Depends(require_admin)) -> list[PromptVersionOut]:
    """Every prompt version, by agent and version (optionally of one agent)."""
    stmt = select(PromptVersion).order_by(PromptVersion.agent, PromptVersion.version)
    if agent is not None:
        stmt = stmt.where(PromptVersion.agent == agent)
    with request.app.state.services.sessions() as s:
        return [_prompt_out(p) for p in s.scalars(stmt)]


@router.post("/admin/prompts", response_model=PromptVersionOut, status_code=201)
def create_prompt(req: PromptCreate, request: Request,
                  admin: User = Depends(require_admin)) -> PromptVersionOut:
    """Store a new, INACTIVE version; it needs a passing replay before it can go live."""
    with request.app.state.services.sessions() as s:
        try:
            version = prompt_registry.create_version(s, req.agent, req.template, admin.user_id)
        except prompt_registry.PromptRegistryError as exc:
            raise HTTPException(422, str(exc)) from exc
        return _prompt_out(version)


@router.post("/admin/prompts/{version_id}/activate", response_model=PromptActivationOut)
def activate_prompt(version_id: int, request: Request,
                    admin: User = Depends(require_admin)) -> PromptActivationOut:
    """Activate a version with a passing replay report, or roll back to one that was
    active before. Everything else is refused (409)."""
    with request.app.state.services.sessions() as s:
        try:
            version, action = prompt_registry.activate(s, version_id, admin.user_id)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except prompt_registry.PromptRegistryError as exc:
            raise HTTPException(409, str(exc)) from exc
        s.commit()
        return PromptActivationOut(action=action, version=_prompt_out(version))


# --------------------------------------------------------------------------- sources (M16c)

@router.get("/sources", response_model=list[SourceOut])
def list_sources(request: Request, admin: User = Depends(require_admin)) -> list[SourceOut]:
    """Every registered source (the console's source list)."""
    with request.app.state.services.sessions() as s:
        return [_source_out(src) for src in s.scalars(select(Source).order_by(Source.id))]


def _set_source_status(request: Request, source_id: str, status: str, admin: User) -> SourceOut:
    with request.app.state.services.sessions() as s:
        source = s.get(Source, source_id)
        if source is None:
            raise HTTPException(404, f"There is no source {source_id!r}.")
        if source.status != status:
            source.status = status
            s.flush()
            write_audit_log(s, admin.user_id, f"source_{status}", source_id)
        return _source_out(source)


@router.post("/sources/{source_id}/pause", response_model=SourceOut)
def pause_source(source_id: str, request: Request, admin: User = Depends(require_admin)) -> SourceOut:
    """Stop taking changes from a source: its connector is not polled and its change events
    are skipped (a resume and the nightly reconciliation catch up)."""
    return _set_source_status(request, source_id, "paused", admin)


@router.post("/sources/{source_id}/resume", response_model=SourceOut)
def resume_source(source_id: str, request: Request, admin: User = Depends(require_admin)) -> SourceOut:
    """Take changes from a paused source again."""
    return _set_source_status(request, source_id, "active", admin)


# ---------------------------------------------------------------- routing policy versions (M16c)

@router.get("/admin/routing-policies", response_model=list[RoutingPolicyOut])
def list_routing_policies(request: Request,
                          admin: User = Depends(require_admin)) -> list[RoutingPolicyOut]:
    """Every routing-policy version, newest first (activate or roll back with PUT /routing-policy)."""
    with request.app.state.services.sessions() as s:
        return [_policy_out(p) for p in
                s.scalars(select(RoutingPolicy).order_by(RoutingPolicy.version.desc()))]


# ------------------------------------------------------------------- recent questions (M16c)

@router.get("/admin/queries", response_model=list[AdminQueryItem])
def recent_queries(request: Request, limit: int = Q(50, ge=1, le=500),
                   admin: User = Depends(require_admin)) -> list[AdminQueryItem]:
    """Recent questions with their outcome, to open a trace. The question text is shown to
    partners only (the same rule as GET /traces)."""
    show = admin.role == "partner"
    with request.app.state.services.sessions() as s:
        rows = s.execute(select(Query, Answer.status).outerjoin(Answer, Answer.query_id == Query.id)
                         .order_by(Query.id.desc()).limit(limit)).all()
        return [AdminQueryItem(query_id=q.id, user_id=q.user_id, asked_at=q.created_at,
                               intent=q.intent, status=status,
                               question=q.text if show else None, question_hidden=not show)
                for q, status in rows]


# ------------------------------------------------------------- classification policies (M16c)

def _rule_out(p: ClassificationPolicy) -> ClassificationPolicyOut:
    return ClassificationPolicyOut(id=p.id, match=p.match, pattern=p.pattern,
                                   classification=p.classification, active=p.active,
                                   created_by=p.created_by, created_at=p.created_at,
                                   deactivated_at=p.deactivated_at)


def _reclassify(request: Request, actor: str) -> list[str]:
    indexer = request.app.state.services.indexer
    if indexer is None:
        raise HTTPException(503, "Re-indexing is not available in this process.")
    return indexer.reclassify(actor=actor)


@router.get("/admin/classification-policies", response_model=list[ClassificationPolicyOut])
def list_classification_policies(request: Request,
                                 admin: User = Depends(require_admin)) -> list[ClassificationPolicyOut]:
    """Every classification rule, active or switched off."""
    with request.app.state.services.sessions() as s:
        return [_rule_out(p) for p in
                s.scalars(select(ClassificationPolicy).order_by(ClassificationPolicy.id))]


@router.post("/admin/classification-policies", response_model=ClassificationChangeOut,
             status_code=201)
def create_classification_policy(req: ClassificationPolicyCreate, request: Request,
                                  admin: User = Depends(require_admin)) -> ClassificationChangeOut:
    """Add a rule ("documents of this source type / path are at least X") and apply it to
    the documents it matches at once. Rules only make documents stricter."""
    from app.ingest.classification import rule_problems

    problems = rule_problems(req.match, req.pattern.strip(), req.classification)
    if problems:
        raise HTTPException(422, " ".join(problems))
    with request.app.state.services.sessions() as s:
        rule = ClassificationPolicy(match=req.match, pattern=req.pattern.strip(),
                                    classification=req.classification, active=True,
                                    created_by=admin.user_id, created_at=_now())
        s.add(rule)
        s.flush()
        write_audit_log(s, admin.user_id, "classification_policy_created",
                        f"#{rule.id}: {req.match} {req.pattern.strip()} -> {req.classification}")
        out = _rule_out(rule)
    return ClassificationChangeOut(policy=out, reclassified=_reclassify(request, admin.user_id))


@router.post("/admin/classification-policies/{policy_id}/deactivate",
             response_model=ClassificationChangeOut)
def deactivate_classification_policy(policy_id: int, request: Request,
                                      admin: User = Depends(require_admin)) -> ClassificationChangeOut:
    """Switch a rule off; the documents it raised return to their base classification
    (unless another active rule still applies)."""
    with request.app.state.services.sessions() as s:
        rule = s.get(ClassificationPolicy, policy_id)
        if rule is None:
            raise HTTPException(404, f"There is no classification policy {policy_id}.")
        if rule.active:
            rule.active, rule.deactivated_at = False, _now()
            s.flush()
            write_audit_log(s, admin.user_id, "classification_policy_deactivated", f"#{policy_id}")
        out = _rule_out(rule)
    return ClassificationChangeOut(policy=out, reclassified=_reclassify(request, admin.user_id))


# -------------------------------------------------------------------- agent performance (M16c)

def _naive_utc(moment: datetime | None) -> datetime | None:
    if moment is None or moment.tzinfo is None:
        return moment
    return moment.astimezone(timezone.utc).replace(tzinfo=None)


@router.get("/admin/agents/performance", response_model=AgentPerformanceOut)
def agents_performance(request: Request, from_: datetime | None = Q(None, alias="from"),
                       to: datetime | None = None,
                       admin: User = Depends(require_admin)) -> dict:
    """Latency per agent (p50/p95), tokens, verification failure, degradation and cache-hit
    rates, and an ESTIMATED cost per answer, for questions asked in [from, to) (default:
    the last 7 days). From Prometheus when PROMETHEUS_URL is set, else from the stored
    traces (app/observability/performance.py); `source` says which."""
    from app.observability.performance import agent_performance

    end = _naive_utc(to) or _now()
    start = _naive_utc(from_) or end - timedelta(days=7)
    if start >= end:
        raise HTTPException(422, "'from' must be before 'to'.")
    settings = request.app.state.services.settings
    if settings.prometheus_url:  # M17a: the metrics backend, as on the dashboards
        from app.observability.performance import prometheus_performance

        try:
            return prometheus_performance(settings.prometheus_url, start, end)
        except Exception:  # noqa: BLE001 - fall back to the stored traces, and say so in the log
            log.warning("Prometheus at %s unreachable; performance from the stored traces",
                        settings.prometheus_url)
    with request.app.state.services.sessions() as s:
        return agent_performance(s, start, end, settings.confidence_threshold)
