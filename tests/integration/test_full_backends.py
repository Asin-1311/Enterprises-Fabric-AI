"""The full-profile adapters against the real servers in deploy/compose.yaml.

    docker compose --env-file .env -f deploy/compose.yaml --profile core up -d
    docker compose --env-file .env -f deploy/compose.yaml --profile ingest up -d   # for Kafka
    .venv\\Scripts\\python.exe -m pytest -m integration

Each test uses its own throwaway PostgreSQL database, Qdrant collection or OpenSearch
index and removes it afterwards, so the demo data in those servers is never touched.
A test whose server is not running is skipped with the command that starts it.

Covers: R-6-stack (PostgreSQL + Alembic, Qdrant server, OpenSearch, Redis, Kafka).
"""

import socket
import uuid
from urllib.parse import urlparse

import pytest
from qdrant_client import QdrantClient
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from app.config import get_settings
from app.db.models import Chunk, Document, User
from app.ports import AccessScope
from app.retrieval.acl import allowed_chunk_ids, can_see, load_user, qdrant_filter
from tests import port_contracts as contract
from tests.helpers import HashingEmbedder, make_searcher
from tests.test_clean_clone import _clean_clone
from tests.test_migrations import schema_drift

pytestmark = pytest.mark.integration

START = "docker compose --env-file .env -f deploy/compose.yaml --profile {group} up -d"


def _need(url: str | None, name: str, group: str, default_port: int) -> str:
    """The server's URL, or skip the test with how to start it."""
    if not url:
        pytest.skip(f"set {name} in .env (see .env.example)")
    parsed = urlparse(url if "://" in url else f"tcp://{url}")
    try:
        socket.create_connection((parsed.hostname or "localhost", parsed.port or default_port),
                                 timeout=1).close()
    except OSError:
        pytest.skip(f"{name} is not reachable; start it with: {START.format(group=group)}")
    return url


@pytest.fixture(scope="module")
def settings():
    return get_settings()


@pytest.fixture()
def pg_url(settings):
    """A fresh PostgreSQL database for one test, dropped afterwards."""
    base = make_url(_need(settings.database_url, "DATABASE_URL", "core", 5432))
    admin = create_engine(base.set(database="postgres"), isolation_level="AUTOCOMMIT")
    name = f"efa_test_{uuid.uuid4().hex[:10]}"
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    yield base.set(database=name).render_as_string(hide_password=False)
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()


@pytest.fixture()
def pg_store(pg_url):
    from app.adapters.full.metadata import PostgresMetadataStore

    store = PostgresMetadataStore(pg_url)
    store.initialise()
    yield store
    store.engine.dispose()


@pytest.fixture()
def opensearch_index(settings):
    from app.adapters.full.keyword import OpenSearchKeywordIndex

    url = _need(settings.opensearch_url, "OPENSEARCH_URL", "core", 9200)
    index = OpenSearchKeywordIndex(url, f"efa-test-{uuid.uuid4().hex[:10]}")
    yield index
    index.client.indices.delete(index=index.index, ignore_unavailable=True)


@pytest.fixture()
def qdrant_server(settings):
    from app.adapters.full.vector import open_server_vector_index

    url = _need(settings.qdrant_url, "QDRANT_URL", "core", 6333)
    store = open_server_vector_index(url, f"efa_test_{uuid.uuid4().hex[:10]}", HashingEmbedder.dim)
    yield store
    store.client.delete_collection(store.collection)


# --------------------------------------------------------------------------- PostgreSQL

def test_postgres_migrations_match_the_models_and_guard_the_audit_log(pg_store) -> None:
    """Covers: R-6-stack. Alembic builds the models' schema on PostgreSQL 16; the audit
    log refuses UPDATE, DELETE and TRUNCATE."""
    assert schema_drift(pg_store.engine) == []
    pg_store.initialise()  # idempotent
    assert "alembic_version" in inspect(pg_store.engine).get_table_names()
    with pg_store.engine.begin() as conn:
        conn.execute(text("INSERT INTO audit_log (actor, action, target, created_at) "
                          "VALUES ('t', 'a', NULL, now())"))
    for statement in ("UPDATE audit_log SET actor = 'x'", "DELETE FROM audit_log",
                      "TRUNCATE audit_log"):
        with pytest.raises(DBAPIError, match="append-only"):
            with pg_store.engine.begin() as conn:
                conn.execute(text(statement))


