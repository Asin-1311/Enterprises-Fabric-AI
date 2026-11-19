"""Webhook connector: POST /api/v1/webhooks/documents (CLAUDE.md sections 6 and 10).

An outside system pushes a document (or its deletion) to a registered source of type
"webhook". This endpoint has no user session: the caller proves who it is with an HMAC
signature over the exact request body, using the source's shared secret.

    X-Source-Id:      webhook-intranet
    X-Signature-256:  sha256=<hex of HMAC-SHA256(secret, raw body)>

- The secret is never stored in the database: the source's connector_config holds a
  reference such as "env:INTRANET_WEBHOOK_SECRET", and the value comes from .env.
- An unknown source, a non-webhook source and a wrong signature all get the same 401, so
  the endpoint does not reveal which sources exist.
- The content is written to the landing folder and a change event is published; the
  ingestion worker enriches and indexes it like any other change (HTTP 202 Accepted).
- A document id that already belongs to another source is refused (409).
"""

import hashlib
import hmac
from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import select

from app.config import env_reference
from app.db.models import Document, Source
from app.db.session import write_audit_log
from app.ingest.events import DocumentChanged, publish

router = APIRouter(prefix="/api/v1/webhooks", tags=["connectors"])

SIGNATURE_PREFIX = "sha256="
NOT_ACCEPTED = "Invalid source or signature."
MAX_CONTENT_CHARS = 1_000_000


class WebhookDocument(BaseModel):
    """The body: a document to add or replace, or one to delete."""

    model_config = ConfigDict(extra="forbid")
    doc_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")
    op: Literal["upsert", "delete"] = "upsert"
    title: str | None = None
    source_type: str | None = None
    access_level: str | None = None
    engagement_id: str | None = None
    effective_date: str | None = None
    version: str | None = None
    supersedes: str | None = None
    owner: str | None = None
    classification: str | None = None
    content: str | None = Field(default=None, max_length=MAX_CONTENT_CHARS)  # Markdown

    @model_validator(mode="after")
    def upsert_has_content(self) -> "WebhookDocument":
        if self.op == "upsert":
            missing = [f for f in ("title", "source_type", "access_level", "effective_date",
                                   "content") if not getattr(self, f)]
            if missing:
                raise ValueError(f"an upsert needs {', '.join(missing)}")
        return self


class WebhookAccepted(BaseModel):
    status: Literal["accepted"]
    event_id: str
    doc_id: str
    op: str


def signature(secret: str, body: bytes) -> str:
    """The value a sender puts in X-Signature-256."""
    return SIGNATURE_PREFIX + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _secret(source: Source) -> str | None:
    # From the environment or .env (M12c fix: before, only an exported variable was seen).
    return env_reference((source.connector_config or {}).get("secret"))


@router.post("/documents", response_model=WebhookAccepted, status_code=202)
async def receive_document(request: Request,
                           x_source_id: str = Header(default=""),
                           x_signature_256: str = Header(default="")) -> WebhookAccepted:
    """Accept a signed document change from a webhook source and queue it for indexing."""
    svc = request.app.state.services
    body = await request.body()
    actor = f"webhook:{x_source_id or '?'}"
    with svc.sessions() as s:
        source = s.get(Source, x_source_id) if x_source_id else None
        if source is None or source.type != "webhook":
            write_audit_log(s, actor, "webhook_rejected", "unknown source")
            raise HTTPException(401, NOT_ACCEPTED)
        secret = _secret(source)
        if not secret:
            write_audit_log(s, actor, "webhook_rejected", "secret not configured")
            raise HTTPException(503, "This webhook is not configured yet.")
        if not hmac.compare_digest(signature(secret, body), x_signature_256):
            write_audit_log(s, actor, "webhook_rejected", "bad signature")
            raise HTTPException(401, NOT_ACCEPTED)
        if source.status != "active":
            raise HTTPException(409, "This source is paused.")
        try:
            doc = WebhookDocument.model_validate_json(body)
        except ValidationError as exc:
            raise HTTPException(422, [e["msg"] for e in exc.errors()]) from None
        owner_source = s.scalar(select(Document.source_id).where(Document.doc_id == doc.doc_id))
        if owner_source not in (None, source.id):
            raise HTTPException(409, f"Document {doc.doc_id} belongs to another source.")
        if svc.bus is None:
            raise HTTPException(503, "Change events are not available.")

        landing = svc.settings.landing_dir / source.id / f"{doc.doc_id}.md"
        content_hash = None
        if doc.op == "upsert":
            landing.parent.mkdir(parents=True, exist_ok=True)
            data = doc.content.encode("utf-8")
            tmp = landing.with_suffix(".tmp")
            tmp.write_bytes(data)
            tmp.replace(landing)  # the worker never sees a half-written file
            content_hash = hashlib.sha256(data).hexdigest()
        metadata = doc.model_dump(exclude={"doc_id", "op", "content"}, exclude_none=True)
        event = DocumentChanged(source_id=source.id, doc_id=doc.doc_id,
                                uri=f"landing:{source.id}/{doc.doc_id}.md", op=doc.op,
                                content_hash=content_hash, metadata=metadata)
        publish(svc.bus, event)
        write_audit_log(s, actor, "webhook_received", f"{doc.op} {doc.doc_id}")
    return WebhookAccepted(status="accepted", event_id=event.event_id, doc_id=doc.doc_id, op=doc.op)
