"""Rate limiting in Redis (full profile; M16a): shared by every API worker, so a user
cannot get more calls by reaching another worker.

- RedisRateLimiter: the same token bucket as the lite profile, as one Lua script, so the
  read-refill-take step is atomic even when two workers serve the same user at once.
- RedisFailureLockout: the failure times of each key in a sorted set (score = time);
  times older than the window are removed before counting.
The clock is passed in (seconds), so tests can move time without waiting.
"""

import time
import uuid
from collections.abc import Callable

_TAKE = """
local capacity = tonumber(ARGV[1])
local rate = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local state = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(state[1]) or capacity
local ts = tonumber(state[2]) or now
tokens = math.min(capacity, tokens + math.max(0, now - ts) * rate)
local wait = 0
if tokens >= 1 then tokens = tokens - 1 else wait = (1 - tokens) / rate end
redis.call('HSET', KEYS[1], 'tokens', tostring(tokens), 'ts', tostring(now))
redis.call('PEXPIRE', KEYS[1], math.ceil(capacity / rate * 1000) + 1000)
return tostring(wait)
"""


def _client(url: str):
    import redis

    return redis.Redis.from_url(url, socket_timeout=2)


class RedisRateLimiter:
    PREFIX = "efa:rate:"

    def __init__(self, url: str, per_minute: float, name: str, client=None,
                 clock: Callable[[], float] = time.time) -> None:
        self.client = client or _client(url)
        self.capacity, self.rate = per_minute, per_minute / 60.0
        self.prefix = f"{self.PREFIX}{name}:"
        self.clock = clock
        self._take = self.client.register_script(_TAKE)

    def take(self, key: str) -> float:
        return float(self._take(keys=[self.prefix + key], args=[self.capacity, self.rate, self.clock()]))


class RedisFailureLockout:
    PREFIX = "efa:lockout:"

    def __init__(self, url: str, max_failures: int, window_minutes: float, name: str,
                 client=None, clock: Callable[[], float] = time.time) -> None:
        self.client = client or _client(url)
        self.max_failures, self.window = max_failures, window_minutes * 60.0
        self.prefix = f"{self.PREFIX}{name}:"
        self.clock = clock

    def record_failure(self, key: str) -> None:
        now, k = self.clock(), self.prefix + key
        pipe = self.client.pipeline()
        pipe.zadd(k, {f"{now}:{uuid.uuid4().hex[:8]}": now})
        pipe.zremrangebyscore(k, "-inf", now - self.window)
        pipe.pexpire(k, int(self.window * 1000) + 1000)
        pipe.execute()

    def locked_for(self, key: str) -> float:
        now, k = self.clock(), self.prefix + key
        self.client.zremrangebyscore(k, "-inf", now - self.window)
        times = [score for _, score in self.client.zrange(k, 0, -1, withscores=True)]
        if len(times) < self.max_failures:
            return 0.0
        return max(0.0, self.window - (now - times[-self.max_failures]))
