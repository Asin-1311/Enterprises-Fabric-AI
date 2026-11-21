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
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from app.config import get_settings
from app.db.models import Chunk, Document, User
from app.ingest.manifest import read_manifest
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
    assert len(report.indexed) == len(read_manifest(root / "data" / "manifest.synthetic.csv")[1])  # 42 since M13a

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


# ------------------------------------------------------------- connectors (M12c)

def _full_indexer(root, pg_store, qdrant_server, opensearch_index):
    from app.ingest.indexer import Indexer

    data = root / "data"
    indexer = Indexer(project_root=root, manifest_path=data / "manifest.csv",
                      users_path=data / "users.csv", engagements_path=data / "engagements.csv",
                      tyk_dir=data / "tyk", session_factory=pg_store.sessions,
                      store=qdrant_server, embedder=HashingEmbedder(), bm25=opensearch_index,
                      synthetic_manifest_path=data / "manifest.synthetic.csv")
    indexer.load_reference_data()
    return indexer


def _drain_until(worker, expected: int) -> list[str]:
    outcomes: list[str] = []
    for _ in range(6):  # the Kafka consumer group may take a few seconds to start
        outcomes += [o for _, o in worker.drain(wait_seconds=10)]
        if len(outcomes) >= expected:
            break
    return outcomes


def _in_every_full_index(pg_store, qdrant_server, opensearch_index, doc_id: str) -> bool:
    with pg_store.sessions() as s:
        in_pg = s.get(Document, doc_id) is not None
    return in_pg and qdrant_server.count(doc_id) > 0 and opensearch_index.count(doc_id) > 0


def test_s3_connector_on_minio_through_kafka_into_every_full_index(
        tmp_path, settings, pg_store, qdrant_server, opensearch_index) -> None:
    """Covers: R-7.1-c, R-5-ing (S3 on the real MinIO). Objects in the bucket are polled,
    published on Kafka and indexed in PostgreSQL, Qdrant and OpenSearch; an overwritten
    object is re-sent alone; a deleted object is removed from every index."""
    import os

    from app.adapters.full.events import KafkaEventBus
    from app.config import env_reference
    from app.connectors import build_connector
    from app.connectors.runner import ConnectorRunner
    from app.db.models import Source
    from app.ingest.worker import IngestWorker
    from scripts.seed_minio import BUCKET, DOCS, seed

    endpoint = _need(os.environ.get("S3_ENDPOINT_URL", "http://127.0.0.1:9000"), "S3_ENDPOINT_URL",
                     "ingest", 9000)
    if not env_reference("env:MINIO_ROOT_USER"):
        pytest.skip("set MINIO_ROOT_USER and MINIO_ROOT_PASSWORD in .env (see .env.example)")
    import boto3

    s3 = boto3.client("s3", endpoint_url=endpoint, region_name="us-east-1",
                      aws_access_key_id=env_reference("env:MINIO_ROOT_USER"),
                      aws_secret_access_key=env_reference("env:MINIO_ROOT_PASSWORD"))
    run = uuid.uuid4().hex[:8]
    prefix, topic = f"test-{run}/", f"doc-changes-test-{run}"
    keys = seed(s3, prefix=prefix)
    bus = KafkaEventBus(_need(settings.kafka_bootstrap, "KAFKA_BOOTSTRAP", "ingest", 9092))
    root = _clean_clone(tmp_path)
    indexer = _full_indexer(root, pg_store, qdrant_server, opensearch_index)
    worker = IngestWorker(indexer, bus, topic=topic, group=f"indexer-test-{run}")
    source_id = f"s3-test-{run}"
    with pg_store.sessions() as s:
        s.add(Source(id=source_id, type="s3", owner="P02", classification="public_internal",
                     status="active", connector_config={
                         "bucket": BUCKET, "prefix": prefix, "endpoint_url": endpoint,
                         "access_key": "env:MINIO_ROOT_USER", "secret_key": "env:MINIO_ROOT_PASSWORD",
                         "defaults": {"source_type": "firm_policy", "access_level": "staff"}}))
        s.commit()
    runner = ConnectorRunner(pg_store.sessions, bus, settings, topic=topic, factory=lambda src: build_connector(
        src.id, src.type, src.connector_config, landing_dir=indexer.landing_dir,
        work_dir=root / "data" / "connectors", project_root=root))
    try:
        assert runner.poll_once() == {source_id: len(DOCS)}
        assert _drain_until(worker, len(DOCS)) == ["indexed"] * len(DOCS)
        doc_ids = [f"{source_id}-{k.replace('/', '-')}" for k in keys]
        for doc_id in doc_ids:
            assert _in_every_full_index(pg_store, qdrant_server, opensearch_index, doc_id), doc_id
        with pg_store.sessions() as s:
            assert s.get(Document, doc_ids[0]).owner == "P02"

        s3.put_object(Bucket=BUCKET, Key=keys[0], Body=s3.get_object(Bucket=BUCKET, Key=keys[0])[
            "Body"].read() + b"\n## 3. Added\nAn added section.\n")
        assert runner.poll_once() == {source_id: 1}
        assert _drain_until(worker, 1) == ["indexed"]

        s3.delete_object(Bucket=BUCKET, Key=keys[1])
        assert runner.poll_once() == {source_id: 1}
        assert _drain_until(worker, 1) == ["removed"]
        assert qdrant_server.count(doc_ids[1]) == 0 and opensearch_index.count(doc_ids[1]) == 0
    finally:
        for key in keys:
            s3.delete_object(Bucket=BUCKET, Key=key)
        worker.close()
        bus.close()


