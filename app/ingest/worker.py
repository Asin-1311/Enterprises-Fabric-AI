"""The ingestion worker: the ONLY consumer of change events (CLAUDE.md section 6).

    .venv\\Scripts\\python.exe -m app.ingest.worker                    # full profile: until Ctrl+C
    .venv\\Scripts\\python.exe -m app.ingest.worker --replay-dead-letters

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
- Any other error (database down, embedding failure) is NOT committed. The subscription
  is rewound to the last commit and the batch is tried again after a growing pause
  (5, 10, 20, 40 s ...). (Before M12d the subscription was not rewound, so a failed batch
  was skipped and lost once a later batch committed; found while building dead letters.)
- Dead letters (M12d): after `max_attempts` failures the batch is applied one event at a
  time. Events that still fail alone are published to the topic `doc-changes-dead` with
  the error, written to audit_log (`event_dead_lettered`) and committed, so one broken
  document can never hold up the queue. Once the cause is fixed, `--replay-dead-letters`
  publishes them again. If the audit log itself cannot be written (the database is
  down), nothing is dead-lettered: the batch keeps being retried.

In the lite profile the API runs this worker in a background thread on the in-process
event bus; in the full profile it runs as its own process on Kafka.
"""

import argparse
import logging
import threading
from datetime import datetime, timezone

from app.db.session import write_audit_log
from app.ingest.events import TOPIC, BadEvent, DocumentChanged
from app.ingest.indexer import Indexer
from app.ports import EventBus

log = logging.getLogger(__name__)
GROUP = "indexer"
DEAD_TOPIC = "doc-changes-dead"
MAX_RETRY_PAUSE_SECONDS = 60.0


class IngestWorker:
    def __init__(self, indexer: Indexer, bus: EventBus, topic: str = TOPIC, group: str = GROUP,
                 batch_size: int = 50, max_attempts: int = 5,
                 dead_topic: str = DEAD_TOPIC) -> None:
        self.indexer, self.bus, self.batch_size = indexer, bus, batch_size
        self.max_attempts, self.dead_topic = max_attempts, dead_topic
        self.subscription = bus.subscribe(topic, group)
        self.attempts = 0  # failed attempts of the batch now at the head of the queue

    def drain(self, wait_seconds: float = 0.0) -> list[tuple[str, str]]:
        """Apply every event waiting now (at most one batch), then commit.

        Waits up to `wait_seconds` for the first event. Returns (event_id, outcome) per
        event; malformed events are reported as 'malformed', dead-lettered ones as
        'dead_lettered'. Raises (after rewinding) if the batch failed and will be retried."""
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
            try:
                outcomes += self.indexer.apply_events(events)
            except Exception:
                self.attempts += 1
                if self.attempts < self.max_attempts:
                    self.subscription.rewind()  # the same events come again next time
                    raise
                try:
                    outcomes += self._one_by_one(events)
                except Exception:
                    self.subscription.rewind()
                    raise
        if events or outcomes:
            self.subscription.commit()
        self.attempts = 0
        return outcomes

    def _one_by_one(self, events: list[DocumentChanged]) -> list[tuple[str, str]]:
        """The batch failed `max_attempts` times: apply each event alone and dead-letter
        the ones that still fail."""
        outcomes: list[tuple[str, str]] = []
        for event in events:
            try:
                outcomes += self.indexer.apply_events([event])
            except Exception as exc:  # noqa: BLE001 - recorded with the event
                self._dead_letter(event, exc)
                outcomes.append((event.event_id, "dead_lettered"))
        return outcomes

    def _dead_letter(self, event: DocumentChanged, exc: Exception) -> None:
        error = f"{type(exc).__name__}: {exc}"[:500]
        # Audit first: if the database is down this raises, and nothing is dead-lettered.
        with self.indexer.sessions() as s:
            write_audit_log(s, "worker", "event_dead_lettered",
                            f"{event.doc_id} ({event.event_id}): {error}"[:500])
        self.bus.publish(self.dead_topic, event.doc_id, {
            "event": event.to_dict(), "error": error, "attempts": self.attempts,
            "dead_lettered_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
        log.error("Change event for %s dead-lettered after %d attempts: %s",
                  event.doc_id, self.attempts, error)

    def retry_pause(self) -> float:
        """Seconds to wait before retrying a failed batch: 5, 10, 20, ... up to 60."""
        return min(5.0 * 2 ** max(self.attempts - 1, 0), MAX_RETRY_PAUSE_SECONDS)

    def run(self, stop: threading.Event, poll_seconds: float = 1.0) -> None:
        """Keep draining until `stop` is set. A failed batch is retried after a growing
        pause, so one bad moment never stops ingestion for good."""
        while not stop.is_set():
            try:
                done = self.drain(poll_seconds)
                if done:
                    log.info("Applied %d change event(s): %s", len(done),
                             ", ".join(sorted({o for _, o in done})))
            except Exception:  # noqa: BLE001 - keep the worker alive; events come again
                pause = self.retry_pause()
                log.exception("Applying change events failed (attempt %d of %d); retrying in "
                              "%.0f seconds", self.attempts, self.max_attempts, pause)
                stop.wait(pause)

    def close(self) -> None:
        self.subscription.close()


def replay_dead_letters(bus: EventBus, group: str = "dead-letter-replay",
                        wait_seconds: float = 5.0) -> int:
    """Publish every dead-lettered event again on `doc-changes` (after the cause was
    fixed). Each one is replayed once per group. Returns how many were replayed."""
    subscription = bus.subscribe(DEAD_TOPIC, group)
    count = 0
    try:
        letter = subscription.poll(wait_seconds)
        while letter is not None:
            event = DocumentChanged.from_dict(letter["event"])
            bus.publish(TOPIC, event.doc_id, event.to_dict())
            count += 1
            letter = subscription.poll(1.0)
        subscription.commit()
    finally:
        subscription.close()
    return count


def start_in_background(worker: IngestWorker) -> tuple[threading.Thread, threading.Event]:
    """Run `worker` in a daemon thread (the lite API does this). Returns (thread, stop)."""
    stop = threading.Event()
    thread = threading.Thread(target=worker.run, args=(stop,), name="ingest-worker", daemon=True)
    thread.start()
    return thread, stop


def main(argv: list[str] | None = None) -> int:
    """Full profile: consume change events from Kafka until interrupted."""
    from app.adapters import open_event_bus
    from app.config import get_settings
    from app.ingest.indexer import build_indexer

    parser = argparse.ArgumentParser(description="The ingestion worker.")
    parser.add_argument("--replay-dead-letters", action="store_true",
                        help="publish dead-lettered events again, then exit")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = get_settings()
    if args.replay_dead_letters:
        bus = open_event_bus(settings)
        print(f"Replayed {replay_dead_letters(bus)} dead-lettered event(s).")
        bus.close()
        return 0
    worker = IngestWorker(build_indexer(settings), open_event_bus(settings),
                          max_attempts=settings.worker_max_attempts)
    if settings.worker_metrics_port:  # M17a: ingestion lag and events for Prometheus
        from prometheus_client import start_http_server

        start_http_server(settings.worker_metrics_port)
        log.info("Worker metrics on port %s (/metrics).", settings.worker_metrics_port)
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
