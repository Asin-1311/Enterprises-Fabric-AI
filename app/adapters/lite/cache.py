"""Cache in process memory (lite profile). Lost on restart; not shared between processes."""

import threading
import time
from collections.abc import Callable


class MemoryCache:
    """A dictionary with expiry times. The clock is injectable for tests."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self._items: dict[str, tuple[float, bytes]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> bytes | None:
        with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            expires_at, value = item
            if self.clock() >= expires_at:
                del self._items[key]
                return None
            return value

    def set(self, key: str, value: bytes, ttl_seconds: float) -> None:
        with self._lock:
            self._items[key] = (self.clock() + ttl_seconds, value)

    def delete(self, key: str) -> None:
        with self._lock:
            self._items.pop(key, None)
