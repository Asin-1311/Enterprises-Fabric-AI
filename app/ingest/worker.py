"""The ingestion worker: the ONLY consumer of change events (CLAUDE.md section 6).

    .venv\\Scripts\\python.exe -m app.ingest.worker      # full profile: runs until Ctrl+C

It reads `DocumentChanged` events from the topic `doc-changes` and applies each one to the
metadata store, the vector index and the keyword index together (Indexer.apply_events),
so the three stay consistent.

Delivery is at-least-once: events are marked done (committed) only AFTER they were
applied. If the worker stops half-way, the same events come again, and applying an event
twice is harmless because the indexer compares content hash and metadata first
(idempotent by doc_id + content).

- A malformed event is refused, written to audit_log and committed, so it cannot block
  the queue.
- A change that fails enrichment is refused the same way, by the indexer.
- Any other error (database down, embedding failure) is NOT committed: the batch is
  tried again.

In the lite profile the API runs this worker in a background thread on the in-process
event bus; in the full profile it runs as its own process on Kafka.
"""

import logging
import threading

from app.db.session import write_audit_log
from app.ingest.events import TOPIC, BadEvent, DocumentChanged
from app.ingest.indexer import Indexer
from app.ports import EventBus

log = logging.getLogger(__name__)
GROUP = "indexer"


class IngestWorker:
    def __init__(self, indexer: Indexer, bus: EventBus, topic: str = TOPIC, group: str = GROUP,
                 batch_size: int = 50) -> None:
        self.indexer, self.batch_size = indexer, batch_size
        self.subscription = bus.subscribe(topic, group)

    def drain(self, wait_seconds: float = 0.0) -> list[tuple[str, str]]:
        """Apply every event waiting now (at most one batch), then commit.

        Waits up to `wait_seconds` for the first event. Returns (event_id, outcome) per
        event; malformed events are reported as 'malformed'."""
        events: list[DocumentChanged] = []
        outcomes: list[tuple[str, str]] = []
        raw = self.subscription.poll(wait_seconds)
        while raw is not None:
            try:
                events.append(DocumentChanged.from_dict(raw))
            except BadEvent as exc:
                log.warning("Malformed change event refused: %s", exc)
                with self.indexer.sessions() as s:
                    write_audit_log(s, "worker", "event_malformed", str(exc)[:500])
                outcomes.append((str(raw.get("event_id", "?")) if isinstance(raw, dict) else "?",
                                 "malformed"))
            if len(events) + len(outcomes) >= self.batch_size:
                break
            raw = self.subscription.poll(0.0)
        if events:
            outcomes += self.indexer.apply_events(events)  # raises: nothing is committed
        if events or outcomes:
            self.subscription.commit()
        return outcomes

    def run(self, stop: threading.Event, poll_seconds: float = 1.0) -> None:
        """Keep draining until `stop` is set. Errors are logged and the batch is retried
        after a pause, so one bad moment never stops ingestion for good."""
        while not stop.is_set():
            try:
                done = self.drain(poll_seconds)
                if done:
                    log.info("Applied %d change event(s): %s", len(done),
                             ", ".join(sorted({o for _, o in done})))
            except Exception:  # noqa: BLE001 - keep the worker alive; events will come again
                log.exception("Applying change events failed; retrying in 5 seconds")
                stop.wait(5)

    def close(self) -> None:
        self.subscription.close()


def start_in_background(worker: IngestWorker) -> tuple[threading.Thread, threading.Event]:
    """Run `worker` in a daemon thread (the lite API does this). Returns (thread, stop)."""
    stop = threading.Event()
    thread = threading.Thread(target=worker.run, args=(stop,), name="ingest-worker", daemon=True)
    thread.start()
    return thread, stop


def main() -> int:
    """Full profile: consume change events from Kafka until interrupted."""
    from app.adapters import open_event_bus
    from app.config import get_settings
    from app.ingest.indexer import build_indexer

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = get_settings()
    worker = IngestWorker(build_indexer(settings), open_event_bus(settings))
    stop = threading.Event()
    log.info("Ingestion worker started (profile %s, topic %s).", settings.profile, TOPIC)
    try:
        worker.run(stop)
    except KeyboardInterrupt:
        stop.set()
    finally:
        worker.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