# --------------------------------------------------------------------------- port contracts

def test_opensearch_keyword_index_contract(pg_store, opensearch_index) -> None:
    """Covers: R-6-stack (OpenSearch BM25 with the access filter inside the query)."""
    contract.seed_metadata(pg_store.sessions)
    contract.check_keyword_index(opensearch_index, pg_store.sessions)


def test_qdrant_server_vector_index_contract(pg_store, qdrant_server) -> None:
    """Covers: R-6-stack (Qdrant server)."""
    contract.seed_metadata(pg_store.sessions)
    contract.check_vector_index(qdrant_server, pg_store.sessions, HashingEmbedder().embed_query)


def test_redis_cache_contract(settings) -> None:
    """Covers: R-6-stack (Redis)."""
    from app.adapters.full.cache import RedisCache

    contract.check_cache(RedisCache(_need(settings.redis_url, "REDIS_URL", "core", 6379)))


def test_kafka_event_bus_contract(settings) -> None:
    """Covers: R-6-stack (Kafka in KRaft mode)."""
    from app.adapters.full.events import KafkaEventBus

    bus = KafkaEventBus(_need(settings.kafka_bootstrap, "KAFKA_BOOTSTRAP", "ingest", 9092))
    try:
        contract.check_event_bus(bus, poll_seconds=20)
    finally:
        bus.close()


# --------------------------------------------------------------------------- whole corpus

def test_synthetic_corpus_on_the_full_stack_and_access_agreement(
        tmp_path, pg_store, qdrant_server, opensearch_index) -> None:
    """Covers: R-6-stack. The synthetic corpus indexes into PostgreSQL, the Qdrant server
    and OpenSearch; for EVERY user, the chunks each index permits equal the metadata
    store's allowed set and `can_see` (three independent statements of one rule)."""
    from app.adapters.full.keyword import access_filter
    from app.ingest.indexer import Indexer

    root = _clean_clone(tmp_path)
    data = root / "data"
    indexer = Indexer(project_root=root, manifest_path=data / "manifest.csv",
                      users_path=data / "users.csv", engagements_path=data / "engagements.csv",
                      tyk_dir=data / "tyk", session_factory=pg_store.sessions,
                      store=qdrant_server, embedder=HashingEmbedder(), bm25=opensearch_index,
                      synthetic_manifest_path=data / "manifest.synthetic.csv")
    report = indexer.sync(actor="integration-test")
    assert len(report.indexed) == 40

    with pg_store.sessions() as s:
        n_chunks = s.query(Chunk).count()
        docs = {d.doc_id: d for d in s.scalars(select(Document))}
        chunk_doc = dict(s.execute(select(Chunk.chunk_id, Chunk.doc_id)).all())
        user_ids = list(s.scalars(select(User.user_id)))
    assert qdrant_server.count() == opensearch_index.count() == n_chunks

    for uid in user_ids:
        with pg_store.sessions() as s:
            user = load_user(s, uid)
            sql = allowed_chunk_ids(s, user)
        rule = {cid for cid, d in chunk_doc.items()
                if can_see(user, docs[d].access_level, docs[d].engagement_id)}
        scope = AccessScope(user, None, frozenset(sql))
        body = {"size": 10000, "_source": ["chunk_id"],
                "query": {"bool": {"filter": access_filter(scope)}}}
        in_opensearch = {h["_source"]["chunk_id"] for h in opensearch_index.client.search(
            index=opensearch_index.index, body=body)["hits"]["hits"]}
        points, _ = qdrant_server.client.scroll(qdrant_server.collection, limit=10000,
                                                scroll_filter=qdrant_filter(user))
        in_qdrant = {p.payload["chunk_id"] for p in points}
        assert sql == rule == in_opensearch == in_qdrant, uid

    staff = load_user(pg_store.sessions(), "S01")
    hits = make_searcher(indexer).search("final audit file assembly days", staff, limit=5)
    assert hits and all(can_see(staff, docs[h.doc_id].access_level, docs[h.doc_id].engagement_id)
                        for h in hits)


