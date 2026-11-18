# Enterprise Fabric AI: specification compliance

The single source of truth for the status of every requirement (CLAUDE.md section 16).
The PDF's Section 15 table is regenerated from this file before submission.

**Rules**
- A row becomes **Met** only in the same commit that adds its passing acceptance test.
- That test names the requirement in its docstring (`Covers: R-...`).
- `scripts/check_compliance.py` fails if a Met row has no such test, if the test is
  skipped, or if the summary below does not match the rows.
- **Partial** means some of the requirement exists. The notes say what is missing and
  which milestone adds it.
- Statuses start from the independent audit of 2026-11-18 (CLAUDE.md Appendix A).

Last updated: M9 (2026-11-18).

<!-- summary: written by hand, verified by scripts/check_compliance.py -->
**Summary: 43 requirements: 2 Met, 28 Partial, 13 Not met, 0 Deviation.**

| ID | Spec § | Requirement | Status | Evidence | Acceptance test | Milestone | Notes |
|---|---|---|---|---|---|---|---|
| R-3-adapt | 3, 8 | Sources, chunk granularity and ranking chosen per query | Partial | `app/agents/router.py`, `app/retrieval/recency.py` | Different intents produce different retriever weights, candidate counts and chunk levels, recorded in `retrievals` | M14, M15 | Sources are chosen by question type (rules), with recency and superseded multipliers. Retriever weights, candidate counts and chunk level do not vary by intent yet. |
| R-3-stream | 3, 5 | Streaming ingestion and incremental updates | Partial | `app/ingest/watcher.py`, `app/ingest/indexer.py` | A DMS row update reaches both indexes via Debezium and Kafka within 60 s (integration) | M12 | A folder watcher re-indexes changed files in both indexes. No Kafka or Debezium. |
| R-3-eval | 3, 13 | Measurable evaluation that can be trusted | Partial | `eval/`, `eval/results/20261113-101102_answers/`, `eval/results/20261114-181537_calibration/` | Reviewed question count reported; judge kappa reported; two runs with spread | M18 | 0 of 63 questions reviewed by the author; the judge is not validated (kappa -0.042 on 30 labels); only one answer run, so no spread. |
| R-5-ing | 5 | Ingestion plane: connectors, CDC listeners | Partial | `app/ingest/watcher.py` | One test per connector (Section 6) | M12 | Filesystem only. |
| R-5-serve | 5, 10 | Rate limiting and response caching | Partial | `app/api/ratelimit.py`, `app/api/auth.py` | 429 after the limit; cache never crosses access fingerprints | M9, M16 | M9 added per-IP limits on the sign-in routes (5 a minute; lockout after 10 failures in 15 minutes): `tests/test_auth.py::test_sign_in_routes_allow_five_calls_a_minute_per_ip`, `::test_ten_failed_sign_ins_in_fifteen_minutes_lock_the_ip_out`. The `/query` limit and the response cache come in M16. |
| R-5-exp | 5, 11 | Token-by-token streaming answer | Partial | `app/api/main.py` | First `draft_token` event arrives before verification starts | M14, M16 | Agent progress streams live; the answer text is streamed only after verification. |
| R-5-par | 5 | Parallel processing where possible; no global lock | Partial | `app/concurrency.py`, `app/api/main.py` | Sub-query searches overlap in time; two `/query` calls overlap | M9, M14 | M9 replaced the global question lock with a readers-writer lock, so questions run together and only index writes run alone: `tests/test_api.py::test_two_questions_are_answered_at_the_same_time`, `tests/test_concurrency.py`. Sub-query searches still run one after another (M14). In the lite profile, embedded-Qdrant search calls are serialised per call (milliseconds). |
| R-6-radix | 6 | Radix UI | Not met | | UI uses Radix primitives; Vitest passes | M16 | React, Tailwind and lucide icons; no Radix yet. |
| R-6-stack | 6 | OpenSearch, PostgreSQL, Kafka, Redis, Debezium, scheduler, OIDC/JWT, OPA, OTel/Prometheus/Grafana/Langfuse, Docker/K8s/Helm | Not met | | Each runs in `full` and has an integration test | M10-M17 | Only the lite stack exists: SQLite, embedded Qdrant, `rank_bm25`, local sign-in. |
| R-7.1-a | 7.1 | Owner, classification, timestamp, ACL enrichment | Partial | `data/manifest.csv`, `app/ingest/indexer.py` | Enriched fields present on every document and index payload | M12 | Access level, engagement and effective date are carried; owner and classification are not. |
| R-7.1-b | 7.1 | Only affected chunks recomputed | Partial | `app/ingest/indexer.py` | One-section edit re-embeds only that section's chunks | M12 | Only changed documents are re-indexed, but the whole document is re-embedded. |
| R-7.1-c | 7.1 | Connectors: SharePoint, Confluence, Jira, Git, S3; webhook listeners | Not met | | Fixture contract tests (SharePoint, Confluence, Jira); integration (Git, S3, webhook) | M12 | |
| R-7.2 | 7.2 | Chunk granularity stored per source type | Partial | `app/ingest/chunker.py` | `chunking_profile` recorded on every chunk | M12 | Structure-aware chunking per source type exists; the profile is not recorded on chunks. |
| R-7.3 | 7.3 | Analytical and open-ended intents | Partial | `app/agents/query.py` | Classifier tests for all seven intents | M14 | Five intents; analytical and open-ended are missing. |
| R-7.4 | 7.4 | Learned routing from success, freshness, scope; decisions refine policy | Partial | `app/agents/router.py` | Bandit converges on a simulated reward set (seeded); new policy version created from logs | M15 | Rule-based routing with an LLM tie-break; nothing is learned. |
| R-7.6 | 7.6 | Compression relevant to each sub-query | Partial | `app/agents/reranker.py`, `app/retrieval/compress.py` | Both sides of a comparison survive re-ranking and compression | M14 | Re-ranking runs over the merged candidates, not per sub-query. |
| R-7.8 | 7.8 | NLI model plus LLM judge; flag failures | Partial | `app/agents/verifier.py` | NLI contradiction flags a claim the LLM accepted | M14 | LLM verification per claim; no NLI model. |
| R-7.9 | 7.9 | Implicit signals update routing, re-ranker dataset, prompt evaluation sets | Partial | `app/api/main.py` (`POST /feedback`) | Signals change bandit rewards; dataset export contains labelled pairs | M15 | Explicit feedback is stored only; there are no implicit signals. |
| R-7.10 | 7.10 | Admin console | Not met | | Admin can register a source, inspect a trace, roll back a prompt (UI + API tests) | M16 | |
| R-8-bandit | 8 | Bandit allocation by observed quality | Not met | | Allocation shifts toward the rewarded arm in a seeded simulation | M15 | |
| R-8-degrade | 8 | Degrade under p95 latency pressure and tell the user | Not met | | Forced high p95 reduces candidates, skips verification only for low-risk, shows the notice | M15 | |
| R-8-record | 8 | Every adjustment recorded | Partial | `app/agents/graph.py` (`score_records`), `app/retrieval/recency.py` | Multipliers and weights stored per chunk in `retrievals` | M9, M14 | M9: every retrieval trace stores, per chunk, the fused score, each retriever's rank, every named multiplier and the final score: `tests/test_agents.py::test_retrieval_trace_records_every_score_multiplier`. The `retrievals` table that the acceptance test names comes in M11. |
| R-9-tables | 9 | Source, Retrieval, RoutingPolicy, PromptVersion entities | Not met | | Migrations create them; API tests use them | M11 | |
| R-9-span | 9 | Citation spans | Partial | `app/db/models.py` (`citations`) | `span_start`/`span_end` point to the supporting text | M11, M14 | Citations name the chunk, not the character span. |
| R-10-sources | 10 | `POST /sources`, `GET /sources/{id}/status` | Partial | `app/api/main.py` (`GET /sources/status`) | Contract tests | M11 | Audited "Not met / Partial". Counts per source type exist; no source registration or per-source status. |
| R-10-reindex | 10 | Re-index a document or a source | Partial | `app/api/main.py` (`POST /documents/reindex`) | Targeted re-index touches only that document's or source's chunks | M11 | Full re-index only (partner-only, logged). |
| R-10-policy | 10 | `PUT /routing-policy` | Not met | | Activation refused without a passing replay report | M11, M15 | |
| R-10-metrics | 10 | `/metrics/quality` over a time window | Met | `app/api/main.py`, `app/quality_runs.py` | `tests/test_api.py::test_metrics_quality_reads_saved_quality_runs_in_a_window` | M9 | Only `retrieval` and `answers` runs; `from`/`to` window; calibration and known-invalid runs left out; values copied from `metrics.json`, never computed. |
| R-12-opa | 12 | OPA evaluated per candidate chunk | Partial | `app/retrieval/acl.py` | `opa test` passes; pre-filter/OPA equivalence on all users x chunks | M13 | The access rule runs inside Qdrant, on the BM25 candidate set and again per result (`can_see`); there is no OPA. |
| R-12-scan | 12 | User prompts scanned for injection | Met | `app/agents/graph.py` (`_understand`), `app/security/injection.py` | `tests/test_agents.py::test_injection_in_the_users_question_is_logged` | M9 | Hits are stored in the query-understanding trace and logged with the query id. The `queries.injection_hits` column comes in M11. |
| R-12-retention | 12 | Trace retention schedule | Partial | `app/security/masking.py` | Purge deletes traces older than the setting; `audit_log` untouched | M13 | Traces are masked; there is no scheduled purge. |
| R-12-model | 12 | Data-retention terms and private model for classified sources | Not met | | No restricted chunk ever appears in an external model request | M13 | |
| R-12-auth | 12, 6 | OIDC/JWT; no open registration | Partial | `app/api/auth.py`, `app/config.py` | Invalid, expired and wrong-audience JWTs rejected; registration off by default | M9, M13 | M9: self-registration is off by default and, when on, accepts only the firm's domain: `tests/test_auth.py::test_registration_is_off_by_default`, `::test_when_registration_is_on_only_the_firm_domain_may_register`. OIDC/JWT comes in M13. |
| R-13.1-sets | 13.1 | Per-source evaluation sets; regression tolerance | Not met | | CI gate fails on an injected regression | M18 | |
| R-13.2-judge | 13.2 | Answer relevance; calibrated judge; weekly review | Partial | `eval/judge.py`, `eval/run_judge_calibration.py` | Relevance metric present; kappa reported; review sampler test | M18 | Kappa is reported (-0.042, not validated); no relevance metric; no weekly sampler; the judge shares the verifier's model family. |
| R-13.3-replay | 13.3 | Replay tests before activation | Not met | | Replay suite runs in CI and gates activation | M15, M18 | |
| R-13.3-load | 13.3 | k6 and Locust load tests against latency targets | Not met | | Load test results files in `eval/results/` with p95 values | M18 | Single-user latency was measured (full answer p95 46.1 s against an 8 s target); no load tests. |
| R-13.3-contract | 13.3 | Contract tests for connectors and API | Partial | `tests/test_api.py`, `tests/test_auth.py` | Schema and fixture tests pass | M11, M12 | Behavioural API tests exist; none check the OpenAPI schema, and there are no connector fixtures. |
| R-14 | 14 | Containers, Kubernetes, Helm, CI/CD, scans, dashboards, alerts | Not met | | CI green; Helm install on kind passes smoke test; dashboards load | M17 | |
| R-15-req | 15 (#1) | Requirements specification: use cases, enterprise source inventory, quality targets | Partial | `CLAUDE.md` section 1, `docs/NOTES.md` | `docs/REQUIREMENTS.md` lists use cases, every registered source and the measurable targets | M19 | Example questions and targets exist; no requirements document. |
| R-15-docs | 15 (#2, #17) | Architecture design, API reference, operations guide | Partial | `docs/NOTES.md`, `docs/report/PROJECT_STATUS.md`, FastAPI `/docs` | `docs/ARCHITECTURE.md`, API reference and `docs/OPERATIONS.md` exist and are linked from README | M17, M19 | |
| R-ops-clone | audit | Runnable from a clean clone | Partial | `data/manifest.synthetic.csv`, `app/ingest/manifest.py`, `app/ingest/indexer.py` | Fresh-clone CI job indexes the synthetic corpus and passes tests | M9 | Audited as Defect. M9: without the ICAI PDFs the indexer uses the synthetic manifest and warns: `tests/test_clean_clone.py`, `tests/test_synthetic_corpus.py::test_committed_manifest_is_valid`. The fresh-clone CI job comes with CI in M17. |
| R-ops-integrity | audit | No edited evaluation outputs; honest Section 15 | Partial | `scripts/make_report.py`, `app/quality_runs.py` (`KNOWN_EDITS`), `docs/NOTES.md` (M9) | `make_report.py` integrity check passes; PDF table generated from `COMPLIANCE.md` | M9, M19 | Audited as Defect. M9: the report refuses any undocumented disagreement between a run's files; the one hand edit (q001) is documented and listed: `tests/test_make_report.py::test_an_undocumented_edit_blocks_the_report`. The PDF table comes in M19. |

## Declared deviations

| Spec asks | Built instead | Why |
|---|---|---|
| Python 3.11 | Python 3.13 | The only version on the development laptop (CLAUDE.md section 3). |