def test_git_connector_through_kafka_into_every_full_index(
        tmp_path, settings, pg_store, qdrant_server, opensearch_index) -> None:
    """Covers: R-7.1-c, R-5-ing (git, full profile): a commit to the synthetic repository
    reaches PostgreSQL, Qdrant and OpenSearch through Kafka."""
    from app.adapters.full.events import KafkaEventBus
    from app.connectors import build_connector
    from app.connectors.runner import ConnectorRunner
    from app.db.models import Source
    from app.ingest.worker import IngestWorker
    from scripts.make_synthetic_git_repo import DOCS_V1, make_repo

    bus = KafkaEventBus(_need(settings.kafka_bootstrap, "KAFKA_BOOTSTRAP", "ingest", 9092))
    run = uuid.uuid4().hex[:8]
    topic = f"doc-changes-test-{run}"
    root = _clean_clone(tmp_path)
    make_repo(root / "data" / "sources" / "repo", [DOCS_V1])
    indexer = _full_indexer(root, pg_store, qdrant_server, opensearch_index)
    worker = IngestWorker(indexer, bus, topic=topic, group=f"indexer-test-{run}")
    with pg_store.sessions() as s:
        s.add(Source(id="git-test", type="git", classification="public_internal", status="active",
                     connector_config={"url": "data/sources/repo", "branch": "main",
                                       "defaults": {"source_type": "firm_policy", "access_level": "staff"}}))
        s.commit()
    runner = ConnectorRunner(pg_store.sessions, bus, settings, topic=topic, factory=lambda src: build_connector(
        src.id, src.type, src.connector_config, landing_dir=indexer.landing_dir,
        work_dir=root / "data" / "connectors", project_root=root))
    try:
        expected = [p for p in DOCS_V1 if p.endswith(".md")]
        assert runner.poll_once() == {"git-test": len(expected)}
        assert _drain_until(worker, len(expected)) == ["indexed"] * len(expected)
        for path in expected:
            doc_id = "git-test-" + path.replace("/", "-")
            assert _in_every_full_index(pg_store, qdrant_server, opensearch_index, doc_id), doc_id
    finally:
        worker.close()
        bus.close()


