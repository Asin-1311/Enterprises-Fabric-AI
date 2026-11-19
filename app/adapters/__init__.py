"""Choose the adapter for each port from the profile (PROFILE=lite|full in .env).

This is the only place that knows which backend is in use. Everything else asks for a
port: `open_metadata_store(settings)`, `open_vector_index(settings, dim)`, and so on.
"""

from pathlib import Path

from qdrant_client import QdrantClient

from app.config import Settings
from app.ports import Cache, EventBus, KeywordIndex, MetadataStore, VectorIndex


def _required(value: str | None, name: str) -> str:
    if not value:
        raise ValueError(f"PROFILE=full needs {name} in .env (see .env.example)")
    return value


def open_metadata_store(settings: Settings) -> MetadataStore:
    """SQLite file (lite) or PostgreSQL (full). Call `initialise()` before use."""
    if settings.profile == "full":
        from app.adapters.full.metadata import PostgresMetadataStore

        return PostgresMetadataStore(_required(settings.database_url, "DATABASE_URL"))
    from app.adapters.lite.metadata import SqliteMetadataStore

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return SqliteMetadataStore(settings.sqlite_url)


def open_vector_index(settings: Settings, dim: int,
                      embedded_client: QdrantClient | None = None) -> VectorIndex:
    """Embedded Qdrant at data/qdrant (lite) or a Qdrant server (full).
    `embedded_client` lets the API share one embedded client (it holds a file lock)."""
    if settings.profile == "full":
        from app.adapters.full.vector import open_server_vector_index

        return open_server_vector_index(_required(settings.qdrant_url, "QDRANT_URL"),
                                        settings.qdrant_collection, dim)
    from app.adapters.lite.vector import open_embedded_vector_index

    return open_embedded_vector_index(Path(settings.qdrant_path), settings.qdrant_collection,
                                      dim, client=embedded_client)


def open_keyword_index(settings: Settings) -> KeywordIndex:
    """rank_bm25 in memory (lite; call `refresh` to load it) or OpenSearch (full)."""
    if settings.profile == "full":
        from app.adapters.full.keyword import OpenSearchKeywordIndex

        return OpenSearchKeywordIndex(_required(settings.opensearch_url, "OPENSEARCH_URL"),
                                      settings.opensearch_index)
    from app.adapters.lite.keyword import RankBm25Index

    return RankBm25Index()


def open_cache(settings: Settings) -> Cache:
    if settings.profile == "full":
        from app.adapters.full.cache import RedisCache

        return RedisCache(_required(settings.redis_url, "REDIS_URL"))
    from app.adapters.lite.cache import MemoryCache

    return MemoryCache()


def open_event_bus(settings: Settings) -> EventBus:
    if settings.profile == "full":
        from app.adapters.full.events import KafkaEventBus

        return KafkaEventBus(_required(settings.kafka_bootstrap, "KAFKA_BOOTSTRAP"))
    from app.adapters.lite.events import InProcessEventBus

    return InProcessEventBus()
