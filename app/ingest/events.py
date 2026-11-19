"""Change events: every source change becomes one `DocumentChanged` event (CLAUDE.md
section 6). Published on the event bus topic `doc-changes` (Kafka in the full profile, an
in-process queue in lite) and consumed by exactly one component: app/ingest/worker.py.

    {event_id, source_id, doc_id, uri, op: upsert|delete, content_hash, metadata, occurred_at}

Events are keyed by doc_id, so all changes to one document stay in order.
"""

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from app.ingest.enrich import manifest_metadata, manifest_source_id
from app.ingest.manifest import ManifestRow
from app.ports import EventBus

TOPIC = "doc-changes"


class BadEvent(ValueError):
    """An event that does not have the required shape (it is refused and logged)."""


@dataclass(frozen=True)
class DocumentChanged:
    source_id: str
    doc_id: str
    uri: str
    op: Literal["upsert", "delete"]
    content_hash: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    occurred_at: str = field(  # ISO 8601, UTC
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DocumentChanged":
        """Parse and check an event read from the bus."""
        if not isinstance(data, dict):
            raise BadEvent("an event must be a JSON object")
        missing = [k for k in ("event_id", "source_id", "doc_id", "uri", "op", "occurred_at")
                   if not data.get(k)]
        if missing:
            raise BadEvent(f"event is missing {', '.join(missing)}")
        if data["op"] not in ("upsert", "delete"):
            raise BadEvent(f"unknown op '{data['op']}'")
        if data["op"] == "upsert" and not data.get("content_hash"):
            raise BadEvent("an upsert needs a content_hash")
        if not isinstance(data.get("metadata") or {}, dict):
            raise BadEvent("metadata must be an object")
        try:
            datetime.fromisoformat(data["occurred_at"])
        except (TypeError, ValueError):
            raise BadEvent("occurred_at must be an ISO date-time") from None
        return cls(source_id=data["source_id"], doc_id=data["doc_id"], uri=data["uri"],
                   op=data["op"], content_hash=data.get("content_hash"),
                   metadata=data.get("metadata") or {}, event_id=data["event_id"],
                   occurred_at=data["occurred_at"])

    @property
    def occurred(self) -> datetime:
        """occurred_at as naive UTC (how the database stores times)."""
        moment = datetime.fromisoformat(self.occurred_at)
        if moment.tzinfo is not None:
            moment = moment.astimezone(timezone.utc).replace(tzinfo=None)
        return moment


def manifest_upsert(row: ManifestRow, content_hash: str) -> DocumentChanged:
    """The event for a corpus file listed in the manifest (filesystem source)."""
    return DocumentChanged(source_id=manifest_source_id(row), doc_id=row.doc_id, uri=row.path,
                           op="upsert", content_hash=content_hash,
                           metadata=manifest_metadata(row))


def deletion(source_id: str, doc_id: str, uri: str) -> DocumentChanged:
    return DocumentChanged(source_id=source_id, doc_id=doc_id, uri=uri, op="delete")


def publish(bus: EventBus, event: DocumentChanged) -> None:
    bus.publish(TOPIC, event.doc_id, event.to_dict())


def relative_uri(path: Path, project_root: Path) -> str:
    return path.resolve().relative_to(project_root.resolve()).as_posix()
