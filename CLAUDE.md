# CLAUDE.md — Enterprise Fabric AI (Phase 2: full specification compliance)

Read this file at the start of every session. It replaces the Phase 1 version of 2026-11-08.

Enterprise Fabric AI is a multi-agent adaptive RAG assistant for a chartered accountancy and
audit firm. It implements the project document "Autonomous Multi-Agent Fabric: Real-Time
Adaptive RAG for Enterprise Intelligence" (`Autonomous_Multi_Agent_RAG_Project_Documentation.pdf`
in the project root, called "the specification" or "the spec" below). It is an internship project.

## 0. Where the project stands and what Phase 2 must do

Phase 1 (milestones M0–M8, commit `d4b6b6a`) built a scaled-down version. An independent audit
on 2026-11-18 compared it with the specification:

- Of the 17 deliverables in spec Section 15, **7 are met, 7 are partial and 3 are not met**.
- The PDF marks all 17 as "Completed". That is not true yet.
- Strong parts: six LangGraph agents, hybrid retrieval with RRF, cross-encoder re-ranking,
  per-claim verification, two-layer access control with zero measured leaks, masked traces,
  append-only audit log, 328 passing Python tests, a React UI with 19 passing tests.

**Phase 2 goal: close every Partial and Not met item in the Gap Register (Appendix A), so that
each spec requirement is either Met with a passing test, or explicitly reported as not met with
the reason.** Nothing in Phase 1 that works may regress.

The Phase 1 "Out of scope" list is cancelled. Kafka, Debezium, Airflow-class scheduling, Redis,
OpenSearch, PostgreSQL, OIDC/JWT, OPA, learned routing, bandits, latency degradation, the admin
console, observability, Docker, Kubernetes and Helm are now **in scope** (Section 3 says how).

### Phase 1 is preserved: all Phase 2 work happens on branches (see Section 18)
`main` stays exactly as Phase 1 left it (tagged `phase1-final`) until the author decides to
merge. Phase 2 never rewrites, resets or deletes Phase 1 history, files or tests.

### Non-negotiable honesty rules (unchanged, and stricter)
- Never mark a requirement Met unless the code exists AND its acceptance test (Appendix A) passes.
- `docs/report/COMPLIANCE.md` is the single source of truth for status (Section 16). The PDF's
  Section 15 table is regenerated from it before submission; never hand-write "Completed".
- Never write, estimate or invent a metric. Every number comes from a file in `eval/results/`.
- Never edit files in `eval/results/` after a run. To correct something, re-run and keep both runs.
- If a target (e.g. latency) is not met, say so plainly with the measured value.

## 1. Goal (unchanged)

Answer questions from audit staff (article assistants, seniors, managers, partners) over the
firm's knowledge base: Standards on Auditing (SA) guidance, the firm's methodology and policies,
technical circulars, audit programmes and checklists, templates, compliance calendar, internal
FAQ and engagement files.

Every answer must:
- carry citations (document, section, page, and the cited character span),
- respect the user's role AND engagement assignment (client confidentiality),
- prefer the current version of a policy or circular over a superseded one,
- include a confidence score, and say when verification was reduced (Section 8, degradation),
- be framed as decision support: judgement and sign-off stay with the engagement partner.
  The assistant never issues or drafts an audit opinion as final.

The system must be measurably better than the single-pass baseline, supported only by
evaluation runs that actually happened.

Example questions (keep working throughout Phase 2):
- "What are the sufficiency and appropriateness requirements for audit evidence under SA 500?"
- "How do positive and negative external confirmations differ (SA 505)?"
- "What is our firm's current overall materiality benchmark for manufacturing clients?"
- "Within how many days must the final audit file be assembled?" (SA 230 + firm policy)
- "Which NPA classification checks are on our bank branch audit checklist?"
- "What did the planning memo for Northwind Textiles identify as significant risks?"
- "When is the tax audit report due this year?" (calendar + circulars, time-sensitive)
- "How did the bank branch audit circular change between FY 2024-25 and FY 2025-26?"
  (the spec Section 11 workflow: time-bounded comparison, sub-queries, change log)

## 2. Two run profiles

The spec requires server infrastructure; the author develops on a Windows laptop. Both must work.

| Profile | Purpose | Backends |
|---|---|---|
| `lite` | Unit tests, CI test job, quick local demo, examiner running from a clone | SQLite, embedded Qdrant, `rank_bm25`, in-process event queue, in-process rate limiter and cache, local email/password sign-in, in-code policy |
| `full` | The specified architecture, the demo for the tutor, load tests, Kubernetes | PostgreSQL 16, Qdrant server, OpenSearch, Kafka, Redis, Debezium, Prefect, Keycloak (OIDC/JWT), OPA, MinIO (S3), OpenTelemetry, Prometheus, Grafana, Langfuse |

- Selected by `PROFILE=lite|full` in `.env`.
- Every backend sits behind a small interface (Protocol) in `app/ports/`, with one adapter per
  profile in `app/adapters/`. Agents and the API depend only on the interfaces. This is how the
  spec's "swap agents, indexes and providers without rewriting the system" (Section 5) is met.
- The same test suite runs against `lite` always, and against `full` in the integration job
  (`pytest -m integration`, needs `docker compose --profile full up`).
- The `full` profile needs Docker Desktop with WSL2 and roughly 12 GB free RAM with every service
  running. Compose profiles let the author start subsets (`core`, `ingest`, `identity`,
  `observability`).

## 3. Stack

Primary choices follow spec Section 6. Where the spec's primary choice is impractical on the
author's laptop, a spec-listed alternative is used and recorded as a deviation in
`docs/report/COMPLIANCE.md`.

