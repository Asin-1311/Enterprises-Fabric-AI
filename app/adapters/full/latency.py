"""LatencyWindow in Redis (full profile; M15a): one list, newest first, trimmed to the
window size, shared by every API worker."""

from app.adaptive.latency_window import p95


class RedisLatencyWindow:
    def __init__(self, url: str, size: int = 100, min_samples: int = 20,
                 key: str = "fabric:latency:answers", client=None) -> None:
        if client is None:
            import redis

            client = redis.Redis.from_url(url, socket_timeout=2)
        self.client, self.size, self.min_samples, self.key = client, size, min_samples, key

    def record(self, seconds: float) -> None:
        pipe = self.client.pipeline()
        pipe.lpush(self.key, float(seconds))
        pipe.ltrim(self.key, 0, self.size - 1)
        pipe.execute()

    def p95(self) -> float | None:
        values = [float(v) for v in self.client.lrange(self.key, 0, self.size - 1)]
        return p95(values) if len(values) >= self.min_samples else None
