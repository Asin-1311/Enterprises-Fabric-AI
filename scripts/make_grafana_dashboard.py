"""Write the Grafana dashboard deploy/grafana/dashboards/enterprise-fabric.json (M17a).

    .venv\\Scripts\\python.exe -m scripts.make_grafana_dashboard

The panels CLAUDE.md section 14 asks for, each one PromQL over the metrics in
app/observability/metrics.py: query volume, latency per agent (p50/p95), answer time and
time to first token against their targets, token use, cost per answer (estimate),
retrieval hit rate, verification failure rate, degradation rate, cache hit rate,
ingestion lag per source, access-control disagreements and the API error rate.
Generated rather than hand-edited, so every query can be read in one place.
"""

import json
import sys

from app.config import PROJECT_ROOT

OUT = PROJECT_ROOT / "deploy" / "grafana" / "dashboards" / "enterprise-fabric.json"
DS = {"type": "prometheus", "uid": "prometheus"}

# (title, unit, [(PromQL, legend)], width, kind)
PANELS = [
    ("Questions per minute, by outcome", "short",
     [("sum by (status) (rate(efa_queries_total[5m])) * 60", "{{status}}")], 12, "timeseries"),
    ("Answer time p95 (target 8 s) and first draft token p95 (target 1.5 s)", "s",
     [("histogram_quantile(0.95, sum by (le) (rate(efa_answer_seconds_bucket[10m])))", "full answer p95"),
      ("histogram_quantile(0.95, sum by (le) (rate(efa_first_draft_token_seconds_bucket[10m])))", "first draft token p95")],
     12, "timeseries"),
    ("Latency per agent, p50", "s",
     [("histogram_quantile(0.5, sum by (le, agent) (rate(efa_agent_step_seconds_bucket[10m])))", "{{agent}}")],
     12, "timeseries"),
    ("Latency per agent, p95", "s",
     [("histogram_quantile(0.95, sum by (le, agent) (rate(efa_agent_step_seconds_bucket[10m])))", "{{agent}}")],
     12, "timeseries"),
    ("Tokens per minute, by agent", "short",
     [("sum by (agent, direction) (rate(efa_llm_tokens_total[5m])) * 60", "{{agent}} {{direction}}")], 12, "timeseries"),
    ("Cost per answer, last hour (ESTIMATE: tokens x list prices)", "currencyUSD",
     [("sum(increase(efa_llm_cost_usd_total[1h])) / sum(increase(efa_queries_total[1h]))", "USD per answer")],
     6, "stat"),
    ("Retrieval hit rate (good evidence found)", "percentunit",
     [('sum(rate(efa_retrieval_total{outcome="hit"}[30m])) / sum(rate(efa_retrieval_total[30m]))', "hit rate")],
     6, "stat"),
    ("Verification failure rate (alert above 20%)", "percentunit",
     [('sum(rate(efa_verification_total{outcome="failed"}[30m])) / sum(rate(efa_verification_total[30m]))', "failure rate")],
     6, "stat"),
    ("Degradation rate (reduced checking)", "percentunit",
     [('sum(rate(efa_degraded_answers_total[30m])) / sum(rate(efa_queries_total{status="answered"}[30m]))', "degraded")],
     6, "stat"),
    ("Cache hit rate", "percentunit",
     [('sum(rate(efa_cache_lookups_total{outcome="hit"}[30m])) / sum(rate(efa_cache_lookups_total[30m]))', "hit rate")],
     6, "stat"),
    ("Ingestion lag per source (alert above 15 min)", "s",
     [("max by (source) (efa_ingestion_lag_seconds)", "{{source}}")], 12, "timeseries"),
    ("Access-control disagreements (must stay 0)", "short",
     [("sum(increase(efa_acl_disagreements_total[1h]))", "last hour")], 6, "stat"),
    ("API server error rate (alert above 5%)", "percentunit",
     [('sum(rate(efa_http_requests_total{status_class="5xx"}[10m])) / sum(rate(efa_http_requests_total[10m]))', "5xx share")],
     6, "stat"),
]


def build() -> dict:
    panels, x, y, row_h = [], 0, 0, 8
    for pid, (title, unit, targets, width, kind) in enumerate(PANELS, start=1):
        if x + width > 24:
            x, y = 0, y + row_h
        panels.append({
            "id": pid, "type": kind, "title": title, "datasource": DS,
            "gridPos": {"x": x, "y": y, "w": width, "h": row_h},
            "fieldConfig": {"defaults": {"unit": unit}, "overrides": []},
            "options": {"reduceOptions": {"calcs": ["lastNotNull"]}} if kind == "stat" else {},
            "targets": [{"refId": chr(65 + i), "datasource": DS, "expr": expr, "legendFormat": legend}
                        for i, (expr, legend) in enumerate(targets)],
        })
        x += width
    return {"uid": "enterprise-fabric", "title": "Enterprise Fabric AI", "tags": ["enterprise-fabric"],
            "timezone": "browser", "schemaVersion": 39, "version": 1, "refresh": "30s",
            "time": {"from": "now-6h", "to": "now"}, "panels": panels}


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(build(), indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUT.relative_to(PROJECT_ROOT)} ({len(PANELS)} panels).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
