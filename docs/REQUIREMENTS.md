# Requirements: use cases, enterprise sources, quality targets

The requirements of "Autonomous Multi-Agent Fabric: Real-Time Adaptive RAG for Enterprise
Intelligence" as applied to an audit firm. The requirement-by-requirement status is in
[report/COMPLIANCE.md](report/COMPLIANCE.md); measured values are only in
[report/RESULTS.md](report/RESULTS.md) and `eval/results/`.

## 1. Users and use cases
The users are the synthetic staff of "Example & Associates, Chartered Accountants"
(`data/users.csv`): article assistants and seniors (staff), managers and partners. One
staff member also has the admin flag, which gives the admin console but no extra
document access.

| # | Use case | Example question | What the system must do |
|---|---|---|---|
| U1 | Look up a Standard on Auditing | "What are the sufficiency and appropriateness requirements for audit evidence under SA 500?" | Answer from the ICAI material with document, section, page and span citations |
| U2 | Apply the firm's current policy | "What is our firm's current overall materiality benchmark for manufacturing clients?" | Prefer the current version, and name the superseded one with both dates |
| U3 | Combine a standard and a firm rule | "Within how many days must the final audit file be assembled?" | Present the SA 230 limit and the firm's stricter rule, and their difference |
| U4 | Follow an audit programme | "Which NPA classification checks are on our bank branch audit checklist?" | Answer from the checklist, procedurally |
| U5 | Engagement work | "What did the planning memo for Northwind Textiles identify as significant risks?" | Only for the engagement team and partners; never confirm restricted material exists |
| U6 | Time-sensitive compliance | "When is the tax audit report due this year?" | Use the current calendar and circulars (the dates are labelled placeholders) |
| U7 | Time-bounded comparison | "How did the bank branch audit circular change between FY 2024-25 and FY 2025-26?" | Sub-queries per year, and a change log |
| U8 | Administration | Register a source, inspect a trace, roll back a prompt or routing policy | Admin console and API, every action in `audit_log` |
| U9 | Feedback | Helpful or not helpful, corrections, implicit signals | Feeds routing, the re-ranker dataset and candidate evaluation questions |

Every answer carries citations and a confidence score (or a "reduced checking" notice
under load), and ends with the professional-judgement note: decision support only.
Sign-off stays with the engagement partner.

## 2. Enterprise source inventory
Every registered source (the `sources` table). The documents are SYNTHETIC, except the
licensed ICAI study material, which is local only and never in git.

| Source id | Connector | Source type | Documents | Notes |
|---|---|---|---|---|
| fs-standards | filesystem | standards | 218 | ICAI study material, accounting standards, tax, Companies Act (licensed; local only) |
| fs-firm_policy | filesystem | firm_policy | 10 | methodology, materiality, documentation, independence, quality, acceptance, key custody (restricted) |
| fs-circulars | filesystem | circulars | 10 | technical and regulatory circulars, including superseded versions |
| fs-checklists | filesystem | checklists | 5 | audit programmes and checklists |
| fs-templates | filesystem | templates | 3 | engagement letter, representation letter, report formats |
| fs-engagement | filesystem | engagement | 11 | three fictional engagements; one note classified restricted |
| fs-calendar | filesystem | calendar | 2 | compliance calendars FY 2025-26 and FY 2026-27 |
| fs-faq | filesystem | faq | 1 | article assistant helpdesk FAQ |
| git-firm-methodology | git | firm_policy | from the repository | a local synthetic Git repository, polled for commits |
| s3-firm-documents | s3 | firm_policy | from the bucket | MinIO bucket `firm-documents` |
| conf-aud | confluence | firm_policy | from the space | recorded synthetic fixtures; no live tenant |
| jira-audq | jira | faq | from the project | recorded synthetic fixtures; no live tenant |
| sp-policies | sharepoint | firm_policy | from the site | recorded synthetic fixtures; no live tenant |
| (webhook) | webhook | per request | pushed | `POST /api/v1/webhooks/documents`, HMAC-signed |
| (dms) | dms (Debezium CDC) | per row | streamed | the synthetic DMS PostgreSQL table `dms.documents` |

The filesystem counts come from `data/manifest.csv`. The connector sources are registered
by `python -m scripts.register_demo_sources`.

## 3. Quality targets
These are the targets each evaluation is measured against. Whether they are met is
stated, with the measured values, in RESULTS.md and COMPLIANCE.md.

| Area | Target | Where it is measured |
|---|---|---|
| Retrieval | recall@5, recall@10, MRR and nDCG@10 per source type do not drop more than 0.02 below the accepted baseline | `eval/gates.py` (CI: synthetic corpus; nightly: full corpus) |
| Access control | 0 role leaks, 0 cross-engagement leaks, 0 restricted chunks to an external model | retrieval and answer evaluations; `tests/test_model_routing.py` |
| Answers | better than the single-pass baseline on faithfulness and citation precision; correctness and relevance reported | `eval/run_answer_eval.py` (two runs, spread reported) |
| Judge | validated only at Cohen's kappa >= 0.6 against the author's labels | `eval/run_judge_calibration.py` |
| Verification | confidence = supported factual claims / factual claims; below 0.6: retry, then clarify or escalate | agent tests |
| Latency | p95 time to first draft token < 1.5 s; p95 full verified answer < 8 s, at 1, 5 and 10 users | `scripts/run_load_tests.py` (k6 and Locust) |
| Freshness | a source change reaches every index within 60 s; ingestion lag alert above 15 minutes | integration test (Debezium); Prometheus alert |
| Replay | a new prompt or routing policy is activated only with a passing replay; recorded traces reproduce | `eval/gates.py`, `eval/trace_replay.py` |
