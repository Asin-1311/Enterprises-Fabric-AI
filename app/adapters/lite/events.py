"""EventBus in process memory (lite profile).

Behaves like a tiny Kafka: each topic is an append-only list of events; each consumer
group remembers how far it has committed. A subscriber that polls but does not commit
gets the same events again after re-subscribing (at-least-once delivery), which is what
the ingestion worker relies on. Events are lost when the process stops.
"""

import copy
import threading
from collections import defaultdict
from typing import Any


class InProcessEventBus:
    def __init__(self) -> None:
        self._topics: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
        self._committed: dict[tuple[str, str], int] = defaultdict(int)  # (topic, group) -> offset
        self._cond = threading.Condition()

    def publish(self, topic: str, key: str, event: dict[str, Any]) -> None:
        with self._cond:
            self._topics[topic].append((key, copy.deepcopy(event)))
            self._cond.notify_all()

    def subscribe(self, topic: str, group: str) -> "InProcessSubscription":
        return InProcessSubscription(self, topic, group)

    def close(self) -> None:
        """Nothing to release."""


class InProcessSubscription:
    def __init__(self, bus: InProcessEventBus, topic: str, group: str) -> None:
        self.bus, self.topic, self.group = bus, topic, group
        with bus._cond:
            self._position = bus._committed[(topic, group)]

    def poll(self, timeout_seconds: float) -> dict[str, Any] | None:
        with self.bus._cond:
            events = self.bus._topics[self.topic]
            if self._position >= len(events):
                self.bus._cond.wait_for(lambda: self._position < len(events), timeout_seconds)
            if self._position >= len(events):
                return None
            _, event = events[self._position]
            self._position += 1
            return copy.deepcopy(event)

    def commit(self) -> None:
        with self.bus._cond:
            key = (self.topic, self.group)
            self.bus._committed[key] = max(self.bus._committed[key], self._position)

    def close(self) -> None:
        """Uncommitted events will be delivered again to the next subscriber."""
