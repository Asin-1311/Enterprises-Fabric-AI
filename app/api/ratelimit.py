"""In-process rate limiting for the sign-in routes (lite profile; Redis comes in M16).

Two separate protections, both keyed by the caller's IP address:

- **Token bucket**: each IP may call the /auth/* routes `per_minute` times a minute.
  The bucket refills continuously, so short bursts are fine but sustained hammering is not.
- **Lockout**: `max_failures` failed sign-ins within `window_minutes` lock that IP out of
  every /auth/* route until the oldest failure leaves the window.

Both answer HTTP 429 with a `Retry-After` header (whole seconds). The clock is injectable
so tests do not have to wait. The IP comes from the TCP connection only: X-Forwarded-For
is not trusted, because any client could set it.
"""

import math
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable


class TokenBucket:
    """`capacity` calls, refilled at `capacity` per minute, per key."""

    def __init__(self, per_minute: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.capacity = per_minute
        self.rate = per_minute / 60.0  # tokens per second
        self.clock = clock
        self._state: dict[str, tuple[float, float]] = {}  # key -> (tokens, last time)
        self._lock = threading.Lock()

    def take(self, key: str) -> float:
        """Use one token. Returns 0 if allowed, else the seconds until one is available."""
        with self._lock:
            now = self.clock()
            tokens, last = self._state.get(key, (self.capacity, now))
            tokens = min(self.capacity, tokens + (now - last) * self.rate)
            if tokens >= 1:
                self._state[key] = (tokens - 1, now)
                return 0.0
            self._state[key] = (tokens, now)
            return (1 - tokens) / self.rate


class FailureLockout:
    """Locks a key after `max_failures` failures within `window_minutes`."""

    def __init__(self, max_failures: int, window_minutes: float,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.max_failures = max_failures
        self.window = window_minutes * 60.0
        self.clock = clock
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _expire(self, key: str, now: float) -> deque[float]:
        times = self._failures[key]
        while times and now - times[0] >= self.window:
            times.popleft()
        return times

    def record_failure(self, key: str) -> None:
        with self._lock:
            now = self.clock()
            self._expire(key, now).append(now)

    def locked_for(self, key: str) -> float:
        """0 if the key may try again, else the seconds until the lockout ends."""
        with self._lock:
            now = self.clock()
            times = self._expire(key, now)
            if len(times) < self.max_failures:
                return 0.0
            return self.window - (now - times[-self.max_failures])


def retry_after_header(seconds: float) -> dict[str, str]:
    """`Retry-After` must be whole seconds; round up and never send 0."""
    return {"Retry-After": str(max(1, math.ceil(seconds)))}