def test_signed_webhook_push_through_kafka_into_every_full_index(
        tmp_path, settings, pg_store, qdrant_server, opensearch_index, monkeypatch) -> None:
    """Covers: R-7.1-c, R-5-ing (webhook, full profile): a signed POST to the API is
    published on Kafka and indexed in PostgreSQL, Qdrant and OpenSearch."""
    import json
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    from app.adapters.full.events import KafkaEventBus
    from app.api.main import Services, create_app
    from app.api.webhooks import signature
    from app.concurrency import ReadWriteLock
    from app.db.models import Source
    from app.ingest.events import TOPIC
    from app.ingest.worker import IngestWorker

    bus = KafkaEventBus(_need(settings.kafka_bootstrap, "KAFKA_BOOTSTRAP", "ingest", 9092))
    run = uuid.uuid4().hex[:8]
    root = _clean_clone(tmp_path)
    indexer = _full_indexer(root, pg_store, qdrant_server, opensearch_index)
    worker = IngestWorker(indexer, bus, topic=TOPIC, group=f"indexer-test-{run}")
    monkeypatch.setenv("TEST_WEBHOOK_SECRET", "integration-secret")
    with pg_store.sessions() as s:
        s.add(Source(id="webhook-test", type="webhook", classification="public_internal",
                     status="active", connector_config={"secret": "env:TEST_WEBHOOK_SECRET"}))
        s.commit()
    services = Services(settings.model_copy(update={"landing_dir": indexer.landing_dir}),
                        SimpleNamespace(deps=SimpleNamespace(sessions=pg_store.sessions)),  # type: ignore[arg-type]
                        indexer, ReadWriteLock(), bus=bus)
    doc_id = f"WEBHOOK-IT-{run}"
    body = json.dumps({"doc_id": doc_id, "title": "Webhook integration", "source_type": "circulars",
                       "access_level": "staff", "effective_date": "2026-11-19",
                       "content": "# Webhook integration\n\n## 1. Rule\nPangolinword pushed."}).encode()
    try:
        with TestClient(create_app(services, static_dir=None)) as client:
            r = client.post("/api/v1/webhooks/documents", content=body, headers={
                "Content-Type": "application/json", "X-Source-Id": "webhook-test",
                "X-Signature-256": signature("integration-secret", body)})
        assert r.status_code == 202
        outcomes: list = []
        for _ in range(6):
            outcomes += worker.drain(wait_seconds=10)
            if any(e == r.json()["event_id"] for e, _ in outcomes):
                break
        assert (r.json()["event_id"], "indexed") in outcomes
        assert _in_every_full_index(pg_store, qdrant_server, opensearch_index, doc_id)
    finally:
        worker.close()
        bus.close()


# ----------------------------------------------- change data capture (M12d)

