"""The lite adapters satisfy the port contracts (tests/port_contracts.py).

Covers: R-6-stack (the ports; lite side). The same contracts run against the full
adapters in tests/integration/test_full_backends.py.
"""

from pathlib import Path

import pytest
from qdrant_client import QdrantClient

from app.adapters import open_cache, open_event_bus, open_keyword_index, open_metadata_store
from app.adapters.lite.cache import MemoryCache
from app.adapters.lite.events import InProcessEventBus
from app.adapters.lite.keyword import RankBm25Index
from app.adapters.lite.metadata import SqliteMetadataStore
from app.adapters.lite.vector import open_embedded_vector_index
from app.ports import Cache, EventBus, KeywordIndex, MetadataStore, VectorIndex
from tests import port_contracts as contract
from tests.helpers import HashingEmbedder
from tests.test_agents import _settings


@pytest.fixture()
def sessions(tmp_path: Path):
    store = SqliteMetadataStore(f"sqlite:///{(tmp_path / 'ports.db').as_posix()}")
    store.initialise()
    contract.seed_metadata(store.sessions)
    return store.sessions


def test_lite_keyword_index_contract(sessions) -> None:
    contract.check_keyword_index(RankBm25Index(), sessions)


def test_lite_vector_index_contract(sessions) -> None:
    embedder = HashingEmbedder()
    index = open_embedded_vector_index(Path("unused"), "contract", embedder.dim,
                                       client=QdrantClient(":memory:"))
    contract.check_vector_index(index, sessions, embedder.embed_query)


def test_lite_cache_contract() -> None:
    contract.check_cache(MemoryCache())


def test_lite_event_bus_contract() -> None:
    contract.check_event_bus(InProcessEventBus(), poll_seconds=0.2)


def test_lite_adapters_are_the_ports_and_the_default_profile(tmp_path) -> None:
    """The factory returns lite adapters unless PROFILE=full, and each satisfies its port."""
    settings = _settings().model_copy(update={"data_dir": tmp_path,
                                              "sqlite_path": tmp_path / "x.db"})
    assert settings.profile == "lite"
    assert isinstance(open_metadata_store(settings), MetadataStore)
    assert isinstance(open_keyword_index(settings), KeywordIndex)
    assert isinstance(open_cache(settings), Cache)
    assert isinstance(open_event_bus(settings), EventBus)
    embedded = open_embedded_vector_index(Path("unused"), "c", 8, client=QdrantClient(":memory:"))
    assert isinstance(embedded, VectorIndex)


def test_full_profile_without_server_addresses_fails_clearly(tmp_path) -> None:
    from app.adapters import open_vector_index

    settings = _settings().model_copy(update={"profile": "full"})
    for opener, name in ((open_metadata_store, "DATABASE_URL"), (open_keyword_index, "OPENSEARCH_URL"),
                         (open_cache, "REDIS_URL"), (open_event_bus, "KAFKA_BOOTSTRAP")):
        with pytest.raises(ValueError, match=name):
            opener(settings)
    with pytest.raises(ValueError, match="QDRANT_URL"):
        open_vector_index(settings, 8)


def test_memory_cache_expiry_uses_its_clock() -> None:
    now = [0.0]
    cache = MemoryCache(clock=lambda: now[0])
    cache.set("k", b"v", ttl_seconds=10)
    now[0] = 9.9
    assert cache.get("k") == b"v"
    now[0] = 10.0
    assert cache.get("k") is None