def test_copy_lite_to_full_on_the_real_servers(tmp_path, pg_store, qdrant_server,
                                               opensearch_index) -> None:
    """The copy script fills PostgreSQL, the Qdrant server and OpenSearch from a lite index
    without re-embedding; dense search gives the same results before and after."""
    from scripts.copy_lite_to_full import copy_lite_to_full
    from tests.helpers import make_indexer

    root = _clean_clone(tmp_path)
    embedder = HashingEmbedder()
    lite = make_indexer(root, embedder, synthetic_fallback=True)
    lite.sync(actor="test")
    report = copy_lite_to_full(lite.sessions, lite.store, pg_store.sessions, qdrant_server,
                               opensearch_index)
    assert report.vectors == qdrant_server.count() == opensearch_index.count() == lite.store.count()

    with lite.sessions() as s:
        partner = load_user(s, "P01")
    before = make_searcher(lite).search("materiality benchmark", partner, mode="dense", limit=10)
    full = make_searcher(lite)
    full.sessions, full.store, full.bm25 = pg_store.sessions, qdrant_server, opensearch_index
    after = full.search("materiality benchmark", partner, mode="dense", limit=10)
    assert [h.chunk_id for h in after] == [h.chunk_id for h in before]
    assert full.search("materiality benchmark", partner, mode="bm25", limit=5)


# --------------------------------------------------------------------------- change events (M12a)

def test_change_events_flow_through_kafka_into_every_full_index(
        tmp_path, settings, pg_store, qdrant_server, opensearch_index) -> None:
    """Covers: R-5-ing, R-7.1-a (full profile). A change event published on Kafka is
    applied by the worker to PostgreSQL, the Qdrant server and OpenSearch, each carrying
    the enriched source and classification fields."""
    from app.adapters.full.events import KafkaEventBus
    from app.ingest.events import DocumentChanged
    from app.ingest.indexer import Indexer
    from app.ingest.worker import IngestWorker
    from tests.helpers import write_doc

    bus = KafkaEventBus(_need(settings.kafka_bootstrap, "KAFKA_BOOTSTRAP", "ingest", 9092))
    run = uuid.uuid4().hex[:8]
    topic, group = f"doc-changes-test-{run}", f"indexer-test-{run}"
    root = _clean_clone(tmp_path)
    data = root / "data"
    indexer = Indexer(project_root=root, manifest_path=data / "manifest.csv",
                      users_path=data / "users.csv", engagements_path=data / "engagements.csv",
                      tyk_dir=data / "tyk", session_factory=pg_store.sessions,
                      store=qdrant_server, embedder=HashingEmbedder(), bm25=opensearch_index,
                      synthetic_manifest_path=data / "manifest.synthetic.csv")
    worker = IngestWorker(indexer, bus, topic=topic, group=group)
    try:
        write_doc(root, "firm_policy/kafka_test.md", "# Kafka test\n\nNarwhalword lives here.")
        event = DocumentChanged(source_id="fs-firm_policy", doc_id="KAFKA-TEST-1",
                                uri="data/corpus/firm_policy/kafka_test.md", op="upsert",
                                content_hash="x" * 64,
                                metadata=dict(source_type="firm_policy", title="Kafka test",
                                              access_level="manager", effective_date="2026-11-19",
                                              owner="P02"))
        bus.publish(topic, event.doc_id, event.to_dict())
        outcomes = []
        for _ in range(5):  # the consumer group may take a few seconds to start
            outcomes += worker.drain(wait_seconds=10)
            if outcomes:
                break
        assert outcomes == [(event.event_id, "indexed")]
        with pg_store.sessions() as s:
            doc = s.get(Document, "KAFKA-TEST-1")
        assert (doc.source_id, doc.owner, doc.classification) == ("fs-firm_policy", "P02", "confidential")
        assert {p["classification"] for p in qdrant_server.payloads("KAFKA-TEST-1")} == {"confidential"}
        hits = opensearch_index.client.search(index=opensearch_index.index, body={
            "query": {"term": {"doc_id": "KAFKA-TEST-1"}}})["hits"]["hits"]
        assert hits and all(h["_source"]["source_id"] == "fs-firm_policy"
                            and h["_source"]["classification"] == "confidential" for h in hits)
        assert worker.drain(wait_seconds=2) == []  # committed: not delivered again
    finally:
        worker.close()
        bus.close()
