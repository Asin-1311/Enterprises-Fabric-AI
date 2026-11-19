"""Folder watcher: turns file changes into change events (CLAUDE.md section 6).

Since M12a the watcher does not index anything itself: it PUBLISHES `DocumentChanged`
events, and the ingestion worker (app/ingest/worker.py) applies them.

- A created or modified .pdf/.md under data/corpus/ becomes an upsert event.
- A deleted file becomes a delete event.
- A moved/renamed file is treated as delete (old path) + change (new path).
- A change to data/manifest.csv becomes an upsert for every listed document (the worker
  skips unchanged ones by content hash) and a delete for documents no longer listed.

Given a shared event bus (the API, Kafka), it only publishes. Without one (the command
line below, and tests), it starts its own in-process bus and worker, so files are still
indexed as soon as they change.

Editors often fire several events for one save, so events are collected and processed
together once nothing has happened for `debounce_seconds`.

Run from the project root (Ctrl+C to stop):
    .venv\\Scripts\\python.exe -m app.ingest.watcher
"""

import logging
import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from app.config import get_settings
from app.ingest.events import publish
from app.ingest.indexer import Indexer, ManifestError, build_indexer
from app.ingest.worker import IngestWorker
from app.ports import EventBus

log = logging.getLogger(__name__)
WATCHED_SUFFIXES = (".pdf", ".md")


class DebouncedCorpusHandler(FileSystemEventHandler):
    """Collects file events and hands them to the indexer after a quiet period."""

    def __init__(self, indexer: Indexer, corpus_dir: Path, manifest_path: Path,
                 debounce_seconds: float = 1.5, bus: EventBus | None = None) -> None:
        self.indexer = indexer
        if bus is None:  # nobody else consumes: run our own worker on a private bus
            from app.adapters.lite.events import InProcessEventBus

            bus = InProcessEventBus()
            self.worker: IngestWorker | None = IngestWorker(indexer, bus)
        else:
            self.worker = None
        self.bus = bus
        self.corpus_dir = corpus_dir.resolve()
        self.manifest_path = manifest_path.resolve()
        # Either manifest may be the active one (the synthetic one in a clean clone).
        synthetic = getattr(indexer, "synthetic_manifest_path", None)
        self.manifest_paths = {self.manifest_path} | ({synthetic.resolve()} if synthetic else set())
        self.debounce_seconds = debounce_seconds
        self._pending: dict[Path, str] = {}  # path -> "change" | "delete"
        self._manifest_changed = False
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None

    # -- event intake -----------------------------------------------------------

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        if event.event_type == "moved":
            self._note(Path(str(event.src_path)), "delete")
            self._note(Path(str(event.dest_path)), "change")
        elif event.event_type == "deleted":
            self._note(Path(str(event.src_path)), "delete")
        elif event.event_type in ("created", "modified", "closed"):
            self._note(Path(str(event.src_path)), "change")

    def _note(self, path: Path, kind: str) -> None:
        path = path.resolve()
        with self._lock:
            if path in self.manifest_paths:
                self._manifest_changed = True
            elif path.suffix.lower() in WATCHED_SUFFIXES and self.corpus_dir in path.parents:
                self._pending[path] = kind
            else:
                return
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self.debounce_seconds, self.flush)
            self._timer.daemon = True
            self._timer.start()

    # -- processing ---------------------------------------------------------------

    def flush(self) -> None:
        """Process everything collected so far."""
        with self._lock:
            pending, self._pending = self._pending, {}
            manifest_changed, self._manifest_changed = self._manifest_changed, False
            self._timer = None
        try:
            if manifest_changed:  # covers any file events in the same batch as well
                events = self.indexer.manifest_change_events(actor="watcher")
            else:
                events = [e for path, kind in sorted(pending.items())
                          if (e := self.indexer.path_change_event(
                              path, deleted=kind == "delete" or not path.exists())) is not None]
            for event in events:
                publish(self.bus, event)
            log.info("Published %d change event(s)", len(events))
            if self.worker is not None:
                while self.worker.drain():
                    pass
        except ManifestError as exc:
            log.error("Manifest invalid, nothing published: %s", exc.errors)
        except Exception:  # keep the watcher alive; the next event will retry
            log.exception("Publishing or applying change events failed")


def start_observer(handler: DebouncedCorpusHandler) -> Observer:
    """Start watching the corpus folder (recursive) and the manifest's folder."""
    observer = Observer()
    observer.schedule(handler, str(handler.corpus_dir), recursive=True)
    observer.schedule(handler, str(handler.manifest_path.parent), recursive=False)
    observer.start()
    return observer


def main() -> int:
    """Initial full sync, then watch until Ctrl+C."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = get_settings()
    indexer = build_indexer(settings)
    report = indexer.sync(actor="watcher")
    log.info("Initial sync: indexed=%d unchanged=%d removed=%d",
             len(report.indexed), len(report.unchanged), len(report.removed))
    handler = DebouncedCorpusHandler(indexer, settings.corpus_dir, settings.manifest_path,
                                     settings.watcher_debounce_seconds)
    observer = start_observer(handler)
    log.info("Watching %s and %s", settings.corpus_dir, settings.manifest_path)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