| Layer | Choice | Spec status |
|---|---|---|
| Language | Python 3.13 (only version on the laptop) | Deviation from 3.11, declared |
| Dev environment | Windows + VS Code. `pathlib` everywhere; PowerShell commands (`.venv\Scripts\Activate.ps1`) | — |
| Frontend | React 19 + TypeScript + Vite + Tailwind CSS + **Radix UI** primitives | Primary |
| Backend API | FastAPI + uvicorn; SSE via `sse-starlette` | Primary |
| Orchestration | LangGraph (use its parallel fan-out for sub-queries) | Primary |
| LLM | Claude through the `anthropic` SDK. Direct Anthropic API is the default for `full`; OpenRouter allowed in `lite`. A private local model (Ollama, a spec-listed alternative) serves sources classified `restricted` | Primary + spec alternative |
| Embeddings | `BAAI/bge-small-en-v1.5` (sentence-transformers, CPU) | Primary |
| Re-ranker | `BAAI/bge-reranker-base` cross-encoder | Primary |
| NLI (verification) | A small NLI cross-encoder from sentence-transformers (author approves the exact model in M14) | Required by spec 7.8 |
| Vector store | Qdrant server (`full`); embedded Qdrant (`lite`) | Primary |
| Keyword search | OpenSearch BM25 with metadata filters (`full`); `rank_bm25` (`lite`) | Primary |
| Metadata store | PostgreSQL 16 with Alembic migrations (`full`); SQLite (`lite`) | Primary |
| Streaming / queue | Apache Kafka in KRaft mode (single broker) (`full`); in-process queue (`lite`) | Primary |
| Change data capture | Debezium (Kafka Connect) on the synthetic DMS PostgreSQL database | Primary |
| Scheduling | Prefect (spec-listed alternative to Airflow: far lighter on a laptop) | Spec alternative, declare |
| Cache | Redis: response cache, rate limits, latency window | Primary |
| Object storage | MinIO (S3-compatible) for the S3 connector | Supports spec 7.1 "S3 buckets" |
| Authentication | OIDC with JWT via Keycloak (spec-listed alternative/companion) in `full`; existing email/password sign-in kept for `lite` only | Primary |
| Authorisation | OPA (Rego policies in `policy/`), evaluated for every candidate chunk | Primary |
| Observability | OpenTelemetry SDK → Prometheus + Grafana; Langfuse for LLM call logs (own compose profile) | Primary |
| Deployment | Docker images, docker compose, Kubernetes via a Helm chart, tested on `kind` | Primary |
| CI/CD | GitHub Actions | Spec Section 14 |
| Load tests | k6 and Locust | Spec 13.3 |
| Tests | pytest (+ `pytest -m integration`), Vitest | Primary |

### Dependencies
New Python packages approved for Phase 2 (pin exact versions in `requirements.txt` when
installing; put integration-only packages in `requirements-full.txt`): `psycopg` (v3),
`alembic`, `opensearch-py`, `confluent-kafka`, `redis`, `prefect`, `boto3`,
`opentelemetry-sdk`, `opentelemetry-exporter-otlp`, `opentelemetry-instrumentation-fastapi`,
`prometheus-client`, `langfuse`, `pyjwt[crypto]`, `httpx` (already present), `locust`,
`ollama`. New npm packages: `@radix-ui/*` primitives as needed, `react-router`.
Ask before adding anything else.

### Model configuration (`.env`; never hard-code keys or model IDs)
- Direct Anthropic API: `ANTHROPIC_API_KEY=...`, model IDs `claude-haiku-5-5`,
  `claude-sonnet-5-5`, `claude-opus-5-5`.
- OpenRouter (`lite` only): `LLM_BASE_URL=https://openrouter.ai/api`, `LLM_AUTH_TOKEN=...`,
  model IDs `anthropic/claude-haiku-5.5` etc.
- `MODEL_LIGHT` (query understanding, routing tie-breaks, compression, reformulation),
  `MODEL_SYNTH` (synthesis), `MODEL_VERIFY` (verification; must differ from `MODEL_SYNTH`).
- `MODEL_JUDGE` (evaluation only): **must be from a different model family than `MODEL_VERIFY`**,
  so the judge does not share the verifier's blind spots (audit finding).
- `PRIVATE_MODEL` (Ollama model name) for `restricted` sources.
- `EFFORT_LIGHT` / `EFFORT_SYNTH` / `EFFORT_VERIFY`: `low | medium | high | xhigh | max`.

### LLM call rules (unchanged)
- Send no sampling parameters (`temperature`, `top_p`, `top_k`); the 5.5 models reject them.
  Steer with `output_config.effort`, fixed prompts and structured output.
- Outputs are not deterministic: report run-to-run variance for every re-run evaluation.
- Log model ID, effort, tokens, latency and prompt version for every call (masked, Section 12).

## 4. Repository layout

```
app/
  config.py                settings (PROFILE, thresholds, budgets)
  ports/                   Protocols: MetadataStore, VectorIndex, KeywordIndex, EventBus,
                           Cache, RateLimiter, PolicyEngine, IdentityProvider, Connector
  adapters/lite/           SQLite, embedded Qdrant, rank_bm25, in-process bus/cache/limiter
  adapters/full/           PostgreSQL, Qdrant server, OpenSearch, Kafka, Redis, OPA, Keycloak
  ingest/                  loaders, cleaner, chunker, chunking_profiles, manifest, indexer,
                           enrich.py (owner, classification, timestamp, ACL), worker.py (consumes
                           change events), watcher.py (publishes file events)
  connectors/              filesystem, git, s3, webhook, confluence, jira, sharepoint, base.py
  retrieval/               dense, keyword, fusion (weighted RRF), recency, rerank, compress,
                           acl filters, parent_expand.py (small-to-big)
  agents/                  query, router, retriever, reranker, synthesizer, verifier (+ nli.py),
                           graph.py, budgets.py, tracing.py
  adaptive/                bandit.py, routing_policy.py, degradation.py, latency_window.py
  learning/                feedback_signals.py, reranker_dataset.py, prompt_registry.py
  security/                masking, injection, passwords (lite only), retention.py, opa_client.py
  api/                     main.py, auth.py (lite), oidc.py (full), admin.py, schemas.py,
                           ratelimit.py, cache.py
  baseline/                single_pass_rag.py
  db/                      models.py, session.py, migrations/ (Alembic)
  observability/           otel.py, metrics.py
frontend/                  React UI (src/), admin console (src/admin/), Vitest tests
ui/streamlit_app.py        fallback UI (lite only; no new features)
policy/                    Rego policies + policy tests (opa test)
deploy/
  docker/                  Dockerfiles (api, worker, frontend)
  compose.yaml             profiles: core, ingest, identity, observability, full
  keycloak/realm.json      SYNTHETIC realm: roles, demo users, client
  grafana/dashboards/      JSON dashboards; prometheus/alerts.yaml
  helm/enterprise-fabric/  Helm chart
  debezium/                connector config for the synthetic DMS database
.github/workflows/         ci.yaml, release.yaml
loadtest/                  k6/*.js, locust/locustfile.py
scripts/                   existing scripts + make_synthetic_dms.py, seed_minio.py,
                           sample_weekly_review.py, export_reranker_dataset.py
data/corpus/<source_type>/ documents
data/manifest.csv          all documents (needs the ICAI PDFs)
data/manifest.synthetic.csv  the 40 synthetic documents only (runnable from a clean clone)
eval/                      questions.jsonl, questions/<source_type>.jsonl, run_*.py, replay/,
                           results/ (never edited after a run)
docs/NOTES.md              plain-language notes, one section per milestone
docs/OPERATIONS.md         operations guide (run, deploy, back up, rotate keys, troubleshoot)
docs/ARCHITECTURE.md       five planes, interfaces, data flow diagrams
docs/REQUIREMENTS.md       use cases, enterprise source inventory, quality targets
docs/report/COMPLIANCE.md  requirement-by-requirement status (Section 16)
docs/report/RESULTS.md     generated by scripts/make_report.py; never hand-edited
```