def test_a_dms_row_update_reaches_every_index_through_debezium_within_60_seconds(
        tmp_path, settings, pg_store, qdrant_server, opensearch_index) -> None:
    """Covers: R-3-stream, R-5-ing. A row inserted, updated and deleted in the synthetic DMS
    (PostgreSQL) is captured by Debezium, carried by Kafka, translated by the DMS bridge and
    applied by the worker to PostgreSQL, Qdrant and OpenSearch. The update must arrive
    within 60 s; the measured time is printed (pytest -s shows it)."""
    import time

    import httpx

    from app.adapters.full.events import KafkaEventBus
    from app.connectors.dms import CdcBridge
    from app.db.models import Source
    from app.ingest.md_loader import SYNTHETIC_BANNER
    from app.ingest.worker import IngestWorker
    from scripts.make_synthetic_dms import dms_url

    try:
        state = httpx.get("http://127.0.0.1:8083/connectors/dms-documents/status", timeout=3).json()
        assert state["connector"]["state"] == "RUNNING"
    except Exception:
        pytest.skip("Debezium connector not running: start the full profile, then run "
                    "scripts.make_synthetic_dms and scripts.register_debezium_connector")
    bus = KafkaEventBus(_need(settings.kafka_bootstrap, "KAFKA_BOOTSTRAP", "ingest", 9092))
    run = uuid.uuid4().hex[:8]
    out_topic, doc_id = f"doc-changes-test-{run}", f"DMS-IT-{run}"
    root = _clean_clone(tmp_path)
    indexer = _full_indexer(root, pg_store, qdrant_server, opensearch_index)
    config = {"table": "dms.documents", "defaults": {}}
    with pg_store.sessions() as s:
        s.add(Source(id="dms-test", type="dms", classification="public_internal", status="active",
                     owner="P02", connector_config=config))
        s.commit()
    bridge = CdcBridge("dms-test", config, bus, indexer.landing_dir, out_topic=out_topic,
                       group=f"cdc-test-{run}")
    worker = IngestWorker(indexer, bus, topic=out_topic, group=f"indexer-test-{run}")
    dms = create_engine(dms_url(settings.database_url))

    def body(word: str) -> str:
        return f"{SYNTHETIC_BANNER}\n\n# CDC test\n\n## 1. Rule\nThe marker is {word}.\n"

    def pump_until(check, limit: float) -> float:
        start = time.monotonic()
        while time.monotonic() - start < limit:
            bridge.drain(0.5)
            worker.drain(0.5)
            if check():
                return time.monotonic() - start
        raise AssertionError(f"not reached within {limit} s")

    def has_marker(word: str) -> bool:
        with pg_store.sessions() as s:
            texts = list(s.scalars(select(Chunk.text).where(Chunk.doc_id == doc_id)))
        hits = opensearch_index.client.search(index=opensearch_index.index, body={
            "query": {"bool": {"must": [{"match": {"text": word}}],
                               "filter": [{"term": {"doc_id": doc_id}}]}}})["hits"]["hits"]
        return any(word in t for t in texts) and qdrant_server.count(doc_id) > 0 and bool(hits)

    try:
        with dms.begin() as conn:
            conn.execute(text("INSERT INTO dms.documents (doc_id, title, source_type, access_level, "
                              "effective_date, body) VALUES (:d, 'CDC test', 'firm_policy', 'manager', "
                              "'2026-11-19', :b)"), {"d": doc_id, "b": body("aardvarkword")})
        pump_until(lambda: has_marker("aardvarkword"), 120)  # first run includes the snapshot rows
        with pg_store.sessions() as s:
            doc = s.get(Document, doc_id)
        assert (doc.source_id, doc.owner, doc.classification) == ("dms-test", "P02", "confidential")

        with dms.begin() as conn:  # the acceptance step: an UPDATE of the row
            conn.execute(text("UPDATE dms.documents SET body = :b, version = '2', updated_at = now() "
                              "WHERE doc_id = :d"), {"d": doc_id, "b": body("bandicootword")})
        seconds = pump_until(lambda: has_marker("bandicootword") and not has_marker("aardvarkword"), 60)
        print(f"\nDMS row update visible in PostgreSQL, Qdrant and OpenSearch after {seconds:.1f} s")
        assert seconds <= 60

        with dms.begin() as conn:
            conn.execute(text("DELETE FROM dms.documents WHERE doc_id = :d"), {"d": doc_id})
        pump_until(lambda: qdrant_server.count(doc_id) == 0 and opensearch_index.count(doc_id) == 0, 60)
        with pg_store.sessions() as s:
            assert s.get(Document, doc_id) is None
    finally:
        with dms.begin() as conn:
            conn.execute(text("DELETE FROM dms.documents WHERE doc_id = :d"), {"d": doc_id})
        dms.dispose()
        bridge.close()
        worker.close()
        bus.close()


# ----------------------------------------------------------------- OPA (M13a)

