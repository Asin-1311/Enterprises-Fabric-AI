# Section 15 replacement: real status of every requirement

Generated from `docs/report/COMPLIANCE.md` by `python -m scripts.make_docs`; do not edit.
This table replaces the project document's Section 15 "Work Completed" table, which
marked every deliverable as Completed. A requirement is **Met** only when its code exists
and its acceptance test passes; the evidence and tests are in COMPLIANCE.md.

**44 requirements: 41 Met, 3 Partial, 0 Not met, 0 Deviation.**

## Specification section 3

| ID | Requirement | Status | Milestone |
|---|---|---|---|
| R-3-adapt | Sources, chunk granularity and ranking chosen per query | Met | M14a, M15a |
| R-3-stream | Streaming ingestion and incremental updates | Met | M12b, M12d |
| R-3-eval | Measurable evaluation that can be trusted | Partial | M18a, M18b |

## Specification section 5

| ID | Requirement | Status | Milestone |
|---|---|---|---|
| R-5-ing | Ingestion plane: connectors, CDC listeners | Met | M12a-M12d |
| R-5-serve | Rate limiting and response caching | Met | M9, M16a |
| R-5-exp | Token-by-token streaming answer | Met | M14b, M16b |
| R-5-par | Parallel processing where possible; no global lock | Met | M9, M14a |

## Specification section 6

| ID | Requirement | Status | Milestone |
|---|---|---|---|
| R-6-radix | Radix UI | Met | M16b |
| R-6-stack | OpenSearch, PostgreSQL, Kafka, Redis, Debezium, scheduler, OIDC/JWT, OPA, OTel/Prometheus/Grafana/Langfuse, Docker/K8s/Helm | Partial | M10-M17 |

## Specification section 7.1

| ID | Requirement | Status | Milestone |
|---|---|---|---|
| R-7.1-a | Owner, classification, timestamp, ACL enrichment | Met | M12a |
| R-7.1-b | Only affected chunks recomputed | Met | M12b |
| R-7.1-c | Connectors: SharePoint, Confluence, Jira, Git, S3; webhook listeners | Met | M12a, M12c |

## Specification section 7.2

| ID | Requirement | Status | Milestone |
|---|---|---|---|
| R-7.2 | Chunk granularity stored per source type | Met | M12b |

## Specification section 7.3

| ID | Requirement | Status | Milestone |
|---|---|---|---|
| R-7.3 | Analytical and open-ended intents | Met | M14a |

## Specification section 7.4

| ID | Requirement | Status | Milestone |
|---|---|---|---|
| R-7.4 | Learned routing from success, freshness, scope; decisions refine policy | Met | M15b |

## Specification section 7.6

| ID | Requirement | Status | Milestone |
|---|---|---|---|
| R-7.6 | Compression relevant to each sub-query | Met | M14a |

## Specification section 7.8

| ID | Requirement | Status | Milestone |
|---|---|---|---|
| R-7.8 | NLI model plus LLM judge; flag failures | Met | M14b |

## Specification section 7.9

| ID | Requirement | Status | Milestone |
|---|---|---|---|
| R-7.9 | Implicit signals update routing, re-ranker dataset, prompt evaluation sets | Met | M15b, M15c |

## Specification section 7.10

| ID | Requirement | Status | Milestone |
|---|---|---|---|
| R-7.10 | Admin console | Met | M11, M15c, M16c |

## Specification section 8

| ID | Requirement | Status | Milestone |
|---|---|---|---|
| R-8-bandit | Bandit allocation by observed quality | Met | M15b |
| R-8-degrade | Degrade under p95 latency pressure and tell the user | Met | M15a |
| R-8-record | Every adjustment recorded | Met | M9, M11 |

## Specification section 9

| ID | Requirement | Status | Milestone |
|---|---|---|---|
| R-9-tables | Source, Retrieval, RoutingPolicy, PromptVersion entities | Met | M11 |
| R-9-span | Citation spans | Met | M11, M14b |

## Specification section 10

| ID | Requirement | Status | Milestone |
|---|---|---|---|
| R-10-sources | `POST /sources`, `GET /sources/{id}/status` | Met | M11 |
| R-10-reindex | Re-index a document or a source | Met | M11 |
| R-10-policy | `PUT /routing-policy` | Met | M11, M15b |
| R-10-metrics | `/metrics/quality` over a time window | Met | M9 |

## Specification section 12

| ID | Requirement | Status | Milestone |
|---|---|---|---|
| R-12-opa | OPA evaluated per candidate chunk | Met | M13a |
| R-12-scan | User prompts scanned for injection | Met | M9 |
| R-12-retention | Trace retention schedule | Met | M13a |
| R-12-model | Data-retention terms and private model for classified sources | Partial | M13a |
| R-12-auth | OIDC/JWT; no open registration | Met | M9, M13b |

## Specification section 13.1

| ID | Requirement | Status | Milestone |
|---|---|---|---|
| R-13.1-sets | Per-source evaluation sets; regression tolerance | Met | M17c, M18a |

## Specification section 13.2

| ID | Requirement | Status | Milestone |
|---|---|---|---|
| R-13.2-judge | Answer relevance; calibrated judge; weekly review | Met | M18a, M18b |

## Specification section 13.3

| ID | Requirement | Status | Milestone |
|---|---|---|---|
| R-13.3-replay | Replay tests before activation | Met | M15b, M15c, M17c, M18b |
| R-13.3-load | k6 and Locust load tests against latency targets | Met | M18c |
| R-13.3-contract | Contract tests for connectors and API | Met | M11, M12c |

## Specification section 14

| ID | Requirement | Status | Milestone |
|---|---|---|---|
| R-14 | Containers, Kubernetes, Helm, CI/CD, scans, dashboards, alerts | Met | M17a, M17b, M17c |

## Specification section 15 (#1)

| ID | Requirement | Status | Milestone |
|---|---|---|---|
| R-15-req | Requirements specification: use cases, enterprise source inventory, quality targets | Met | M19 |

## Specification section 15 (#2

| ID | Requirement | Status | Milestone |
|---|---|---|---|
| R-15-docs | Architecture design, API reference, operations guide | Met | M17c, M19 |

## Specification section audit

| ID | Requirement | Status | Milestone |
|---|---|---|---|
| R-ops-clone | Runnable from a clean clone | Met | M9, M17c |
| R-ops-integrity | No edited evaluation outputs; honest Section 15 | Met | M9, M19 |

## Specification section audit (M20)

| ID | Requirement | Status | Milestone |
|---|---|---|---|
| R-ops-secrets | No secrets in committed run snapshots | Met | M20 |

## Not met in full, and why

Every requirement that is not Met, with the reason from COMPLIANCE.md (shortened).

- **R-3-eval (Partial)**: M20: the 12 sample questions were reviewed by the author on 2026-11-22, after the answer runs, with no change to any gold answer (`docs/review/sample_questions.md`), so those runs stand; their own files still record 0 reviewed, as it was when they ran. Kept Partial for those reasons: 12 of 147 reviewed.
- **R-6-stack (Partial)**: Remaining for Met: an automated test of the Prefect deployment itself.
- **R-12-model (Partial)**: Not yet: (1) the provider's data-retention terms are NOT confirmed by the author, so no zero-retention claim is made; (2) no live private model: Ollama is not installed, and the 7.3 GB laptop cannot run a local model beside the full stack; the adapter is tested against a mock Ollama server. M16c: administrators can mark more sources or paths `restricted` with classification rules (admin console); the rule updates documents and index payloads at once, so the model router sends that text only to the private model from the next question.