## 5. Corpus and metadata

Unchanged from Phase 1 unless stated: source types `standards`, `firm_policy`, `circulars`,
`checklists`, `templates`, `engagement`, `calendar`, `faq`; ICAI chapters as listed in
`docs/NOTES.md`; synthetic users and engagements in `data/users.csv` and `data/engagements.csv`.

### Manifest (`data/manifest.csv`)
Existing columns: `doc_id, path, source_type, title, version, effective_date, supersedes,
access_level, engagement_id, synthetic`. Phase 2 adds:
- `source_id` — the registered Source the document came from (Section 9),
- `owner` — the synthetic user id responsible for the document,
- `classification` — `public_internal | confidential | restricted`. `restricted` content is
  sent only to the private model (Section 12).

Connector-ingested documents get the same fields from the connector, not from the CSV.

### Runnable from a clone (audit finding)
- `data/manifest.synthetic.csv` lists only committed synthetic documents.
- When `data/corpus/standards/` is missing, the indexer uses the synthetic manifest and logs a
  clear warning. It never refuses the whole sync because licensed PDFs are absent.
- `test_committed_manifest_is_valid` validates the synthetic manifest; a separate test,
  skipped without the PDFs, validates the full one.

### Access rules (unchanged in meaning)
- Role levels: partner sees all; manager sees manager + staff; staff sees staff only.
- Engagement documents are visible only to that engagement's team; partners see all.
- A new capability flag `admin` (Keycloak realm role) controls the admin console. It grants
  NO extra document access.
- Checks run before scoring (Section 7.3) and again in OPA per candidate (Section 12).

### Synthetic content, copyright (unchanged)
- Every synthetic file starts with
  `SYNTHETIC — created for an internship demonstration. Not real firm or client data.`
- Fictional firm "Example & Associates, Chartered Accountants"; fictional clients; fake but
  well-formed PAN/GSTIN/account numbers.
- ICAI material stays out of git and is never redistributed; answers quote short passages only.
- The synthetic "enterprise systems" added in Phase 2 (DMS database, Git repo, MinIO bucket,
  Confluence/Jira/SharePoint fixtures) contain only synthetic content.

## 6. Ingestion plane (spec 5, 7.1, 7.2)

### Change events
Every source change becomes one `DocumentChanged` event on the event bus (Kafka topic
`doc-changes` in `full`, in-process queue in `lite`):
`{event_id, source_id, doc_id, uri, op: upsert|delete, content_hash, metadata, occurred_at}`.
`app/ingest/worker.py` is the only consumer. It updates the metadata store, the vector index
and the keyword index **from the same event**, so the indexes stay consistent (spec 9).
Processing is idempotent by `(doc_id, content_hash)`.

### Connectors (`app/connectors/`, one `Connector` interface)
| Connector | Real backend in `full` | How it is tested |
|---|---|---|
| filesystem | `watchdog` on `data/corpus/` (publishes events instead of indexing directly) | unit + integration |
| git | a local synthetic Git repository of firm documents; polls commits | unit + integration |
| s3 | MinIO bucket `firm-documents`; bucket notifications or polling | integration |
| webhook | `POST /api/v1/webhooks/documents` with an HMAC signature | unit + contract |
| dms (CDC) | Debezium streams row changes from the synthetic DMS PostgreSQL table `dms.documents` | integration |
| confluence, jira, sharepoint | Implemented against their documented REST shapes; tested with recorded synthetic fixtures served by a local mock server | contract tests only; state plainly in the report that no live tenant was used |

### Enrichment (`app/ingest/enrich.py`)
Each change is parsed, normalised and enriched with owner, classification, timestamp and access
control list before indexing (spec 7.1).

### Chunk-level incremental updates (spec 7.1 "only affected chunks are recomputed")
- Chunk ids become stable: `<doc_id>:<section path>:<window n>` instead of a running counter.
- Each chunk stores `content_hash`. On a document change, only chunks whose hash changed are
  re-embedded and upserted; vanished chunks are deleted from both indexes.
- Test: editing one section of a 20-chunk document re-embeds only that section's chunks.

### Scheduled pipelines (Prefect)
- Nightly reconciliation: compares every source with the indexes and repairs drift.
- Nightly retrieval evaluation (no LLM calls) with the regression gate (Section 13).
- Weekly sampling of answers for human review (Section 13).
- Trace retention purge (Section 12).

### Adaptive chunking (spec 7.2)
Phase 1 chunking stays (structure first, tables and illustrations whole, FAQ and checklist
groups whole, parent/child links). Phase 2 adds `chunking_profiles` per source type
(max tokens, overlap, whole-unit rule), stored in config and recorded on each chunk, so
"chunk granularity is stored per source type". Slides and code blocks are kept whole if such
sources are added.

## 7. Agent plane (LangGraph)

The Phase 1 agents stay. Changes per agent:

