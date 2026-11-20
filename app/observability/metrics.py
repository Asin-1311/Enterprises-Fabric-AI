"""Prometheus metrics (CLAUDE.md section 14; Gap Register R-14, R-6-stack; M17a).

Served on GET /metrics (internal network only: the Helm chart does not route it outside
the cluster). Every figure the Grafana dashboards and the alerts use is defined here:

  efa_queries_total{status}                 answers by status (answered, denied, ...)
  efa_answer_seconds                        full answer time (histogram; p95 target 8 s)
  efa_first_draft_token_seconds             time to the first draft token (p95 target 1.5 s)
  efa_agent_step_seconds{agent}             time of each agent step (histogram)
  efa_llm_tokens_total{agent,model,direction}  tokens in / out
  efa_llm_cost_usd_total{agent,model}       ESTIMATED cost (token counts x list prices)
  efa_verification_total{outcome}           verification steps: passed | failed
  efa_retrieval_total{outcome}              retrieval rounds that found good evidence: hit | miss
  efa_degraded_answers_total                answers given with reduced checking
  efa_cache_lookups_total{outcome}          response cache: hit | miss
  efa_ingestion_lag_seconds{source}         change -> indexed, last event per source (gauge)
  efa_ingestion_events_total{source,outcome}
  efa_acl_disagreements_total               pre-filter allowed a chunk the policy denied
  efa_http_requests_total{status_class}     API responses: 2xx, 4xx, 5xx

Labels hold only bounded values (status, agent, model, source id): never a user, a
question or a document, so the metrics reveal nothing confidential.
"""

from prometheus_client import Counter, Gauge, Histogram

QUERIES = Counter("efa_queries", "Answers by final status", ["status"])
ANSWER_SECONDS = Histogram("efa_answer_seconds", "Full answer time (seconds)",
                           buckets=(0.5, 1, 2, 4, 6, 8, 10, 15, 20, 30, 45, 60, 120))
FIRST_DRAFT_SECONDS = Histogram("efa_first_draft_token_seconds", "Time to the first draft token (seconds)",
                                buckets=(0.25, 0.5, 0.75, 1, 1.5, 2, 3, 5, 8, 13, 20, 30))
AGENT_SECONDS = Histogram("efa_agent_step_seconds", "Agent step time (seconds)", ["agent"],
                          buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 4, 8, 16, 32, 64))
LLM_TOKENS = Counter("efa_llm_tokens", "LLM tokens", ["agent", "model", "direction"])
LLM_COST = Counter("efa_llm_cost_usd", "Estimated LLM cost in USD (token counts x list prices)",
                   ["agent", "model"])
VERIFICATIONS = Counter("efa_verification", "Verification steps", ["outcome"])
RETRIEVALS = Counter("efa_retrieval", "Retrieval rounds by whether good evidence was found", ["outcome"])
DEGRADED = Counter("efa_degraded_answers", "Answers given with reduced checking (under load)")
CACHE = Counter("efa_cache_lookups", "Response cache lookups", ["outcome"])
INGESTION_LAG = Gauge("efa_ingestion_lag_seconds", "Change to indexed, last event per source", ["source"])
INGESTION_EVENTS = Counter("efa_ingestion_events", "Change events applied", ["source", "outcome"])
ACL_DISAGREEMENTS = Counter("efa_acl_disagreements", "Chunks the pre-filter allowed but the policy denied")
HTTP_REQUESTS = Counter("efa_http_requests", "API responses by status class", ["status_class"])


def record_llm(agent: str, model: str, tokens_in: int, tokens_out: int) -> None:
    """Tokens and the estimated cost of one model call made by an agent step."""
    from eval.costs import estimate_usd

    LLM_TOKENS.labels(agent, model, "in").inc(tokens_in)
    LLM_TOKENS.labels(agent, model, "out").inc(tokens_out)
    LLM_COST.labels(agent, model).inc(estimate_usd(model, tokens_in, tokens_out))
