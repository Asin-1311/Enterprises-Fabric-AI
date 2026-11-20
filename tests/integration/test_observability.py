"""M17a on the real servers: the alert rules (promtool), Prometheus scraping a live API,
Grafana's provisioned dashboard, and Langfuse receiving a question's spans as an LLM call
log. Needs `docker compose --profile observability up -d` (see deploy/compose.yaml) and
`python -m scripts.observability_setup`; skipped otherwise.

Covers: R-14 (dashboards load, alerts tested, metrics scraped), R-6-stack (OTel,
Prometheus, Grafana, Langfuse).
"""

import socket
import subprocess
import threading
import time
import uuid

import httpx
import pytest

from app.config import PROJECT_ROOT, env_reference

pytestmark = pytest.mark.integration

PROMETHEUS, GRAFANA, LANGFUSE = "http://127.0.0.1:9090", "http://127.0.0.1:3001", "http://127.0.0.1:3000"
PROM_IMAGE = "prom/prometheus:v3.15.1"


def _up(url: str, path: str) -> bool:
    try:
        return httpx.get(url + path, timeout=3).status_code < 500
    except httpx.HTTPError:
        return False


def _need(url: str, path: str, name: str) -> None:
    if not _up(url, path):
        pytest.skip(f"{name} is not running: docker compose --env-file .env -f deploy/compose.yaml "
                    "--profile observability up -d")


def _promtool(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", "run", "--rm", "--entrypoint", "promtool", "-v",
                           f"{PROJECT_ROOT / 'deploy' / 'prometheus'}:/rules:ro", "-w", "/rules", PROM_IMAGE, *args],
                          capture_output=True, text=True, timeout=300)


def test_alert_rules_are_valid_and_each_alert_fires_on_its_threshold() -> None:
    """Covers: R-14 (alerts): promtool checks alerts.yaml and runs alerts_test.yaml, where
    each alert fires on a series that breaks its threshold and not on one that does not."""
    check = _promtool("check", "rules", "alerts.yaml")
    assert check.returncode == 0 and "6 rules found" in check.stdout, check.stdout + check.stderr
    tested = _promtool("test", "rules", "alerts_test.yaml")
    assert tested.returncode == 0 and "SUCCESS" in tested.stdout, tested.stdout + tested.stderr


