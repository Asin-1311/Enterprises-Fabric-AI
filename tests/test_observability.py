"""M17a: Prometheus metrics, OpenTelemetry traces (Langfuse's LLM call log), the dashboard
and the performance figures from the metrics backend. Lite profile; the servers
(Prometheus, Grafana, Langfuse) are tested in tests/integration/test_observability.py.

Covers: R-14 (metrics, traces, dashboards, alerts: the lite half), R-6-stack (OTel).
"""

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from prometheus_client import REGISTRY

from app.agents.llm import LLMResult
from app.config import PROJECT_ROOT
from app.observability import otel
from app.observability.performance import prometheus_performance
from tests.test_agents import _settings
from tests.test_api import as_user, client, parse_sse  # noqa: F401  (the API fixture)

QUESTION = "Within how many days must the final audit file be assembled?"
_exporter = InMemorySpanExporter()


def sample(name: str, **labels) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


@pytest.fixture(scope="module")
def spans():
    """One tracer provider for this process (OpenTelemetry allows one), exporting to memory."""
    if not otel.tracing_enabled():
        otel.setup_tracing(_settings(), exporter=_exporter)
    return _exporter


def ask(c, text: str, user: str = "S1") -> dict:
    r = c.post("/api/v1/query", json={"text": text}, headers=as_user(c, user))
    assert r.status_code == 200
    return parse_sse(r.text)[-1][1]


# ------------------------------------------------------------------- metrics

def test_metrics_count_answers_steps_tokens_and_verification(client) -> None:  # noqa: F811
    """Covers: R-14 (the figures the dashboards and alerts read come from /metrics)."""
    before = {
        "answered": sample("efa_queries_total", status="answered"),
        "synth": sample("efa_agent_step_seconds_count", agent="synthesis"),
        "tokens": sample("efa_llm_tokens_total", agent="synthesis", model="fake-synth", direction="out"),
        "verified": sample("efa_verification_total", outcome="passed") + sample("efa_verification_total", outcome="failed"),
        "retrieval": sample("efa_retrieval_total", outcome="hit") + sample("efa_retrieval_total", outcome="miss"),
        "ttft": sample("efa_first_draft_token_seconds_count"),
        "ok": sample("efa_http_requests_total", status_class="2xx"),
        "answer_time": sample("efa_answer_seconds_count"),
    }
    final = ask(client, QUESTION)
    assert final["status"] == "answered"
    assert sample("efa_queries_total", status="answered") == before["answered"] + 1
    assert sample("efa_agent_step_seconds_count", agent="synthesis") == before["synth"] + 1
    assert sample("efa_llm_tokens_total", agent="synthesis", model="fake-synth", direction="out") \
        == before["tokens"] + 5  # FakeLLM reports 5 output tokens per call
    assert sample("efa_verification_total", outcome="passed") + sample("efa_verification_total", outcome="failed") \
        == before["verified"] + 1
    assert sample("efa_retrieval_total", outcome="hit") + sample("efa_retrieval_total", outcome="miss") \
        > before["retrieval"]
    assert sample("efa_first_draft_token_seconds_count") == before["ttft"] + 1
    assert sample("efa_answer_seconds_count") == before["answer_time"] + 1
    assert sample("efa_http_requests_total", status_class="2xx") > before["ok"]

    scrape = client.get("/metrics")
    assert scrape.status_code == 200 and "efa_queries_total" in scrape.text
    # Labels carry bounded values only: no user, question or document text.
    for secret in ("S1", "final audit file", "POL-NEW", "s1@test.example"):
        assert secret not in scrape.text
    assert client.get("/openapi.json").json()["paths"].get("/metrics") is None  # not part of the API


def test_metric_names_used_by_the_dashboard_and_alerts_exist() -> None:
    """Every efa_* series the dashboard and the alerts query is one the code defines, and
    the committed dashboard is what scripts/make_grafana_dashboard.py writes."""
    from scripts.make_grafana_dashboard import OUT, build

    assert json.loads(OUT.read_text(encoding="utf-8")) == build()
    defined = {m.name for m in REGISTRY.collect() if m.name.startswith("efa_")}
    used = set(re.findall(r"efa_[a-z_]+", OUT.read_text(encoding="utf-8")
                          + (PROJECT_ROOT / "deploy" / "prometheus" / "alerts.yaml").read_text(encoding="utf-8")))
    base = {re.sub(r"_(total|bucket|count|sum)$", "", n) for n in used}
    assert base <= defined, base - defined
    panels = {p["title"] for p in build()["panels"]}
    for needed in ("Questions per minute", "Latency per agent, p95", "Tokens per minute", "Cost per answer",
                   "Retrieval hit rate", "Verification failure rate", "Degradation rate", "Cache hit rate",
                   "Ingestion lag per source"):
        assert any(t.startswith(needed) for t in panels), needed


