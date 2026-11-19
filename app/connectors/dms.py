"""DMS connector: change data capture from the synthetic document management system
(CLAUDE.md section 6; Gap Register R-3-stream, R-5-ing; M12d).

    PostgreSQL dms.documents --(Debezium, logical replication)--> Kafka "dms.dms.documents"
        --(CdcBridge)--> DocumentChanged on "doc-changes" --(worker)--> all indexes

Unlike the polling connectors, nothing is listed or compared: Debezium reads the
database's write-ahead log and sends one message per committed row change, within
seconds. This module turns each message into the usual change event:

- op "c" (insert), "u" (update), "r" (row read by the initial snapshot): the row's
  Markdown body is written to the landing folder and an upsert event is sent; the row's
  columns are the document's metadata (title, source_type, access_level, engagement_id,
  effective_date, version, owner, classification), with the source's `defaults` for
  anything missing. Enrichment then checks it like any other change.
- op "d" (delete): a delete event (the table has REPLICA IDENTITY FULL, so the deleted
  row's doc_id is in `before`).
- op "t" (truncate) is ignored and logged: documents are removed one row at a time.

connector_config: {"table": "dms.documents", "topic": "dms.dms.documents",
                   "defaults": {...}}

The bridge commits its Kafka position only after the events are published
(at-least-once); on an error it rewinds and tries again.
"""

import hashlib
import logging
import re
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.ingest.events import TOPIC, DocumentChanged, deletion
from app.ports import EventBus

log = logging.getLogger(__name__)

_DOC_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")
_TABLE = re.compile(r"^[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*$")
METADATA_COLUMNS = ("title", "source_type", "access_level", "engagement_id", "version",
                    "supersedes", "owner", "classification")


class SkippedChange(ValueError):
    """A change message that cannot become a document event (logged and skipped)."""


def debezium_topic(config: dict[str, Any]) -> str:
    """The Kafka topic Debezium writes the table's changes to: <prefix>.<schema>.<table>."""
    return str(config.get("topic") or f"dms.{config.get('table', 'dms.documents')}")


def check_dms_config(config: dict[str, Any]) -> list[str]:
    """Problems with a dms source's settings (used when it is registered)."""
    table = str(config.get("table", ""))
    return [] if _TABLE.match(table) else ["table must be <schema>.<table>, e.g. dms.documents"]


def _iso_date(value: Any) -> str | None:
    """Debezium sends a DATE as days since 1970-01-01 (time.precision.mode=connect)."""
    if value is None or value == "":
        return None
    if isinstance(value, int):
        return (date(1970, 1, 1) + timedelta(days=value)).isoformat()
    return str(value)[:10]


class DmsTranslator:
    def __init__(self, source_id: str, config: dict[str, Any], landing_dir: Path) -> None:
        self.source_id, self.landing_dir = source_id, landing_dir
        self.defaults: dict[str, Any] = dict((config or {}).get("defaults") or {})

    def _landing(self, doc_id: str) -> tuple[Path, str]:
        return (self.landing_dir / self.source_id / f"{doc_id}.md",
                f"landing:{self.source_id}/{doc_id}.md")

    def translate(self, message: dict[str, Any]) -> DocumentChanged:
        """One Debezium change message as a change event (raises SkippedChange)."""
        op = message.get("op")
        if op not in ("c", "u", "r", "d"):
            raise SkippedChange(f"operation '{op}' is not a row change")
        row = message.get("before") if op == "d" else message.get("after")
        if not isinstance(row, dict):
            raise SkippedChange(f"op '{op}' without the row")
        doc_id = str(row.get("doc_id") or "")
        if not _DOC_ID.match(doc_id):  # it becomes a file name: never trust it unchecked
            raise SkippedChange(f"invalid doc_id {doc_id!r}")
        when = message.get("ts_ms") or (message.get("source") or {}).get("ts_ms")
        occurred = (datetime.fromtimestamp(when / 1000, timezone.utc) if when
                    else datetime.now(timezone.utc)).isoformat(timespec="seconds")
        path, uri = self._landing(doc_id)
        if op == "d":
            return DocumentChanged(**{**deletion(self.source_id, doc_id, uri).to_dict(),
                                      "occurred_at": occurred})
        data = str(row.get("body") or "").encode("utf-8")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)  # the worker never sees a half-written file
        metadata: dict[str, Any] = {"synthetic": True, **self.defaults}
        metadata |= {k: row[k] for k in METADATA_COLUMNS if row.get(k) not in (None, "")}
        metadata["effective_date"] = _iso_date(row.get("effective_date")) or metadata.get("effective_date")
        return DocumentChanged(source_id=self.source_id, doc_id=doc_id, uri=uri, op="upsert",
                               content_hash=hashlib.sha256(data).hexdigest(), metadata=metadata,
                               occurred_at=occurred)


class CdcBridge:
    """Reads one dms source's Debezium topic and publishes change events (full profile)."""

    def __init__(self, source_id: str, config: dict[str, Any], bus: EventBus, landing_dir: Path,
                 out_topic: str = TOPIC, group: str | None = None, batch_size: int = 100) -> None:
        self.translator = DmsTranslator(source_id, config, landing_dir)
        self.bus, self.out_topic, self.batch_size = bus, out_topic, batch_size
        self.subscription = bus.subscribe(debezium_topic(config), group or f"cdc-{source_id}")
        self.skipped = 0

    def drain(self, wait_seconds: float = 0.0) -> int:
        """Translate and publish every change waiting now; returns events published."""
        published, seen = 0, 0
        message = self.subscription.poll(wait_seconds)
        try:
            while message is not None:
                seen += 1
                try:
                    event = self.translator.translate(message)
                    self.bus.publish(self.out_topic, event.doc_id, event.to_dict())
                    published += 1
                except SkippedChange as exc:
                    self.skipped += 1
                    log.warning("DMS change skipped: %s", exc)
                if seen >= self.batch_size:
                    break
                message = self.subscription.poll(0.0)
        except Exception:
            self.subscription.rewind()  # nothing is lost: the same changes come again
            raise
        if seen:
            self.subscription.commit()
        return published

    def run(self, stop: threading.Event, poll_seconds: float = 1.0) -> None:
        while not stop.is_set():
            try:
                self.drain(poll_seconds)
            except Exception:  # noqa: BLE001 - e.g. Kafka restarting: retry
                log.exception("DMS change data capture failed; retrying in 5 seconds")
                stop.wait(5)

    def close(self) -> None:
        self.subscription.close()