def test_compose_profiles_need_no_observability_secrets(tmp_path) -> None:
    """The full profile must still start for someone who never set up observability."""
    env = tmp_path / "ci-check.env"
    env.write_text("POSTGRES_PASSWORD=x\nMINIO_ROOT_USER=x\nMINIO_ROOT_PASSWORD=xxxxxxxx\n"
                   "KEYCLOAK_ADMIN_PASSWORD=x\n", encoding="utf-8")
    result = subprocess.run(["docker", "compose", "--env-file", str(env), "-f",
                             str(PROJECT_ROOT / "deploy" / "compose.yaml"), "--profile", "full", "config", "-q"],
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr


def test_prometheus_loads_the_alerts_and_scrapes_a_live_api(api_on_port_8000) -> None:
    """Covers: R-14 (metrics scraped): Prometheus has the six alert rules and scrapes the
    API's /metrics."""
    _need(PROMETHEUS, "/-/ready", "Prometheus")
    rules = httpx.get(f"{PROMETHEUS}/api/v1/rules", timeout=10).json()["data"]["groups"]
    names = {r["name"] for g in rules for r in g["rules"]}
    assert names == {"AnswerLatencyAboveTarget", "FirstTokenLatencyAboveTarget", "VerificationFailureRateHigh",
                     "IngestionLagHigh", "AccessControlDisagreement", "ApiErrorRateHigh"}
    up: dict = {}
    deadline = time.time() + 60
    while time.time() < deadline:
        up = httpx.get(f"{PROMETHEUS}/api/v1/query", params={"query": 'up{job="api"}'}, timeout=10).json()
        if up["data"]["result"] and up["data"]["result"][0]["value"][1] == "1":
            break
        time.sleep(5)
    else:
        pytest.fail(f"Prometheus never scraped the API: {up}")
    deadline = time.time() + 40
    while time.time() < deadline:  # the series appears after the next scrape
        series = httpx.get(f"{PROMETHEUS}/api/v1/query", params={"query": "efa_http_requests_total"},
                           timeout=10).json()
        if series["data"]["result"]:
            break
        time.sleep(5)
    assert series["data"]["result"], "no efa_http_requests_total series scraped"


def test_grafana_provisions_the_dashboard() -> None:
    """Covers: R-14 (dashboards load)."""
    _need(GRAFANA, "/api/health", "Grafana")
    auth = ("admin", env_reference("env:GRAFANA_ADMIN_PASSWORD") or "")
    found = httpx.get(f"{GRAFANA}/api/search", params={"query": "Enterprise Fabric AI"}, auth=auth, timeout=10)
    assert found.status_code == 200, found.text
    assert any(d["uid"] == "enterprise-fabric" for d in found.json())
    dash = httpx.get(f"{GRAFANA}/api/dashboards/uid/enterprise-fabric", auth=auth, timeout=10).json()["dashboard"]
    assert len(dash["panels"]) == 13
    datasource = httpx.get(f"{GRAFANA}/api/datasources/uid/prometheus/health", auth=auth, timeout=10)
    assert datasource.status_code == 200 and datasource.json()["status"] == "OK", datasource.text


def test_langfuse_receives_the_model_calls_of_a_question_with_prompt_versions() -> None:
    """Covers: R-6-stack (Langfuse): the same spans the API exports arrive in Langfuse as a
    trace with a generation carrying model, tokens, masked content and the prompt version."""
    _need(LANGFUSE, "/api/public/health", "Langfuse")
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor

    from app.agents.llm import LLMResult
    from app.observability import otel
    from tests.test_agents import _settings

    public, secret = env_reference("env:LANGFUSE_PUBLIC_KEY"), env_reference("env:LANGFUSE_SECRET_KEY")
    if not (public and secret):
        pytest.skip("run python -m scripts.observability_setup")
    provider = TracerProvider(resource=Resource.create({"service.name": "efa-integration-test"}))
    provider.add_span_processor(SimpleSpanProcessor(OTLPSpanExporter(
        endpoint=f"{LANGFUSE}/api/public/otel/v1/traces", headers=otel._headers(_settings()))))
    marker = 900_000 + uuid.uuid4().int % 99_999
    saved = otel.tracer
    otel.tracer = lambda: provider.get_tracer("integration")  # this test's own provider
    try:
        root = otel.QuerySpan(marker, "staff")
        root.step("synthesis", latency_ms=1200, versions={"prompt_version_id": 7, "policy_version_id": 1},
                  llm=[LLMResult("ok", None, "claude-sonnet-5-5", "medium", 1500, 300, 1100.0)],
                  step_input={"question": "masked [PAN] question"}, step_output={"text": "an answer"},
                  include_content=True)
        root.end("answered")
        provider.force_flush()
    finally:
        otel.tracer = saved
    trace_id = format(root.span.get_span_context().trace_id, "032x")
    last = None
    deadline = time.time() + 120
    while time.time() < deadline:  # Langfuse ingests asynchronously (worker -> ClickHouse)
        last = httpx.get(f"{LANGFUSE}/api/public/traces/{trace_id}", auth=(public, secret), timeout=10)
        if last.status_code == 200 and last.json().get("observations"):
            break
        time.sleep(5)
    else:
        pytest.fail(f"trace {trace_id} not in Langfuse: {last.status_code} {last.text[:300]}")
    generations = []
    while time.time() < deadline + 60 and not generations:  # its spans arrive one by one
        observations = httpx.get(f"{LANGFUSE}/api/public/traces/{trace_id}", auth=(public, secret),
                                 timeout=10).json().get("observations", [])
        generations = [o for o in observations if o["type"] == "GENERATION"]
        if not generations:
            time.sleep(5)
    assert generations, observations
    generation = generations[0]
    assert generation["model"] == "claude-sonnet-5-5"
    usage = generation.get("usageDetails") or generation.get("usage") or {}
    assert usage.get("input") == 1500 and usage.get("output") == 300, usage
    assert "[PAN]" in str(generation["input"])
    assert generation["metadata"]["attributes"]["efa.prompt_version_id"] == "7"


@pytest.fixture()
def api_on_port_8000(tmp_path):
    """The API (lite, fake model) on 127.0.0.1:8000, where Prometheus scrapes it."""
    with socket.socket() as s:
        if s.connect_ex(("127.0.0.1", 8000)) == 0:
            pytest.skip("port 8000 is in use (is the demo API running?)")
    import uvicorn

    from app.agents.graph import AgentDeps, AuditAssistant
    from app.agents.query import ClientMatcher
    from app.api.main import Services, create_app
    from app.concurrency import ReadWriteLock
    from tests.helpers import make_indexer, make_searcher
    from tests.test_agents import FakeLLM, FakeReranker, _settings
    from tests.test_clean_clone import _clean_clone

    indexer = make_indexer(_clean_clone(tmp_path), synthetic_fallback=True)
    indexer.sync(actor="test")
    with indexer.sessions() as s:
        clients = ClientMatcher.from_db(s)
    assistant = AuditAssistant(AgentDeps(settings=_settings(), llm=FakeLLM(), searcher=make_searcher(indexer),
                                         reranker=FakeReranker(), sessions=indexer.sessions, clients=clients))
    app = create_app(Services(_settings(), assistant, indexer, ReadWriteLock()), static_dir=None)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8000, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(50):
        if _up("http://127.0.0.1:8000", "/api/v1/health"):
            break
        time.sleep(0.2)
    httpx.get("http://127.0.0.1:8000/api/v1/health", timeout=5)  # one counted API response
    yield
    server.should_exit = True
    thread.join(timeout=10)
