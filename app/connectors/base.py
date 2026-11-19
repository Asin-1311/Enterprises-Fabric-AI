"""What every polling connector shares (CLAUDE.md section 6; Gap Register R-7.1-c).

A polling connector only has to answer two questions about its system:
- `list_items()`: which documents exist now, each with a version token that changes
  whenever its content changes (a Git blob hash, an S3 ETag, a Confluence version, ...);
- `fetch(item)`: the document's text, as Markdown.

`SnapshotConnector.poll` does the rest, the same way for every system:
1. compare the listing with the snapshot saved after the previous poll;
2. for a new or changed item: fetch it, write it to the landing folder
   (data/landing/<source>/<doc_id>.md) and emit an upsert event;
3. for an item that disappeared: emit a delete event;
4. return the new snapshot, which the runner saves only after the events are published,
   so a crash in between sends the changes again (at-least-once), never loses them.

Metadata. Each source's connector_config has `defaults` (source_type, access_level and
optionally engagement_id, effective_date, owner, classification). Items may override them
with labels: `access-manager`, `access-partner`, `engagement-<ID>`, `type-<source_type>`,
`classification-<value>`. Enrichment (app/ingest/enrich.py) then checks every event the
same way as a manifest row or a webhook push; anything inconsistent is refused, not guessed.
"""

import hashlib
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import env_reference
from app.ingest.events import DocumentChanged, deletion
from app.ingest.manifest import CLASSIFICATIONS, SOURCE_TYPES
from app.ingest.md_loader import SYNTHETIC_BANNER

log = logging.getLogger(__name__)

ACCESS_LEVELS = ("staff", "manager", "partner")
MAX_DOC_ID = 100


class ConnectorError(RuntimeError):
    """A source system could not be read (logged by the runner; the source is retried)."""


@dataclass(frozen=True)
class RemoteItem:
    """One document as the source system lists it."""

    key: str                        # stable id in that system: a path, a page id, an issue key
    version: str                    # changes whenever the content changes
    title: str | None = None
    modified: datetime | None = None
    labels: tuple[str, ...] = ()
    extra: dict[str, Any] | None = None  # whatever fetch() needs (a download URL, ...)


def resolve_secret(ref: Any) -> str | None:
    """The value of an "env:NAME" reference, from the environment or .env (secrets are
    never stored in the database)."""
    return env_reference(ref)


def make_doc_id(source_id: str, key: str) -> str:
    """A stable, valid document id for an item: "<source>-<key with unsafe characters
    replaced>", or a hashed form if that would be too long."""
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", key).strip("-.") or "item"
    doc_id = f"{source_id}-{slug}"
    if len(doc_id) > MAX_DOC_ID:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]
        doc_id = f"{source_id}-{slug[: MAX_DOC_ID - len(source_id) - 14]}-{digest}"
    return doc_id


def first_heading(text: str) -> str | None:
    match = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
    return match.group(1).strip() if match else None


def banner_first(text: str) -> str:
    """Move the SYNTHETIC banner line to the top, where the Markdown loader expects it
    (a converted page may have it after its title). A banner is never added."""
    lines = text.splitlines()
    if SYNTHETIC_BANNER in (line.strip() for line in lines) and lines[0].strip() != SYNTHETIC_BANNER:
        rest = [line for line in lines if line.strip() != SYNTHETIC_BANNER]
        return "\n".join([SYNTHETIC_BANNER, "", *rest]).strip() + "\n"
    return text


def label_overrides(labels: tuple[str, ...]) -> dict[str, str]:
    """Metadata set by item labels (see the module docstring)."""
    out: dict[str, str] = {}
    for label in labels:
        name = label.strip()
        if name.startswith("access-") and name[7:] in ACCESS_LEVELS:
            out["access_level"] = name[7:]
        elif name.startswith("engagement-") and len(name) > 11:
            out["engagement_id"] = name[11:]
        elif name.startswith("type-") and name[5:] in SOURCE_TYPES:
            out["source_type"] = name[5:]
        elif name.startswith("classification-") and name[15:] in CLASSIFICATIONS:
            out["classification"] = name[15:]
    return out


class SnapshotConnector(ABC):
    """Base class of the polling connectors (git, s3, confluence, jira, sharepoint)."""

    #: File types the connector ingests; others are skipped and logged once per version.
    suffixes: tuple[str, ...] = (".md",)

    def __init__(self, source_id: str, config: dict[str, Any], landing_dir: Path) -> None:
        self.source_id, self.config, self.landing_dir = source_id, config or {}, landing_dir
        self.defaults: dict[str, Any] = dict(self.config.get("defaults") or {})

    @abstractmethod
    def list_items(self) -> list[RemoteItem]:
        """Every document currently in the source (raises ConnectorError if unreachable)."""

    @abstractmethod
    def fetch(self, item: RemoteItem) -> str | None:
        """The item's content as Markdown, or None if it is not a document we ingest."""

    # ------------------------------------------------------------------ shared

    def metadata(self, item: RemoteItem, text: str) -> dict[str, Any]:
        """Metadata for the change event: defaults, then label overrides, then the item."""
        when = item.modified or datetime.now(timezone.utc)
        meta: dict[str, Any] = {"synthetic": True, **self.defaults, **label_overrides(item.labels)}
        meta.setdefault("effective_date", when.date().isoformat())
        meta["title"] = item.title or first_heading(text) or item.key
        meta["version"] = item.version[:40]
        return meta

    def _landing(self, doc_id: str) -> tuple[Path, str]:
        return self.landing_dir / self.source_id / f"{doc_id}.md", f"landing:{self.source_id}/{doc_id}.md"

    def _write(self, path: Path, text: str) -> bytes:
        data = text.encode("utf-8")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)  # the worker never sees a half-written file
        return data

    def poll(self, state: dict[str, Any]) -> tuple[list[DocumentChanged], dict[str, Any]]:
        """See the module docstring. Unchanged items are not fetched again."""
        seen: dict[str, dict[str, str]] = dict((state or {}).get("items") or {})
        items = {i.key: i for i in self.list_items()}
        events: list[DocumentChanged] = []
        new: dict[str, dict[str, str]] = {}
        for key, item in items.items():
            before = seen.get(key)
            if before is not None and before.get("version") == item.version:
                new[key] = before
                continue
            doc_id = make_doc_id(self.source_id, key)
            text = self.fetch(item)
            if text is None:  # not a document type we ingest: remember it, skip it
                log.info("%s: %s skipped (unsupported type)", self.source_id, key)
                new[key] = {"version": item.version, "doc_id": "", "skipped": "1"}
                if before is not None and before.get("doc_id"):  # it was a document before
                    events.append(deletion(self.source_id, before["doc_id"],
                                           self._landing(before["doc_id"])[1]))
                continue
            path, uri = self._landing(doc_id)
            text = banner_first(text)
            data = self._write(path, text)
            events.append(DocumentChanged(
                source_id=self.source_id, doc_id=doc_id, uri=uri, op="upsert",
                content_hash=hashlib.sha256(data).hexdigest(), metadata=self.metadata(item, text)))
            new[key] = {"version": item.version, "doc_id": doc_id}
        for key in seen.keys() - items.keys():
            if seen[key].get("doc_id"):
                events.append(deletion(self.source_id, seen[key]["doc_id"],
                                       self._landing(seen[key]["doc_id"])[1]))
        return events, {"items": new}

    def close(self) -> None:
        """Release network clients (the SaaS connectors keep one in `self.api`)."""
        api = getattr(self, "api", None)
        if api is not None:
            api.close()