def test_ingestion_lag_is_measured_per_source(tmp_path) -> None:
    from app.ingest.events import DocumentChanged
    from app.ingest.indexer import _record_ingestion

    ten_minutes_ago = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(timespec="seconds")
    event = DocumentChanged(source_id="git-policies", doc_id="D1", uri="x", op="upsert",
                            content_hash="h", occurred_at=ten_minutes_ago)
    _record_ingestion([event], [(event.event_id, "indexed")])
    assert 590 <= sample("efa_ingestion_lag_seconds", source="git-policies") <= 660
    assert sample("efa_ingestion_events_total", source="git-policies", outcome="indexed") >= 1


# ------------------------------------------------------------------- traces

def test_each_question_is_one_trace_with_agent_and_model_call_spans(client, spans) -> None:  # noqa: F811
    """Covers: R-14, R-6-stack (OpenTelemetry). The root span names the query; each agent
    step is a child; each model call is a `generation` (what Langfuse shows) with tokens,
    model and prompt version, and only MASKED content."""
    spans.clear()
    final = ask(client, "Within how many days must the final audit file be assembled for ABCDE1234F?")
    otel.shutdown()  # flush
    got = spans.get_finished_spans()
    roots = [s for s in got if s.name == "query" and s.attributes["efa.query_id"] == final["query_id"]]
    assert len(roots) == 1
    root = roots[0]
    steps = [s for s in got if s.name.startswith("agent.") and s.parent and s.parent.span_id == root.context.span_id]
    assert {"agent.query_understanding", "agent.synthesis", "agent.verification"} <= {s.name for s in steps}
    synth_step = next(s for s in steps if s.name == "agent.synthesis")
    calls = [s for s in got if s.name == "llm.synthesis" and s.parent.span_id == synth_step.context.span_id]
    assert len(calls) == 1
    call = calls[0].attributes
    assert call["langfuse.observation.type"] == "generation"
    assert (call["gen_ai.usage.input_tokens"], call["gen_ai.usage.output_tokens"]) == (10, 5)
    assert call["gen_ai.request.model"] == "fake-synth" and "efa.prompt_version_id" in call
    assert not any(k.startswith("langfuse.observation.prompt") for k in call)  # Langfuse rejects those
    understanding = next(s for s in got if s.name == "llm.query_understanding" and s.context.trace_id == root.context.trace_id)
    assert "[PAN]" in understanding.attributes["langfuse.observation.input"]
    assert "ABCDE1234F" not in json.dumps([dict(s.attributes) for s in got])
    assert root.attributes["efa.status"] == final["status"]


def test_private_model_calls_never_carry_content(spans) -> None:
    spans.clear()
    root = otel.QuerySpan(999)
    restricted = LLMResult("secret text", None, "ollama:llama", "n/a", 100, 20, 50.0)
    external = LLMResult("ok", None, "claude-sonnet-5-5", "medium", 100, 20, 50.0)
    root.step("synthesis", latency_ms=60, llm=[restricted, external], versions={"prompt_version_id": 3},
              step_input={"evidence": "restricted clause"}, step_output={"text": "answer"}, include_content=True)
    root.end("answered")
    otel.shutdown()
    calls = {s.attributes["gen_ai.request.model"]: s.attributes for s in spans.get_finished_spans()
             if s.name == "llm.synthesis"}
    assert "langfuse.observation.input" not in calls["ollama:llama"]
    assert calls["ollama:llama"]["gen_ai.system"] == "ollama"
    assert "restricted clause" in calls["claude-sonnet-5-5"]["langfuse.observation.input"]


def test_without_an_endpoint_tracing_is_off() -> None:
    saved = otel._provider
    otel._provider = None
    try:
        assert otel.setup_tracing(_settings()) is None and not otel.tracing_enabled()
    finally:
        otel._provider = saved


def test_langfuse_keys_are_sent_as_basic_auth_from_env_references(monkeypatch) -> None:
    import base64

    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    header = otel._headers(_settings())["Authorization"]
    assert base64.b64decode(header.removeprefix("Basic ")).decode() == "pk-lf-test:sk-lf-test"


# ------------------------------------------------------- performance from Prometheus

