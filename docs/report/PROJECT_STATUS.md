# Enterprise Fabric AI: project status, deviations and future work

Maintained by hand. It contains no metrics: every number lives in [RESULTS.md](RESULTS.md),
which `scripts/make_report.py` generates from `eval/results/`.
Status as of 2026-11-18.

> **Phase 2:** requirement-by-requirement status now lives in
> [COMPLIANCE.md](COMPLIANCE.md), the single source of truth (CLAUDE.md section 16).
> This file records Phase 1 (M0-M8) as audited.

The project document's "Work Completed" table lists every item as Completed. This table
shows the real status instead. "Done" means the item exists and its tests pass.

## 1. Deliverable status

| Deliverable | Status | Evidence / what is missing |
|---|---|---|
| M0 Skeleton: layout, config, `.env.example`, `.gitignore`, SQLite models, pytest | Done | `tests/` |
| M1 Corpus: ICAI chapters copied, PDF/Markdown loaders, cleaner | Done | cleaner and loader tests |
| M1 Synthetic firm documents, users, engagements | Built; **author review pending** | CLAUDE.md requires the author to review `scripts/make_synthetic_docs.py` output; see `docs/PLACEHOLDERS.md` |
| M1 Manifest validation, `corpus_stats.py` | Done | manifest tests |
| Corpus extensions (CA Final, Taxation, AS + Companies Act 2013) | Done | 258 manifest rows, 15,705 chunks (NOTES.md) |
| M2 Structure-aware chunker, Qdrant + BM25 indexing | Done | chunker and indexing tests |
| M2 Incremental watcher with `audit_log` | Done | edit/delete reflected in both indexes (tests) |
| M3 Hybrid retrieval (dense + BM25 + RRF), recency, superseded weighting | Done | retrieval tests; RESULTS.md section 2 |
| M3 Two-layer ACL (role + engagement) before scoring | Done | ACL tests and a sweep over the real index; leak rates 0 (RESULTS.md section 4) |
| M4 Six agents in LangGraph, masked traces | Done | agent tests (LLM mocked) |
| M5 Per-claim verifier, confidence, loop-back, abstention | Done | verifier tests |
| M6 FastAPI SSE API | Done | API tests |
| M6 React UI (Streamlit kept as fallback) | Done | Vitest tests; type check clean |
| Sign-in (login, create account, forgot/reset password) and "thinking" indicator | Done (2026-11-14, author's request) | `tests/test_auth.py`, `tests/test_api.py`, `frontend/src/App.test.tsx` |
| M7 Test set of at least 60 questions with every quota met | Built (63); **0 of 63 reviewed** | Author review outstanding: the gold answers are drafts |
| M7 Baseline and ablation | Done | RESULTS.md section 3 |
| M7 Leak rates (role, cross-engagement, content) | Done: all 0 | RESULTS.md section 4 |
| M7 Latency against targets | Measured: **targets not met** by the full system | RESULTS.md section 5 |
| M7 Judge calibration (30 hand labels) | Done; **judge not validated** (kappa near 0) | RESULTS.md section 6 |
| M7 Run-to-run variance | **Not done** | Needs a second paid run (about $5–6) |
| M8 Report generator, tables, SVG figures | Done | `scripts/make_report.py`, `tests/test_make_report.py` |
| M8 Deviations and Future Work | Done | this file |
| One git commit per milestone | **Not done**: replaced by one initial commit | Git was not installed while M0–M8 were built. All work up to 2026-11-18 is in commit `d4b6b6a`, pushed to the private GitHub repository `Asin-1311/Enterprises-Fabric-AI`. Later changes get their own commits. |
| Copyright: supervisor confirms use of the ICAI material | **Author action** | Required before any demo outside the firm |

## 2. Deviations from the project document

| Area | Project document | This build | Why |
|---|---|---|---|
| Language runtime | Python 3.11 (CLAUDE.md) | Python 3.13 | Only 3.13 is installed on the laptop; the author kept it. |
| LLM access | Anthropic API | Claude via OpenRouter, using the same `anthropic` SDK | The author's available key. A direct Anthropic key works unchanged. A real firm would need a data-processing review of any third-party gateway. |
| Determinism | Temperature 0 | No sampling parameters; `output_config.effort` per role | The API removed sampling parameters for the 5.5 models. Outputs vary between runs. |
| Metadata store | PostgreSQL | SQLite via SQLAlchemy | A single-user laptop prototype; SQLAlchemy keeps a later move simple. |
| Vector store | Qdrant server | Qdrant embedded local mode | No Docker. It has the same filtering API but allows only one process at a time. |
| Lexical search | OpenSearch | `rank_bm25` in memory, Lucene-style IDF | At this corpus size, rebuilding after each change is acceptable. |
| Real-time ingestion | Kafka + Debezium CDC, Airflow | `watchdog` folder watcher keyed by content hash | No live source systems; out of scope. |
| Access policy | OPA policy engine | One access rule in code, in three equivalent forms (Qdrant filter, BM25 candidate set, `can_see`), tested for equivalence | Out of scope; the rule is small. |
| Authentication | OIDC/JWT single sign-on | Email + password sign-in run by this API: scrypt password hashes, bearer session tokens (digest stored), self-registration as staff only, reset link printed to the API console | Author's request (2026-11-14). No email server or identity provider in a laptop prototype; SSO is still out of scope. |
| Routing | Learned routing policy with bandit budget allocation | Rule-based routing, with the light model used only as a tie-breaker | Out of scope; rules are explainable and testable. |
| Observability | OpenTelemetry, Grafana, Langfuse | `traces` table (masked), one row per agent step | Out of scope. |
| Answer streaming | Streamed answer | Progress streams live; only the verified answer is streamed | Never shows draft text that verification may remove. Time to first token therefore equals full-answer time. |
| No-retrieval questions | Short model answer | Fixed text, no model call | A misclassified audit question can never be answered from model memory. |
| Verification | Remove unsupported claims | Same, plus "citation repair" for true but uncited sentences | Live runs showed the synthesiser often leaves true sentences uncited. |
| Corpus | Enterprise sources | ICAI study material (Intermediate ch4–11, CA Final, Taxation, Accounting Standards, Companies Act 2013 as enacted) and synthetic firm data | No real firm data may be used. |
| Frontend | React | React 19 + Vite + TypeScript, served by FastAPI; Streamlit fallback kept | Matches the document; the earlier Streamlit deviation is removed. |
| Data model | CLAUDE.md section 8 | Adds `chunks.section_title`, `chunks.chapter`, `traces.effort` | Required by CLAUDE.md sections 6 and 3. |
| API | CLAUDE.md section 9 | Adds `GET /users` and `GET /health`; reindex restricted to partners | Needed by the UI; re-indexing is an admin action. |
| Evaluation judge | Not specified | Opus judges the answers, and Opus is also the system's verifier | Strongest available model. This likely favours the full system on faithfulness (see section 4). |

## 3. Future work

### Out of scope by design (CLAUDE.md section 2)
- Kafka, Debezium, Airflow, Redis, Kubernetes and Helm for production-grade streaming and
  deployment.
- OPA for policy-as-code, and OIDC/JWT single sign-on. The current sign-in also has no
  multi-factor authentication, no lockout after repeated failed attempts, and no real
  email delivery for reset links.
- Live connectors: SharePoint, audit software, DMS, email.
- A learned routing policy and bandit budget allocation, with `RoutingPolicy` /
  `PromptVersion` tables.
- Re-ranker fine-tuning and a feedback learning loop. Feedback is stored today but not used.
- An admin console; OpenTelemetry, Grafana and Langfuse; a response cache.
- Latency-based degradation, multimodal sources (scanned forms, images), a knowledge graph.
- Agents that take actions in firm systems.

### Found during evaluation
1. **Latency.** The full system is far above the 1.5 s and 8 s targets. Options:
   - stream the draft and retract unsupported claims afterwards;
   - verify all claims in one call;
   - use a faster verifier;
   - cache repeated questions.
2. **Time-sensitive questions.** The weakest category. The q008 answer says the evidence
   "does not say what today's date is". Giving the agents the current date would let them
   resolve "this year".
3. **Judge validity.** Kappa is near 0 because almost every label is "correct" (chance
   agreement is high). Next steps:
   - a stratified calibration sample with more partially correct and incorrect answers;
   - a judge from a different model family than the verifier.
4. **Run-to-run variance.** Repeat the answer evaluation at least once and report the
   spread.
5. **Ranking on the extended corpus.** Accounting Standards questions often rank Ind AS
   chapters first. Possible fixes are source-aware routing for the CA Final material, or
   metadata filters on standard numbers.
6. **Author review** of all 63 questions and of the synthetic documents. The results stay
   preliminary until this is done.
