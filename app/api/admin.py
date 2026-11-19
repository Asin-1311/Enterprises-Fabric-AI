"""Administration endpoints (M11, CLAUDE.md section 10). Routes under /api/v1.

Only users with the admin flag (`users.is_admin`) may call them. The flag is a capability
for managing the system: it gives NO extra document access (access still comes from role
and engagement team, enforced in retrieval).

- POST /sources                registers a source; connector settings are checked and any
                               secret written into them is refused (use "env:NAME").
- GET  /sources/{id}/status    ingestion status, last change, counts, freshness lag.
- PUT  /routing-policy         activates a routing-policy version, only with a passing replay
                               report (or a version that was active before: a rollback).
Every change is written to audit_log. (POST /documents/reindex is in main.py.)
"""

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select

from app.api.auth import current_user
from app.api.schemas import (
    RoutingPolicyActivate,
    RoutingPolicyOut,
    SourceCreate,
    SourceOut,
    SourceStatusOut,
)
from app.db.models import Chunk, Document, RoutingPolicy, Source, User
from app.db.session import write_audit_log

router = APIRouter(prefix="/api/v1", tags=["admin"])

ADMIN_ONLY = "This action is restricted to administrators."

# Settings each connector type needs (the connectors themselves arrive in M12).
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
    """Register a source of documents. Its connector starts ingesting in M12."""
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
