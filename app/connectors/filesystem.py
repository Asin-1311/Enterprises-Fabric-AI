"""Filesystem connector: one corpus folder under data/corpus/ (CLAUDE.md section 6).

The folder watcher (app/ingest/watcher.py) already turns every file change into an event
the moment it happens. This connector is the same source seen through the Connector port:
`poll` compares the manifest with what is indexed and returns the events that bring this
folder's documents in line (unchanged files are skipped by the worker by content hash).
It is what a scheduled reconciliation calls (M12d); the runner does not poll it, because
the watcher is faster and cheaper.
"""

from typing import Any

from app.ingest.events import DocumentChanged
from app.ingest.indexer import Indexer


class FilesystemConnector:
    def __init__(self, source_id: str, indexer: Indexer) -> None:
        self.source_id, self.indexer = source_id, indexer

    def poll(self, state: dict[str, Any]) -> tuple[list[DocumentChanged], dict[str, Any]]:
        events = [e for e in self.indexer.manifest_change_events(actor=f"connector:{self.source_id}")
                  if e.source_id == self.source_id]
        return events, dict(state or {})
