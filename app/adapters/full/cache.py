"""Cache on Redis (full profile): shared by every API process, survives restarts."""


class RedisCache:
    PREFIX = "efa:cache:"

    def __init__(self, url: str, client=None) -> None:
        if client is None:
            import redis

            client = redis.Redis.from_url(url)
        self.client = client

    def get(self, key: str) -> bytes | None:
        return self.client.get(self.PREFIX + key)

    def set(self, key: str, value: bytes, ttl_seconds: float) -> None:
        self.client.set(self.PREFIX + key, value, px=max(1, int(ttl_seconds * 1000)))

    def delete(self, key: str) -> None:
        self.client.delete(self.PREFIX + key)