def test_opa_policy_tests_pass() -> None:
    """Covers: R-12-opa (`opa test policy/`, run in the pinned OPA image)."""
    import shutil
    import subprocess
    from pathlib import Path

    if shutil.which("docker") is None:
        pytest.skip("docker is not available")
    policy = Path(__file__).resolve().parents[2] / "policy"
    done = subprocess.run(["docker", "run", "--rm", "-v", f"{policy}:/policy:ro",
                           "openpolicyagent/opa:1.4.2", "test", "/policy", "-v"],
                          capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "PASS: 7/7" in done.stdout


def test_opa_server_agrees_with_the_prefilter_on_every_chunk_for_every_user(tmp_path, settings) -> None:
    """Covers: R-12-opa. For every synthetic user and every chunk of the synthetic corpus,
    the OPA server (policy/document_access.rego) allows exactly what the index pre-filter
    allows: one batch call per user."""
    from app.adapters.full.policy import OpaPolicyEngine
    from app.db.models import User
    from tests.helpers import make_indexer
    from tests.test_policy import candidates

    engine = OpaPolicyEngine(_need(settings.opa_url, "OPA_URL", "identity", 8181))
    root = _clean_clone(tmp_path)
    indexer = make_indexer(root, synthetic_fallback=True)
    indexer.sync(actor="test")
    with indexer.sessions() as s:
        ids, docs = candidates(s)
        users = [load_user(s, u) for u in s.scalars(select(User.user_id))]
        for user in users:
            decided = {cid for cid, ok in zip(ids, engine.allowed(user, docs)) if ok}
            assert decided == allowed_chunk_ids(s, user), user.user_id
    assert len(users) >= 8 and len(ids) > 100


# ----------------------------------------------------------- Keycloak (M13b)

def test_keycloak_tokens_sign_in_to_the_api_and_others_are_rejected(tmp_path, settings) -> None:
    """Covers: R-12-auth (real realm). A token the `fabric` realm issues to a synthetic user
    signs in to the API with that user's identity; a token for another audience (the realm's
    built-in admin-cli) and a tampered token are rejected with 401."""
    from types import SimpleNamespace

    import httpx
    from fastapi.testclient import TestClient

    from app.api.main import Services, create_app
    from app.config import env_reference
    from app.concurrency import ReadWriteLock
    from scripts.keycloak_token import KEYCLOAK_URL, get_token
    from tests.helpers import make_indexer

    _need(KEYCLOAK_URL, "Keycloak", "identity", 8180)
    if not env_reference("env:KEYCLOAK_DEMO_PASSWORD"):
        pytest.skip("set KEYCLOAK_DEMO_PASSWORD in .env and run scripts.keycloak_setup")
    root = _clean_clone(tmp_path)
    indexer = make_indexer(root, synthetic_fallback=True)
    indexer.load_reference_data()
    app_settings = settings.model_copy(update={"auth_mode": "oidc",
                                               "oidc_issuer": f"{KEYCLOAK_URL}/realms/fabric"})
    services = Services(app_settings, SimpleNamespace(deps=SimpleNamespace(sessions=indexer.sessions)),  # type: ignore[arg-type]
                        indexer, ReadWriteLock())
    staff, admin = get_token("S01"), get_token("A01")
    other_audience = httpx.post(f"{KEYCLOAK_URL}/realms/fabric/protocol/openid-connect/token", data={
        "grant_type": "password", "client_id": "admin-cli", "username": "s01",
        "password": env_reference("env:KEYCLOAK_DEMO_PASSWORD")}, timeout=10).json()["access_token"]
    head, body, sig = staff.split(".")
    tampered = ".".join([head, get_token("P01").split(".")[1], sig])  # partner claims, staff signature
    with TestClient(create_app(services, static_dir=None)) as client:
        me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {staff}"})
        assert me.status_code == 200 and (me.json()["user_id"], me.json()["role"]) == ("S01", "staff")
        a01 = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {admin}"}).json()
        assert (a01["user_id"], a01["is_admin"]) == ("A01", True)
        for bad in (other_audience, tampered):
            assert client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {bad}"}).status_code == 401
        assert client.post("/api/v1/auth/login", json={"email": "riya.kapoor@example-associates.test",
                                                       "password": "x" * 12}).status_code == 404


# ------------------------------------------------------ latency window (M15a)

def test_redis_latency_window_is_shared_between_workers(settings) -> None:
    """Covers: R-8-degrade (full profile): the load window lives in Redis, so every API
    worker sees the same p95."""
    from app.adapters.full.latency import RedisLatencyWindow

    url = _need(settings.redis_url, "REDIS_URL", "core", 6379)
    key = f"fabric:latency:test-{uuid.uuid4().hex[:8]}"
    a = RedisLatencyWindow(url, size=3, min_samples=2, key=key)
    b = RedisLatencyWindow(url, size=3, min_samples=2, key=key)
    try:
        a.record(1.0)
        assert b.p95() is None
        for x in (9.0, 2.0, 3.0):
            b.record(x)
        assert a.p95() == b.p95() == 9.0 and a.client.llen(key) == 3
    finally:
        a.client.delete(key)


# ------------------------------------------------- rate limits and response cache (M16a)

def test_redis_rate_limit_and_lockout_are_shared_between_workers(settings) -> None:
    """Covers: R-5-serve (full profile): two API workers share one token bucket per user
    and one sign-in lockout per IP, so reaching another worker gains nothing."""
    from app.adapters.full.ratelimit import RedisFailureLockout, RedisRateLimiter

    url = _need(settings.redis_url, "REDIS_URL", "core", 6379)
    name, now = f"test-{uuid.uuid4().hex[:8]}", [1000.0]
    a = RedisRateLimiter(url, per_minute=2, name=name, clock=lambda: now[0])
    b = RedisRateLimiter(url, per_minute=2, name=name, clock=lambda: now[0])
    assert a.take("user:S01") == 0 and b.take("user:S01") == 0
    wait = a.take("user:S01")
    assert wait == pytest.approx(30.0)  # 2 a minute: one token every 30 s
    assert b.take("user:S02") == 0      # per user
    now[0] += 30
    assert b.take("user:S01") == 0      # refilled, seen by the other worker

    la = RedisFailureLockout(url, max_failures=3, window_minutes=15, name=name, clock=lambda: now[0])
    lb = RedisFailureLockout(url, max_failures=3, window_minutes=15, name=name, clock=lambda: now[0])
    for worker in (la, lb, la):
        worker.record_failure("ip:10.0.0.9")
    assert lb.locked_for("ip:10.0.0.9") == pytest.approx(900.0)
    now[0] += 901
    assert la.locked_for("ip:10.0.0.9") == 0
    keys = list(a.client.scan_iter(f"efa:*:{name}:*"))
    if keys:
        a.client.delete(*keys)


def test_response_cache_keys_on_redis_never_cross_access_fingerprints(settings, pg_store) -> None:
    """Covers: R-5-serve (full profile): an entry stored for one fingerprint is not visible
    under another user's key, and an index write (PostgreSQL index_state) moves every key."""
    from app.adapters.full.cache import RedisCache
    from app.api.cache import ResponseCache
    from app.ingest import index_version
    from app.retrieval.acl import UserContext

    cache = RedisCache(_need(settings.redis_url, "REDIS_URL", "core", 6379))
    rc = ResponseCache(cache, pg_store.sessions, 60)
    q = f"Within how many days must the file be assembled? {uuid.uuid4().hex[:6]}"
    s1 = UserContext("S01", "staff", frozenset({"ENG-NWT-FY26"}))
    s2 = UserContext("S02", "staff", frozenset({"ENG-NWT-FY26", "ENG-MBL-FY26"}))
    twin = UserContext("S99", "staff", frozenset({"ENG-NWT-FY26"}))
    key = rc.key(q, s1)
    cache.set(key, b'{"marker": 1}', 60)
    try:
        assert rc.key(q, twin) == key and rc.key(q, s2) != key
        assert cache.get(rc.key(q, s2)) is None
        index_version.bump(pg_store.sessions)
        assert rc.key(q, s1) != key
    finally:
        cache.delete(key)


# ------------------------------------------------------ classification policies (M16c)

def test_a_classification_policy_reaches_postgres_qdrant_and_opensearch(
        tmp_path, pg_store, qdrant_server, opensearch_index) -> None:
    """Covers: R-7.10 (full profile): a rule raises the matching documents in PostgreSQL
    and in the payloads of the Qdrant server and OpenSearch; switching it off restores them."""
    from app.db.models import ClassificationPolicy

    indexer = _full_indexer(_clean_clone(tmp_path), pg_store, qdrant_server, opensearch_index)
    indexer.sync(actor="integration-test")

    def levels(doc_id: str) -> tuple[str, set, set]:
        with pg_store.sessions() as s:
            doc = s.get(Document, doc_id)
        hits = opensearch_index.client.search(index=opensearch_index.index, body={
            "size": 100, "query": {"term": {"doc_id": doc_id}}})["hits"]["hits"]
        return (doc.classification, {p["classification"] for p in qdrant_server.payloads(doc_id)},
                {h["_source"]["classification"] for h in hits})

    assert levels("FAQ-01") == ("public_internal", {"public_internal"}, {"public_internal"})
    with pg_store.sessions() as s:
        rule = ClassificationPolicy(match="source_type", pattern="faq", classification="restricted",
                                    active=True, created_by="test")
        s.add(rule)
        s.commit()
        rule_id = rule.id
    assert "FAQ-01" in indexer.reclassify(actor="test")
    assert levels("FAQ-01") == ("restricted", {"restricted"}, {"restricted"})
    with pg_store.sessions() as s:
        s.get(ClassificationPolicy, rule_id).active = False
        s.commit()
    assert "FAQ-01" in indexer.reclassify(actor="test")
    assert levels("FAQ-01") == ("public_internal", {"public_internal"}, {"public_internal"})


# ------------------------------------------------------ browser sign-in with PKCE (M16c)

def _keycloak_sign_in(client, base: str, verifier_for_challenge: str, username: str, password: str) -> str:
    """Drive the realm's sign-in page as a browser would; returns the authorization code."""
    import base64
    import hashlib
    import html
    import re
    from urllib.parse import parse_qs, urlparse

    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier_for_challenge.encode()).digest()).rstrip(b"=").decode()
    page = client.get(f"{base}/realms/fabric/protocol/openid-connect/auth", params={
        "response_type": "code", "client_id": "fabric-ui", "redirect_uri": "http://127.0.0.1:5173/",
        "scope": "openid", "state": "st-1", "code_challenge": challenge, "code_challenge_method": "S256"})
    assert page.status_code == 200, page.text[:300]
    action = html.unescape(re.search(r'<form[^>]*action="([^"]+)"', page.text).group(1))
    # Keycloak marks its sign-in cookies Secure; browsers still send them to 127.0.0.1 and
    # localhost (secure origins), httpx does not over http, so they are passed on here.
    cookie = "; ".join(f"{k}={v}" for k, v in page.cookies.items())
    done = client.post(action, data={"username": username, "password": password},
                       headers={"Cookie": cookie})
    assert done.status_code == 302, done.text[:300]
    query = parse_qs(urlparse(done.headers["location"]).query)
    assert query["state"] == ["st-1"]
    return query["code"][0]


