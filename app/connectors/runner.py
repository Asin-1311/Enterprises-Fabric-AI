"""The connector runner: polls every active polling source and publishes its changes
(CLAUDE.md section 6; M12c).

Every `connector_poll_seconds` (default 30), for each source of type git, s3, confluence,
jira or sharepoint whose status is `active`:
1. build its connector and call `poll(previous state)`;
2. publish each change event on the event bus (the ingestion worker applies them);
3. only then save the new state on the source (`sources.sync_state`), with the poll time.
If publishing fails, the state is not saved, so the same changes are found and sent again
next time (at-least-once; the worker ignores repeats). If a source cannot be reached, the
error is logged and stored as `last_error`; its previous state is kept and the other
sources are still polled. A paused source is not polled at all.

Where it runs: a background thread of the API in lite (API_RUN_CONNECTORS), its own
process in the full profile:
    .venv\\Scripts\\python.exe -m app.connectors.runner
In the full profile that process also runs one change data capture bridge per active
`dms` source (app/connectors/dms.py), which needs Kafka and Debezium (M12d).
"""

import logging
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import PROJECT_ROOT, Settings
from app.connectors import POLLING_TYPES, build_connector
from app.connectors.base import ConnectorError, SnapshotConnector
from app.db.models import Source
from app.ingest.events import TOPIC
from app.ports import EventBus

log = logging.getLogger(__name__)

ConnectorFactory = Callable[[Source], SnapshotConnector]


class ConnectorRunner:
    def __init__(self, sessions: sessionmaker[Session], bus: EventBus, settings: Settings,
                 factory: ConnectorFactory | None = None, topic: str = TOPIC) -> None:
        self.sessions, self.bus, self.settings, self.topic = sessions, bus, settings, topic
        self.factory = factory or self._build

    def _build(self, source: Source) -> SnapshotConnector:
        return build_connector(source.id, source.type, source.connector_config or {},
                               landing_dir=self.settings.landing_dir,
                               work_dir=self.settings.connectors_dir, project_root=PROJECT_ROOT)

    def poll_once(self) -> dict[str, int | None]:
        """Poll every active polling source once. Returns {source id: events published,
        or None if the source failed}."""
        with self.sessions() as s:
            ids = list(s.scalars(select(Source.id).where(Source.type.in_(POLLING_TYPES),
                                                        Source.status == "active")
                                 .order_by(Source.id)))
        return {source_id: self.poll_source(source_id) for source_id in ids}

    def _save(self, source_id: str, state: dict[str, Any]) -> None:
        with self.sessions() as s:
            source = s.get(Source, source_id)
            if source is not None:
                source.sync_state = state
                s.commit()

    def poll_source(self, source_id: str) -> int | None:
        # Read the source, then close the session: no database lock is held while the
        # connector talks to the network.
        with self.sessions() as s:
            source = s.get(Source, source_id)
            if source is None or source.status != "active":
                return 0
            s.expunge(source)
        state: dict[str, Any] = dict(source.sync_state or {})
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        connector: SnapshotConnector | None = None
        try:
            connector = self.factory(source)
            events, new_state = connector.poll(state)
            for event in events:
                self.bus.publish(self.topic, event.doc_id, event.to_dict())
        except Exception as exc:  # one broken source must not stop the others
            message = str(exc) if isinstance(exc, ConnectorError) else type(exc).__name__
            log.warning("Connector %s failed: %s", source_id, message,
                        exc_info=not isinstance(exc, ConnectorError))
            self._save(source_id, {**state, "last_poll_at": now, "last_error": message[:500]})
            return None
        finally:
            if connector is not None:
                connector.close()
        self._save(source_id, {**new_state, "last_poll_at": now, "last_error": None,
                               "last_changes": len(events)})
        if events:
            log.info("Connector %s: %d change(s) published", source_id, len(events))
        return len(events)

    def run(self, stop: threading.Event) -> None:
        """Poll now, then every `connector_poll_seconds`, until `stop` is set."""
        while True:
            try:
                self.poll_once()
            except Exception:  # e.g. the database is restarting: try again next round
                log.exception("Connector poll round failed")
            if stop.wait(self.settings.connector_poll_seconds):
                return


def start_in_background(runner: ConnectorRunner) -> tuple[threading.Thread, threading.Event]:
    """Run the poller in a daemon thread (the lite API does this). Returns (thread, stop)."""
    stop = threading.Event()
    thread = threading.Thread(target=runner.run, args=(stop,), name="connector-runner", daemon=True)
    thread.start()
    return thread, stop


def main() -> int:
    """Full profile: poll the sources and publish to Kafka until interrupted."""
    from app.adapters import open_event_bus, open_metadata_store
    from app.config import get_settings

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = get_settings()
    metadata = open_metadata_store(settings)
    metadata.initialise()
    runner = ConnectorRunner(metadata.sessions, open_event_bus(settings), settings)
    stop = threading.Event()
    bridges = start_cdc_bridges(metadata.sessions, settings, stop) if settings.profile == "full" else []
    log.info("Connector runner started (profile %s, every %.0f s; %d DMS bridge(s)).",
             settings.profile, settings.connector_poll_seconds, len(bridges))
    try:
        runner.run(stop)
    except KeyboardInterrupt:
        stop.set()
    finally:
        for bridge in bridges:
            bridge.close()
        runner.bus.close()
    return 0


def start_cdc_bridges(sessions: sessionmaker[Session], settings: Settings,
                      stop: threading.Event) -> list:
    """One change data capture bridge thread per active `dms` source (full profile)."""
    from app.adapters import open_event_bus
    from app.connectors.dms import CdcBridge

    with sessions() as s:
        sources = [(src.id, dict(src.connector_config or {})) for src in s.scalars(
            select(Source).where(Source.type == "dms", Source.status == "active"))]
    bridges = []
    for source_id, config in sources:
        bridge = CdcBridge(source_id, config, open_event_bus(settings), settings.landing_dir)
        threading.Thread(target=bridge.run, args=(stop,), name=f"cdc-{source_id}",
                         daemon=True).start()
        bridges.append(bridge)
    return bridges


if __name__ == "__main__":
    raise SystemExit(main())
