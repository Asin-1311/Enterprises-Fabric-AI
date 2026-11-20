"""LatencyWindow in process memory (lite profile; M15a)."""

import threading
from collections import deque

from app.adaptive.latency_window import p95


class InMemoryLatencyWindow:
    def __init__(self, size: int = 100, min_samples: int = 20) -> None:
        self.values: deque[float] = deque(maxlen=size)
        self.min_samples = min_samples
        self._lock = threading.Lock()

    def record(self, seconds: float) -> None:
        with self._lock:
            self.values.append(float(seconds))

    def p95(self) -> float | None:
        with self._lock:
            values = list(self.values)
        return p95(values) if len(values) >= self.min_samples else None