PROM_RESULTS = {
    "sum by (status) (increase(efa_queries_total[": [({"status": "answered"}, "8"), ({"status": "denied"}, "2")],
    'sum(increase(efa_verification_total[': [({}, "8")],
    'sum(increase(efa_verification_total{outcome="failed"}': [({}, "2")],
    "sum(increase(efa_degraded_answers_total[": [({}, "1")],
    'sum(increase(efa_cache_lookups_total{outcome="hit"}': [({}, "3")],
    "sum(increase(efa_llm_cost_usd_total[": [({}, "0.05")],
    "sum by (agent) (increase(efa_agent_step_seconds_count[": [({"agent": "synthesis"}, "8")],
    "histogram_quantile(0.5,": [({"agent": "synthesis"}, "2.5")],
    "histogram_quantile(0.95,": [({"agent": "synthesis"}, "6.0")],
    "sum by (agent, direction) (increase(efa_llm_tokens_total[": [({"agent": "synthesis", "direction": "in"}, "9000"),
                                                                 ({"agent": "synthesis", "direction": "out"}, "2400")],
}


def fake_prometheus(seen: list[str]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        query = request.url.params["query"]
        seen.append(query)
        rows = next((r for prefix, r in PROM_RESULTS.items() if query.startswith(prefix)), [])
        return httpx.Response(200, json={"status": "success", "data": {"resultType": "vector", "result": [
            {"metric": m, "value": [0, v]} for m, v in rows]}})
    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://prom.test")


def test_performance_figures_from_prometheus() -> None:
    """Covers: R-14 (the console reads the metrics backend)."""
    seen: list[str] = []
    end = datetime(2026, 11, 20, 12, 0)
    perf = prometheus_performance("http://prom.test", end - timedelta(days=7), end, client=fake_prometheus(seen))
    assert perf["source"] == "prometheus" and perf["answers"] == 10
    assert perf["answers_by_status"] == {"answered": 8, "denied": 2}
    assert perf["verification_failure_rate"] == 0.25 and perf["degraded_rate"] == 0.1
    assert perf["cache_hit_rate"] == 0.3 and perf["estimated_cost_per_answer_usd"] == 0.005
    assert perf["agents"] == [{"agent": "synthesis", "steps": 8, "latency_p50_ms": 2500.0,
                               "latency_p95_ms": 6000.0, "tokens_in": 9000, "tokens_out": 2400}]
    assert all("[604800s]" in q for q in seen if "increase(" in q)  # the 7-day window


def test_the_console_falls_back_to_traces_when_prometheus_is_unreachable(client) -> None:  # noqa: F811
    settings = client.app.state.services.settings
    saved = settings.prometheus_url
    settings.prometheus_url = "http://127.0.0.1:9"  # nothing listens there
    try:
        r = client.get("/api/v1/admin/agents/performance", headers=as_user(client, "A1"))
        assert r.status_code == 200 and r.json()["source"] == "traces"
    finally:
        settings.prometheus_url = saved


def test_observability_profile_files_exist() -> None:
    deploy = PROJECT_ROOT / "deploy"
    compose = (deploy / "compose.yaml").read_text(encoding="utf-8")
    for service in ("prometheus:", "grafana:", "clickhouse:", "langfuse-web:", "langfuse-worker:"):
        assert f"\n  {service}" in compose, service
    for rel in ("prometheus/prometheus.yml", "prometheus/alerts.yaml", "prometheus/alerts_test.yaml",
                "grafana/provisioning/datasources/prometheus.yaml",
                "grafana/provisioning/dashboards/provider.yaml", "grafana/dashboards/enterprise-fabric.json"):
        assert (deploy / rel).is_file(), rel


def test_setup_appends_only_missing_secrets(tmp_path: Path) -> None:
    from scripts.observability_setup import GENERATED, ensure_secrets

    env = tmp_path / ".env"
    env.write_text("POSTGRES_PASSWORD=keep\nGRAFANA_ADMIN_PASSWORD=mine\n", encoding="utf-8")
    added = ensure_secrets(env)
    assert "GRAFANA_ADMIN_PASSWORD" not in added and set(added) == set(GENERATED) - {"GRAFANA_ADMIN_PASSWORD"}
    text = env.read_text(encoding="utf-8")
    assert "POSTGRES_PASSWORD=keep" in text and "GRAFANA_ADMIN_PASSWORD=mine" in text
    assert re.search(r"^LANGFUSE_ENCRYPTION_KEY=[0-9a-f]{64}$", text, re.M)
    assert ensure_secrets(env) == []  # nothing changes the second time