1. **Query understanding** (`MODEL_LIGHT`)
   - Intents: `no_retrieval | factual | procedural | comparison | analytical | open_ended |
     time_sensitive` (adds the spec's analytical and open-ended classes).
   - Extracts SA numbers, clients, financial statement items, dates and financial years.
   - Rewrites into 1–3 sub-queries.
   - **Scans the user's question with `app/security/injection.py`** and logs hits (audit finding).
   - Simple questions take a **light path** (spec 16.1): intent `factual`, one sub-query, no
     client, high-confidence routing → skip compression, smaller candidate set.
2. **Source routing** — learned policy (Section 8). The Phase 1 rules remain as the prior and
   the fallback. The engagement denial check stays rule-based and runs first; no learning can
   override access rules. Every decision is logged with the policy version.
3. **Retrieval**
   - Sub-queries run **in parallel** (LangGraph fan-out); dense and keyword search for one
     sub-query also run concurrently.
   - Pre-filter by role + engagement inside both indexes, then OPA per candidate (Section 12).
   - Weighted RRF: per-retriever weights set by query type (Section 8).
   - Recency boost, superseded down-weighting, follow-up retrieval when evidence is
     insufficient (as Phase 1).
   - Writes one `retrievals` row per sub-query and method, including every score multiplier
     applied (audit finding: adjustments must be recorded).
4. **Re-rank and compress**
   - Re-rank **per sub-query**, then merge (audit finding), so both sides of a comparison
     survive.
   - Compression keeps the sentences relevant to each sub-query, copied verbatim.
   - For exploratory intents, expand to the parent chunk (small-to-big) before compression.
5. **Synthesis** (`MODEL_SYNTH`)
   - **Streams draft tokens to the client as they are generated** (Section 10).
   - Every factual sentence cites `[chunk_id]`; the code records the character span of the
     supporting text in the chunk for the `citations` table.
   - Rules unchanged: no invented numbers or dates, cite current over superseded with both
     dates, present ICAI-versus-firm differences, end with the professional-judgement note.
6. **Verification** (`MODEL_VERIFY` + NLI model)
   - Each claim is checked by **both** an NLI model (entailment against its cited span) and the
     LLM judge (spec 7.8).
   - Both agree supported → kept. LLM unsupported → removed. LLM supported but NLI
     contradiction → kept and **flagged** in the answer ("check this point").
   - Confidence = supported factual claims / factual claims. Below 0.6 → one loop back to
     retrieval, then a clarifying question or escalation to the engagement manager.
   - Sends one `claim_verdict` event per claim while the answer is still on screen.

### Step budgets (spec 5, agent plane)
`app/agents/budgets.py` enforces per-question limits, configurable in `.env`: max LLM calls,
max total tokens, max wall-clock seconds, max retrieval rounds. A breached budget ends the run
with the best verified answer so far or an abstention, and is logged.

### Rules for all agents (unchanged)
- Retrieved text is untrusted data: delimited `<document>` blocks, the untrusted-data rule in
  every system prompt, injection scan with logging.
- Restricted content: "not available to your role or engagement"; never confirm or deny that a
  restricted document exists.
- One `traces` row per agent step, with the prompt version used.
- Prompts live in the `prompt_versions` table (Section 9), not in code constants. The code holds
  only the seed version.

## 8. Adaptive retrieval (spec Section 8, all four dimensions)

Every adjustment is written to the `retrievals` and `traces` rows so its effect can be measured.

1. **Query type**
   - `factual`, `procedural`: keyword weight 0.6 / dense 0.4 in weighted RRF, child chunks,
     fewer candidates (default 10 per retriever).
   - `comparison`, `analytical`, `open_ended`: dense weight 0.6, parent expansion, more
     candidates (default 30 per retriever).
   - All weights and counts live in config; the defaults are starting points to be tuned on the
     evaluation set, not facts.
2. **Source freshness**: recency boost for time-sensitive terms (Phase 1), plus a boost for
   documents whose source changed recently (from `documents.updated_at`).
3. **Observed quality (bandit)**
   - Arms: (source_type, ranking method) combinations, per intent.
   - Thompson sampling with Beta priors; fixed random seed in tests.
   - Reward = 1 when the answer is verified with confidence ≥ 0.6 and gets no "not helpful"
     feedback within 24 hours; 0 otherwise. Implicit signals (Section 11) add weaker rewards.
   - The bandit allocates the retrieval candidate budget across arms. It may add or drop sources
     only among those the user is permitted to see.
   - Bandit state is saved as a new `routing_policies` version; activation is gated (Section 13).
4. **Load and latency**
   - `app/adaptive/latency_window.py` keeps a rolling p95 of full-answer latency (Redis in
     `full`, memory in `lite`).
   - Above target: cut candidate counts, skip compression, and skip optional verification for
     **low-risk** questions only.
   - Low-risk = intent `factual`, all evidence from `standards`, `faq` or `calendar`, no
     engagement documents, no superseded documents, user role `staff`-level content only.
     Verification is never skipped for engagement, superseded, manager- or partner-level content.
   - The answer then says "Reduced checking: this answer was not fully verified because the
     system is under load" and carries no confidence score. The UI shows a badge.

## 9. Data model

`full` uses PostgreSQL with Alembic migrations; `lite` uses SQLite with the same models.

| Table | Columns (new in **bold**) |
|---|---|
| `users` | user_id, name, role, synthetic, email, password_hash (lite only), **is_admin** |
| `engagements`, `engagement_members` | unchanged |
| **`sources`** | **id, type, connector_config (JSON, secrets referenced not stored), owner, classification, status, last_event_at, created_at** |
| `documents` | doc_id, **source_id**, path/**uri**, source_type, title, version, effective_date, supersedes, access_level, engagement_id, **owner, classification**, synthetic, content_hash, indexed_at, **updated_at** |
| `chunks` | chunk_id, doc_id, parent_id, text, token_count, section, section_title, chapter, page_start, page_end, sa_refs, **content_hash, chunking_profile, embedding_ref** |
| `queries` | id, user_id, text (masked), intent, **injection_hits**, created_at |
| `traces` | id, query_id, agent, input, output, model, effort, tokens_in, tokens_out, latency_ms, **prompt_version_id, policy_version_id, created_at** |
| **`retrievals`** | **id, query_id, sub_query, source_id, method (dense\|keyword\|fused), scores (JSON: per chunk raw score, rank, multipliers), created_at** |
| `answers` | id, query_id, text, confidence, status, **degraded (bool), flagged_claims** |
| `citations` | id, answer_id, chunk_id, **span_start, span_end** |
| `feedback` | id, answer_id, user_id, rating, correction (masked) |
| **`feedback_signals`** | **id, answer_id, user_id, kind (copy\|citation_click\|follow_up\|rephrase), created_at** |
| **`routing_policies`** | **id, version, weights (JSON), created_by, eval_report (JSON), activated_at, is_active** |
| **`prompt_versions`** | **id, agent, version, template, eval_score, created_by, created_at, is_active** |
| `audit_log` | id, actor, action, target, created_at — append-only (ORM hooks + DB triggers in both SQLite and PostgreSQL) |
| `auth_sessions`, `password_resets` | lite profile only |

Vector and keyword index payloads carry `doc_id, chunk_id, source_id, source_type,
access_level, engagement_id, classification, effective_date, superseded`.

## 10. API (`/api/v1`) — spec Section 10 in full

All endpoints need a bearer token: a Keycloak-issued JWT in `full` (validated against the realm
JWKS: signature, issuer, audience, expiry), a session token in `lite`. Only `/health`,
`/metrics` (Prometheus, internal network only) and the `lite` sign-in routes are open.

| Method | Endpoint | Behaviour |
|---|---|---|
| POST | `/query` | SSE stream: `progress` (agent steps), `draft_token` (live synthesis tokens), `claim_verdict` (per claim), `final` (verified text, citations with spans, confidence, flags, degraded) and `error` (generic message; details only in server logs) |
| GET | `/query/{id}` | Completed answer, citations, trace (asker or partner) |
| POST | `/feedback` | Rating and correction |
| POST | `/feedback/signals` | Implicit signals from the UI |
| POST | `/sources` | Register a source (admin). Validates connector config; writes `audit_log` |
| GET | `/sources/{id}/status` | Ingestion status, last event, document and chunk counts, freshness lag |
| POST | `/documents/reindex` | Body `{doc_id}` or `{source_id}` or `{all: true}` (admin); `audit_log` |
| GET | `/traces/{query_id}` | Agent execution trace (admin or partner) |
| PUT | `/routing-policy` | Activate a routing policy version (admin), only if its replay report passed; `audit_log` |
| GET | `/metrics/quality?from=&to=` | Retrieval and faithfulness metrics from `eval/results/` runs of type `retrieval` or `answers` within the window. Never computes or estimates |
| POST | `/webhooks/documents` | Webhook connector (HMAC-signed) |
| GET/POST | `/admin/prompts`, `/admin/prompts/{id}/activate`, `/admin/agents/performance`, `/admin/classification-policies` | Admin console back end (Section 11) |
| GET | `/history`, `/history/{query_id}` | Own history only (unchanged) |

### Serving-plane requirements
- **Rate limiting**: token bucket per user and per IP (Redis in `full`). Defaults: `/query`
  10 per minute per user; `/auth/*` and token endpoints 5 per minute per IP with lockout after
  10 failures in 15 minutes. HTTP 429 with `Retry-After`.
- **Response caching**: cache key = normalised question + **the user's access fingerprint**
  (role + sorted engagement ids) + index version + active prompt and policy versions. A cached
  answer is never served to a user with a different fingerprint. Any index change bumps the
  index version. Test: two users with different engagements never share a cache entry.
- **Concurrency**: no global query lock (audit finding). Only index writes are serialised.
  Test: two concurrent `/query` calls overlap in time.
- Open self-registration is **off** by default (`ALLOW_SELF_REGISTRATION=false`); when on, it
  accepts only `@example-associates.test` emails (audit finding).

## 11. Experience plane, feedback loop and admin console

### Answer view (React)
- Draft text streams token by token, shown in a muted style labelled "Checking…".
- As `claim_verdict` events arrive: supported sentences turn normal, removed ones disappear,
  flagged ones get a "check this point" marker. The final text replaces the draft.
- Citations with source preview (section text, page, the cited span highlighted).
- Confidence score, "superseded policy" badge, "reduced checking" badge.
- Feedback: helpful / not helpful + correction. Implicit signals sent: copy of answer text,
  citation click, follow-up question within 5 minutes, immediate rephrase.
- Built with Radix UI primitives (dialog, dropdown, tooltip, tabs) and Tailwind.

### Feedback and learning (spec 7.9)
- Signals update the bandit rewards (Section 8).
- `scripts/export_reranker_dataset.py` writes (query, chunk, label) pairs from feedback and
  verification results to `data/learning/reranker/<timestamp>.jsonl`.
- `scripts/finetune_reranker.py` fine-tunes the cross-encoder on that data. A fine-tuned model
  is used only if it beats the current one on the retrieval evaluation (Section 13).
- Corrections become candidate evaluation questions with `"reviewed": false`.

### Admin console (spec 7.10, `/admin` route, `is_admin` users only)
- Register and pause sources; see their status and freshness lag.
- Set classification policies (which source types or paths are `confidential` / `restricted`).
- Monitor agent performance: latency per agent, verification failure rate, token use, cost per
  answer (from the metrics backend).
- Inspect traces.
- Roll back an agent prompt version or a routing policy version. Each rollback writes `audit_log`.

## 12. Security and governance (spec Section 12, every bullet)

1. Access control during retrieval: pre-filters in Qdrant and OpenSearch, built from the same
   policy data as OPA. Unauthorised text is never sent to a model.
2. **OPA**: `policy/document_access.rego` decides `allow` for (user, chunk metadata). It is
   evaluated for **every candidate chunk** after retrieval and before re-ranking, in one batch
   call. Disagreement between the pre-filter and OPA is logged as a security error and the chunk
   is dropped. Tests: `opa test policy/` plus a Python test that the pre-filter and OPA agree on
   every chunk of the synthetic corpus for every synthetic user.
3. **Injection**: both the user's question and retrieved text are scanned and logged; retrieved
   text stays delimited and untrusted.
4. **Masking and retention**: PAN, GSTIN, account numbers, emails and phones are masked before
   writing traces, queries, feedback and logs. `TRACE_RETENTION_DAYS` (default 90) drives a
   scheduled purge of traces and retrievals. `audit_log` is exempt and never purged.
5. **Model data handling**: `full` uses the direct Anthropic API. Documents classified
   `restricted` are answered only by the private Ollama model; the router enforces this and a
   test proves no restricted chunk is ever in a request to an external model. Record in the
   report what data-retention terms apply to the provider used; do not claim zero data retention
   unless the account actually has it.
6. **Audit log**: append-only for administrative actions, policy changes, prompt and routing
   activations, source registration, re-indexing and sign-in events.
- Secrets only in `.env` (local) or Kubernetes Secrets; never in git, logs or connector config.
- Errors returned to clients are generic; details stay in server logs (audit finding).

## 13. Testing and evaluation (spec Section 13, every bullet)

### 13.1 Retrieval evaluation
- recall@5, recall@10, MRR, nDCG@10, overall and per source type (Phase 1 code).
- **One evaluation file per source type**: `eval/questions/<source_type>.jsonl`, at least
  10 questions each (calendar, templates and faq included).
- **Regression gate**: CI compares the new run with the last accepted run in
  `eval/baselines/retrieval.json`. Any metric dropping more than `RETRIEVAL_TOLERANCE`
  (default 0.02 absolute) for any source type fails the build.

### 13.2 Answer evaluation
- Faithfulness, **answer relevance** (judge: does the answer address the question) and
  citation precision, plus correctness against gold answers.
- Judge = `MODEL_JUDGE`, a different model family from the verifier.
- Calibration: **stratified** sample of at least 30 answers with at least 8 judged partially
  correct or incorrect, hand-labelled by the author. Report agreement and Cohen's kappa. The
  judge is "validated" only if kappa ≥ 0.6; otherwise say it is not validated.
- `scripts/sample_weekly_review.py` draws a fixed percentage (default 5%) of the week's answers
  for human review and stores labels; the report tracks judge drift over time.
- Every answer evaluation is run at least twice; report the mean and the spread.

### 13.3 System testing
- **Unit tests** (pytest) for agents, chunkers, access control, bandit, degradation, cache keys,
  OPA client. LLM and NLI calls are mocked in unit tests.
- **Contract tests**: every API endpoint against its OpenAPI schema; every connector against its
  recorded fixtures.
- **Replay tests**: `eval/replay/` holds recorded traces (inputs, retrieved chunk ids, LLM
  responses). A new prompt version or routing policy version is replayed against them and must
  not lower the replay score beyond tolerance. `PUT /routing-policy` and prompt activation
  refuse versions without a passing replay report.
- **Load tests**: `loadtest/k6/query.js` and `loadtest/locust/locustfile.py`. Measure p95 time
  to first draft token and p95 full verified answer at 1, 5 and 10 concurrent users. Targets:
  p95 TTFT < 1.5 s, p95 full answer < 8 s. Report the hardware. If not met, say so with the
  numbers; never adjust how latency is measured to meet a target.
- **Security tests**: prompt-injection suite (documents and user questions), permission-boundary
  tests that try cross-engagement and cross-role retrieval through every path (direct search,
  cache, history, traces, admin endpoints, follow-up retrieval, restricted-to-external-model).

### Evaluation set (from Phase 1, plus)
- At least 60 general questions (Phase 1 quotas) + per-source files above + at least 10
  time-sensitive questions (the weakest Phase 1 category had only 2).
- Claude may draft questions and gold answers, but each starts `"reviewed": false`. Only the
  author sets it to true. Every report states how many are reviewed.

### Output rules (unchanged)
Every run writes `eval/results/<timestamp>_<type>/` with metrics JSON, per-question CSV, config
snapshot and model IDs. Never edit them afterwards. `scripts/make_report.py` refuses to build a
report from a run whose files disagree.

## 14. Deployment and operations (spec Section 14)

- `deploy/docker/`: Dockerfiles for `api`, `worker` and `frontend`; non-root user; health
  checks.
- `deploy/compose.yaml`: every `full` service with profiles; one command starts the demo:
  `docker compose -f deploy/compose.yaml --profile full up -d`.
- `deploy/helm/enterprise-fabric/`: chart for api, worker and frontend with ConfigMaps, Secrets,
  probes, resource limits and an HPA for the API. Stateful services (Qdrant, OpenSearch, Kafka,
  PostgreSQL, Redis) are referenced as external or operator-managed, as the spec says.
- Verified on a local `kind` cluster: `helm lint`, `helm install`, smoke test of `/health` and
  one `/query` with the LLM mocked by `LLM_MODE=mock`.
- **CI (`.github/workflows/ci.yaml`)** on every push and pull request: Python lint + unit tests
  (lite), Vitest + type check, `opa test`, security scans (`pip-audit`, `npm audit
  --audit-level=high`, Trivy on the images), retrieval evaluation + regression gate, replay
  tests, `helm lint`, and an integration job with docker compose.
- **Promotion**: images are tagged per commit; staging deploy (namespace `staging` on kind) only
  after CI passes; prompt or routing changes reach production only after the replay tests pass.
- **Dashboards** (`deploy/grafana/dashboards/`): query volume, latency per agent (p50/p95), token
  use, cost per answer, retrieval hit rate, verification failure rate, degradation rate, cache hit
  rate, ingestion lag per source.
- **Alerts** (`deploy/prometheus/alerts.yaml`): p95 latency above target, verification failure
  rate above 20%, ingestion lag above 15 minutes, any ACL disagreement between pre-filter and OPA,
  error rate above 5%.
- OpenTelemetry traces span every agent step and LLM call; Langfuse records LLM calls with prompt
  versions (masked inputs only).
- `docs/OPERATIONS.md`: start/stop, deploy, back up and restore PostgreSQL and indexes, rotate
  keys, add a source, roll back a prompt or policy, read dashboards, troubleshoot.

## 15. Milestones (Phase 2)

Work one milestone at a time, in order. Each milestone: propose a short plan and wait for
approval; build; run every test and show the results; update `docs/NOTES.md`,
`docs/report/COMPLIANCE.md` and the Gap Register status; commit with a clear message that lists
the requirement IDs closed (e.g. `M12: connectors and CDC (R-7.1-a, R-7.1-b, R-5-ing)`).

**M9 — Audit fixes and honest baseline** (do first; mostly small)
- Synthetic manifest; clean-clone indexing; fix the failing manifest test.
- Remove the global query lock (serialise index writes only); concurrency test.
- Rate-limit sign-in and turn off open self-registration by default (lite limiter).
- Scan user questions for injection.
- `/metrics/quality`: quality runs only, `from`/`to` window.
- Generic client error messages.
- Record score multipliers per chunk in the retrieval trace.
- Explain the q001 label edit in `eval/results/20261113-101102_answers/answers.jsonl` in
  `docs/NOTES.md`; regenerate that run's report from untouched files.
- Create `docs/report/COMPLIANCE.md` from Appendix A.

**M10 — Ports, adapters and the full stack skeleton**
- `app/ports/` interfaces; lite adapters wrap the Phase 1 code (no behaviour change).
- `deploy/compose.yaml` with PostgreSQL, Qdrant server, OpenSearch, Redis, Kafka.
- Full adapters for metadata (Alembic), vector, keyword, cache, event bus.
- The whole Phase 1 test suite passes on `lite`; integration tests pass on `full`.

**M11 — Data model and API completion**
- New tables and columns (Section 9), migrations.
- `POST /sources`, `GET /sources/{id}/status`, targeted re-index, `PUT /routing-policy`
  (with a stub policy until M15), citation spans, contract tests for every endpoint.

**M12 — Ingestion plane**
- Change events, worker, connectors (Section 6), Debezium CDC from the synthetic DMS, webhook,
  enrichment, chunk-level incremental updates, chunking profiles, Prefect flows.

**M13 — Identity and policy**
- Keycloak realm with synthetic users and roles, OIDC/JWT validation, admin flag.
- OPA policies, per-candidate evaluation, equivalence tests.
- Retention purge; restricted-content routing to the private model.

**M14 — Agent plane upgrades**
- New intents and the light path; parallel fan-out; per-sub-query re-ranking; parent expansion;
  step budgets; NLI + LLM verification with flagging; true draft streaming with claim verdicts.

**M15 — Adaptive controller and learning**
- Query-type weighting; source-change freshness; Thompson-sampling bandit with versioned
  routing policies; latency window and degradation with user notice; implicit signals; reranker
  dataset export and fine-tuning script; prompt registry with rollback.

**M16 — Serving and experience**
- Redis rate limiting and ACL-safe response cache; Radix UI; streaming draft view; admin
  console.

**M17 — Observability and deployment**
- OpenTelemetry, Prometheus, Grafana dashboards, alerts, Langfuse; Dockerfiles; Helm chart on
  kind; GitHub Actions CI/CD with scans, eval gate and replay gate; `docs/OPERATIONS.md`.

**M18 — Evaluation completion**
- Per-source evaluation sets; regression baseline; replay suite; k6 and Locust runs; judge from
  another family with stratified recalibration; two answer runs for variance; weekly review
  sampler.

**M19 — Report**
- Regenerate `RESULTS.md` from `eval/results/` only.
- Final `COMPLIANCE.md`; a Section 15 replacement table with real status for the PDF;
  `docs/ARCHITECTURE.md` with diagrams of the five planes; API reference from OpenAPI;
  `docs/REQUIREMENTS.md` (use cases, source inventory, quality targets).
- List every remaining deviation and every requirement still not met, with the reason.

## 16. Compliance tracking

`docs/report/COMPLIANCE.md` has one row per requirement ID in Appendix A:
`ID | spec § | requirement | status (Met / Partial / Not met / Deviation) | evidence (files) |
acceptance test (test id) | milestone | notes`.

- A row becomes **Met** only in the same commit that adds its passing acceptance test.
- Tests name the requirements they prove in their docstring: `Covers: R-7.8-a`.
- `scripts/check_compliance.py` (CI) fails if a row marked Met has no test that names its ID,
  or if that test is skipped.
- A **Deviation** row says what the spec asks, what was built instead, and why.

## 17. Conventions and working rules for Claude Code

- Type hints, docstrings on public functions, small single-purpose modules, simple readable code:
  the author must be able to explain every module in the viva.
- All configuration in `app/config.py` / `.env`. No secrets in code or git. Fixed seeds.
- One commit per completed milestone (or per closed requirement group), never one big commit.
- After each milestone, append a plain-language section to `docs/NOTES.md`: what was built, why,
  how it works, the audit-domain reasoning, likely viva questions with answers.
- Do not state auditing facts from memory in code, synthetic documents or answers; ground them in
  the ICAI chapters and cite chapter and section.
- If a requirement is ambiguous, or the laptop cannot run something, ask; do not guess and do not
  silently scale down. A scaled-down choice is a Deviation row, agreed with the author.
- Never weaken an existing access-control, masking or audit-log test to make new code pass.

## 18. Git workflow (protects the Phase 1 work)

### One-time setup (the author runs this, or Claude Code runs it after asking)
```powershell
git switch main
git pull
git tag -a phase1-final -m "Phase 1 (M0-M8) as audited on 2026-11-18"
git push origin phase1-final
git switch -c phase2
git push -u origin phase2
```

### Branches
- `main` — Phase 1 as submitted. **Never commit to `main` in Phase 2.**
- `phase2` — the integration branch for all Phase 2 work.
- One branch per milestone, created from `phase2`: `phase2/m9-audit-fixes`,
  `phase2/m10-ports-adapters`, … `phase2/m19-report`.
- A milestone branch is merged into `phase2` (pull request, no squash, so each commit and its
  requirement IDs stay visible) only after every test passes and the author approves.
- `phase2` is merged into `main` only when the author decides, never by Claude Code on its own.

### At the start of every session
1. Run `git status` and `git branch --show-current`.
2. If the current branch is `main`, stop and switch to the right `phase2/...` branch (ask if
   unsure which).
3. If there are uncommitted changes you did not make in this session, stop and ask.

### Never, without the author's explicit approval in this session
- commit to `main`, merge into `main`, or push to `main`;
- `git push --force` (or `--force-with-lease`), `git rebase` of pushed commits,
  `git commit --amend` of pushed commits, `git reset --hard`;
- delete a branch or a tag (especially `phase1-final`);
- `git clean`, `git checkout -- .`, `git restore .` or anything else that discards work;
- delete Phase 1 files. A Phase 1 module that is replaced moves behind a port as the `lite`
  adapter (Section 2); the Streamlit fallback, Phase 1 tests and `eval/results/` stay.

### Commits and pushes
- Commit only when the milestone's tests pass (or, mid-milestone, when the author asks).
- Commit message names the milestone and the requirement IDs closed.
- Push the milestone branch only when the author asks.
- `eval/results/` runs are committed as new folders; existing run folders are never modified.

### Going back
At any time the author can return to the audited Phase 1 code with `git switch main` (or
`git checkout phase1-final`), without losing Phase 2 work on its branches.

## Appendix A — Gap Register (from the 2026-11-18 audit)

Every row starts with the audited status. Close each one with its acceptance test.

| ID | Spec § | Requirement | Audited | Milestone | Acceptance test |
|---|---|---|---|---|---|
| R-3-adapt | 3, 8 | Sources, chunk granularity and ranking chosen per query | Partial | M14, M15 | Different intents produce different retriever weights, candidate counts and chunk levels, recorded in `retrievals` |
| R-3-stream | 3, 5 | Streaming ingestion and incremental updates | Partial | M12 | A DMS row update reaches both indexes via Debezium and Kafka within 60 s (integration) |
| R-3-eval | 3, 13 | Measurable evaluation that can be trusted | Partial | M18 | Reviewed question count reported; judge kappa reported; two runs with spread |
| R-5-ing | 5 | Ingestion plane: connectors, CDC listeners | Partial | M12 | One test per connector (Section 6) |
| R-5-serve | 5, 10 | Rate limiting and response caching | Not met | M9, M16 | 429 after the limit; cache never crosses access fingerprints |
| R-5-exp | 5, 11 | Token-by-token streaming answer | Partial | M14, M16 | First `draft_token` event arrives before verification starts |
| R-5-par | 5 | Parallel processing where possible; no global lock | Not met | M9, M14 | Sub-query searches overlap in time; two `/query` calls overlap |
| R-6-radix | 6 | Radix UI | Not met | M16 | UI uses Radix primitives; Vitest passes |
| R-6-stack | 6 | OpenSearch, PostgreSQL, Kafka, Redis, Debezium, scheduler, OIDC/JWT, OPA, OTel/Prometheus/Grafana/Langfuse, Docker/K8s/Helm | Not met | M10–M17 | Each runs in `full` and has an integration test |
| R-7.1-a | 7.1 | Owner, classification, timestamp, ACL enrichment | Partial | M12 | Enriched fields present on every document and index payload |
| R-7.1-b | 7.1 | Only affected chunks recomputed | Partial | M12 | One-section edit re-embeds only that section's chunks |
| R-7.1-c | 7.1 | Connectors: SharePoint, Confluence, Jira, Git, S3; webhook listeners | Not met | M12 | Fixture contract tests (SharePoint, Confluence, Jira); integration (Git, S3, webhook) |
| R-7.2 | 7.2 | Chunk granularity stored per source type | Partial | M12 | `chunking_profile` recorded on every chunk |
| R-7.3 | 7.3 | Analytical and open-ended intents | Partial | M14 | Classifier tests for all seven intents |
| R-7.4 | 7.4 | Learned routing from success, freshness, scope; decisions refine policy | Partial | M15 | Bandit converges on a simulated reward set (seeded); new policy version created from logs |
| R-7.6 | 7.6 | Compression relevant to each sub-query | Partial | M14 | Both sides of a comparison survive re-ranking and compression |
| R-7.8 | 7.8 | NLI model plus LLM judge; flag failures | Partial | M14 | NLI contradiction flags a claim the LLM accepted |
| R-7.9 | 7.9 | Implicit signals update routing, re-ranker dataset, prompt evaluation sets | Partial | M15 | Signals change bandit rewards; dataset export contains labelled pairs |
| R-7.10 | 7.10 | Admin console | Not met | M16 | Admin can register a source, inspect a trace, roll back a prompt (UI + API tests) |
| R-8-bandit | 8 | Bandit allocation by observed quality | Not met | M15 | Allocation shifts toward the rewarded arm in a seeded simulation |
| R-8-degrade | 8 | Degrade under p95 latency pressure and tell the user | Not met | M15 | Forced high p95 reduces candidates, skips verification only for low-risk, shows the notice |
| R-8-record | 8 | Every adjustment recorded | Partial | M9, M14 | Multipliers and weights stored per chunk in `retrievals` |
| R-9-tables | 9 | Source, Retrieval, RoutingPolicy, PromptVersion entities | Not met | M11 | Migrations create them; API tests use them |
| R-9-span | 9 | Citation spans | Partial | M11, M14 | `span_start`/`span_end` point to the supporting text |
| R-10-sources | 10 | `POST /sources`, `GET /sources/{id}/status` | Not met / Partial | M11 | Contract tests |
| R-10-reindex | 10 | Re-index a document or a source | Partial | M11 | Targeted re-index touches only that document's or source's chunks |
| R-10-policy | 10 | `PUT /routing-policy` | Not met | M11, M15 | Activation refused without a passing replay report |
| R-10-metrics | 10 | `/metrics/quality` over a time window | Partial | M9 | Window filter test; calibration runs excluded |
| R-12-opa | 12 | OPA evaluated per candidate chunk | Partial | M13 | `opa test` passes; pre-filter/OPA equivalence on all users × chunks |
| R-12-scan | 12 | User prompts scanned for injection | Partial | M9 | Injection in a question is logged |
| R-12-retention | 12 | Trace retention schedule | Partial | M13 | Purge deletes traces older than the setting; `audit_log` untouched |
| R-12-model | 12 | Data-retention terms and private model for classified sources | Not met | M13 | No restricted chunk ever appears in an external model request |
| R-12-auth | 12, 6 | OIDC/JWT; no open registration | Not met | M9, M13 | Invalid, expired and wrong-audience JWTs rejected; registration off by default |
| R-13.1-sets | 13.1 | Per-source evaluation sets; regression tolerance | Not met | M18 | CI gate fails on an injected regression |
| R-13.2-judge | 13.2 | Answer relevance; calibrated judge; weekly review | Partial | M18 | Relevance metric present; kappa reported; review sampler test |
| R-13.3-replay | 13.3 | Replay tests before activation | Not met | M15, M18 | Replay suite runs in CI and gates activation |
| R-13.3-load | 13.3 | k6 and Locust load tests against latency targets | Not met | M18 | Load test results files in `eval/results/` with p95 values |
| R-13.3-contract | 13.3 | Contract tests for connectors and API | Partial | M11, M12 | Schema and fixture tests pass |
| R-14 | 14 | Containers, Kubernetes, Helm, CI/CD, scans, dashboards, alerts | Not met | M17 | CI green; Helm install on kind passes smoke test; dashboards load |
| R-15-req | 15 (#1) | Requirements specification: use cases, enterprise source inventory, quality targets | Partial | M19 | `docs/REQUIREMENTS.md` lists use cases, every registered source and the measurable targets |
| R-15-docs | 15 (#2, #17) | Architecture design, API reference, operations guide | Partial | M17, M19 | `docs/ARCHITECTURE.md`, API reference and `docs/OPERATIONS.md` exist and are linked from README |
| R-ops-clone | audit | Runnable from a clean clone | Defect | M9 | Fresh-clone CI job indexes the synthetic corpus and passes tests |
| R-ops-integrity | audit | No edited evaluation outputs; honest Section 15 | Defect | M9, M19 | `make_report.py` integrity check passes; PDF table generated from `COMPLIANCE.md` |
