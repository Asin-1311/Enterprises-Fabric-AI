"""Folder watcher: keeps the indexes up to date as files change (CLAUDE.md section 6).

- A created or modified .pdf/.md under data/corpus/ is re-indexed (only that document).
- A deleted file has its chunks removed from SQLite, Qdrant and BM25.
- A moved/renamed file is treated as delete (old path) + change (new path).
- A change to data/manifest.csv triggers a full sync (unchanged files are skipped by
  content hash, so this is cheap).

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
from app.ingest.indexer import Indexer, ManifestError, build_indexer

log = logging.getLogger(__name__)
WATCHED_SUFFIXES = (".pdf", ".md")


class DebouncedCorpusHandler(FileSystemEventHandler):
    """Collects file events and hands them to the indexer after a quiet period."""

    def __init__(self, indexer: Indexer, corpus_dir: Path, manifest_path: Path,
                 debounce_seconds: float = 1.5) -> None:
        self.indexer = indexer
        self.corpus_dir = corpus_dir.resolve()
        self.manifest_path = manifest_path.resolve()
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
            if path == self.manifest_path:
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
            if manifest_changed:
                report = self.indexer.sync(actor="watcher")
                log.info("Manifest changed: indexed=%s removed=%s",
                         report.indexed, report.removed)
                return  # a full sync also covers any file events in the same batch
            for path, kind in sorted(pending.items()):
                if kind == "delete" or not path.exists():
                    doc_id = self.indexer.remove_path(path, actor="watcher")
                    log.info("Removed %s (%s)", path.name, doc_id)
                else:
                    doc_id = self.indexer.index_path(path, actor="watcher")
                    log.info("Indexed %s (%s)", path.name, doc_id)
        except ManifestError as exc:
            log.error("Manifest invalid, sync refused: %s", exc.errors)
        except Exception:  # keep the watcher alive; the next event will retry
            log.exception("Indexing failed")


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