def test_the_browser_sign_in_with_pkce_works_against_the_real_realm(tmp_path, settings) -> None:
    """Covers: R-12-auth (browser sign-in, M16c): the fabric-ui client signs a synthetic user
    in with the authorization code flow; the code is exchanged only with the matching PKCE
    verifier, and the resulting access token signs in to the API as that user."""
    import secrets
    from types import SimpleNamespace

    import httpx
    from fastapi.testclient import TestClient

    from app.api.main import Services, create_app
    from app.concurrency import ReadWriteLock
    from app.config import env_reference
    from scripts.keycloak_token import KEYCLOAK_URL
    from tests.helpers import make_indexer

    _need(KEYCLOAK_URL, "Keycloak", "identity", 8180)
    password = env_reference("env:KEYCLOAK_DEMO_PASSWORD")
    if not password:
        pytest.skip("set KEYCLOAK_DEMO_PASSWORD in .env and run scripts.keycloak_setup")
    token_url = f"{KEYCLOAK_URL}/realms/fabric/protocol/openid-connect/token"
    exchange = {"grant_type": "authorization_code", "client_id": "fabric-ui",
                "redirect_uri": "http://127.0.0.1:5173/"}

    verifier = secrets.token_urlsafe(48)
    with httpx.Client(follow_redirects=False, timeout=15) as browser:
        code = _keycloak_sign_in(browser, KEYCLOAK_URL, verifier, "m01", password)
        wrong = httpx.post(token_url, data={**exchange, "code": code, "code_verifier": secrets.token_urlsafe(48)})
        assert wrong.status_code == 400  # a stolen code is useless without the verifier
    with httpx.Client(follow_redirects=False, timeout=15) as browser:
        code = _keycloak_sign_in(browser, KEYCLOAK_URL, verifier, "m01", password)
        tokens = httpx.post(token_url, data={**exchange, "code": code, "code_verifier": verifier})
    assert tokens.status_code == 200, tokens.text
    access = tokens.json()["access_token"]
    assert tokens.json().get("refresh_token")

    root = _clean_clone(tmp_path)
    indexer = make_indexer(root, synthetic_fallback=True)
    indexer.load_reference_data()
    app_settings = settings.model_copy(update={"auth_mode": "oidc",
                                               "oidc_issuer": f"{KEYCLOAK_URL}/realms/fabric"})
    services = Services(app_settings, SimpleNamespace(deps=SimpleNamespace(sessions=indexer.sessions)),  # type: ignore[arg-type]
                        indexer, ReadWriteLock())
    with TestClient(create_app(services, static_dir=None)) as client:
        config = client.get("/api/v1/auth/config").json()
        assert (config["mode"], config["oidc_client_id"]) == ("oidc", "fabric-ui")
        me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {access}"})
        assert me.status_code == 200 and (me.json()["user_id"], me.json()["role"]) == ("M01", "manager")
