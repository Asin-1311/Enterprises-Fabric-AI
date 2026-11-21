# Operations guide

How to run, deploy, back up, secure and troubleshoot Enterprise Fabric AI. Commands are for
PowerShell on the Windows development laptop unless they say otherwise; inside containers
and on CI they run in Linux.

Every procedure is marked:
- **[tested]**: an automated test or CI job runs it;
- **[run once]**: run by hand on the laptop on the date given;
- **[procedure]**: written down but not yet exercised by a test.

## 1. The two profiles

| | `lite` | `full` |
|---|---|---|
| Use | Unit tests, CI, quick demo, a clean clone | The specified architecture, integration tests, load tests |
| Stores | SQLite, embedded Qdrant, `rank_bm25` | PostgreSQL 16, Qdrant server, OpenSearch, Kafka, Redis, MinIO, Debezium, Keycloak, OPA |
| Start | `scripts\run_demo.bat` | `docker compose ... --profile full up -d` (below) |

Select the profile with `PROFILE=lite|full` in `.env`. Every setting lives in `app/config.py`
and can be overridden in `.env` or the environment. Copy `.env.example` to `.env` first.

## 2. Start and stop

### Lite (laptop) [tested]
```powershell
.venv\Scripts\Activate.ps1
python -m app.ingest.indexer --actor setup   # index data/corpus (synthetic only on a clean clone)
scripts\run_demo.bat                         # API and React UI on http://127.0.0.1:8000
```
Only one program at a time may open the embedded Qdrant folder, so stop the demo before
running the indexer or an evaluation.

### Full stack (Docker Desktop) [tested in CI]
```powershell
docker compose --env-file .env -f deploy/compose.yaml --profile full up -d --wait
python -m scripts.keycloak_setup                 # demo passwords for the synthetic users
python -m scripts.make_synthetic_dms             # the synthetic DMS database (CDC source)
python -m scripts.register_debezium_connector    # Debezium -> Kafka
docker compose --env-file .env -f deploy/compose.yaml --profile full down    # stop (data volumes kept)
```
- Smaller subsets: `--profile core` (PostgreSQL, Qdrant, OpenSearch, Redis), `ingest`
  (Kafka, MinIO), `identity` (Keycloak, OPA), `observability` (Prometheus, Grafana,
  Langfuse; run `python -m scripts.observability_setup` once first).
- On the laptop's 3.5 GB Docker VM, `full` (about 2.4 GB) and `observability` (about 2.7 GB)
  cannot run together. Stop one before starting the other.

### Scheduled jobs (Prefect) [run once, 2026-11-19]
`.venv-flows\Scripts\python.exe -m flows.serve` keeps the nightly jobs of `flows/jobs.py`
running: retention purge, reconciliation, retrieval evaluation and routing-policy
proposal. Prefect has its own virtual environment (see `docs/NOTES.md`, M12d).

## 3. Deploy

### Container images [tested in CI]
```powershell
docker build -f deploy/docker/app.Dockerfile --target api    -t enterprise-fabric/api:dev .
docker build -f deploy/docker/app.Dockerfile --target worker -t enterprise-fabric/worker:dev .
docker build -f deploy/docker/frontend.Dockerfile            -t enterprise-fabric/frontend:dev .
```
- The images run as non-root users and need no internet at run time, because the models
  are built in.
- They hold no `.env`, local index or ICAI material (`.dockerignore`).
- CI scans them with Trivy and fails on any HIGH or CRITICAL vulnerability that has a fix.

### Kubernetes with Helm [tested: kind on the laptop 2026-11-21, and the release job]
```powershell
python -m scripts.kind_smoke              # kind cluster, load images, lint, install, helm test
python -m scripts.kind_smoke --delete     # the same, then delete the cluster
```
For a real cluster:
1. Push the images to a registry and set `images.*.repository` and `tag`.
2. Create a Secret with the keys of `secrets.values` and set `secrets.existingSecret`.
3. Install:
   `helm install ef deploy/helm/enterprise-fabric -f my-values.yaml`.

Then `helm test ef` runs `scripts/smoke_deploy.py` inside the cluster: health, metrics,
sign-in and one answered question.

- **`lite`** (the default) is one self-contained API pod. The chart refuses replicas and
  the HPA for it.
- **`full`** needs `external.*` addresses of operator-managed PostgreSQL, Qdrant,
  OpenSearch, Kafka and Redis, and adds the worker.
- Use `llm.mode: live` with a real key in the Secret for actual answers.
- `mock` is for smoke tests only. Every mock answer says it is a mock.

### CI/CD (GitHub Actions) [tested]
`.github/workflows/ci.yaml` runs on every push and pull request:
- lint and unit tests;
- frontend type check, Vitest and `npm audit`;
- `opa test`;
- `pip-audit`;
- the evaluation gates;
- `helm lint`;
- image builds with Trivy and a container smoke test;
- the docker compose integration job.

When everything passes on a push to `phase2` or `main`, `release.yaml` pushes the images
to `ghcr.io/<owner>/enterprise-fabric-{api,worker,frontend}:<commit>`. It then installs
them in the `staging` namespace on a kind cluster and runs the smoke test.

## 4. Quality gates (CI) [tested]
| Gate | Command | Fails when |
|---|---|---|
| Retrieval regression | `python -m eval.run_retrieval_eval --corpus synthetic` then `python -m eval.gates retrieval` | any metric (overall or per source type) drops more than `RETRIEVAL_TOLERANCE` (0.02) below `eval/baselines/retrieval_synthetic.json`; any leak; the question set changed |
| Routing replay | `python -m eval.gates routing-replay` | the code's routing loses more than `REPLAY_TOLERANCE` (0.02) on `eval/replay/routing_replay.jsonl` |
| Prompt replay | `python -m eval.gates prompts` | a seed prompt in the code changed and there is no PASSING `*_prompt_replay` report for exactly that text |

The gates need an index of the committed synthetic documents only, which is what a clean
clone builds. On the laptop the licensed ICAI chapters are present, so run the gates in a
clean clone; this is how the baselines were made on 2026-11-21:
```powershell
git clone . $env:TEMP\efa-clone; cd $env:TEMP\efa-clone      # no .env, no ICAI material
$env:LLM_MODE="mock"; $env:MODEL_LIGHT="x"; $env:MODEL_SYNTH="x"; $env:MODEL_VERIFY="x"
<project>\.venv\Scripts\python.exe -m app.ingest.indexer --actor local-gate
<project>\.venv\Scripts\python.exe -m eval.run_retrieval_eval --corpus synthetic
<project>\.venv\Scripts\python.exe -m eval.gates retrieval
```
`--corpus synthetic` refuses to run on an index that holds any non-synthetic document.

### Accepting a new baseline [procedure]
Accept a new baseline only after an intended change: new questions, a better ranking, or
a deliberately accepted drop.
1. Run the evaluation or replay.
2. Commit its `eval/results/` folder.
3. Copy the values from that run:
   `python -m eval.gates accept retrieval --run eval/results/<run>` (or
   `accept routing-replay --run ...`, or `accept prompts` after a passing prompt replay).

Baselines are never typed by hand, and the gate checks that they match their run.

## 5. Back up and restore

### Lite [procedure]
Stop the API, then copy `data/fabric.db` and `data/qdrant/` (as
`scripts/migrate_chunk_ids.py` does, to `data/backups/`). Restore by copying them back
while the API is stopped.

### Full [procedure]
```powershell
# PostgreSQL: metadata, users, answers, traces, audit log, prompt and routing versions
docker compose --env-file .env -f deploy/compose.yaml exec -T postgres pg_dump -U fabric -Fc fabric > fabric.dump
docker compose --env-file .env -f deploy/compose.yaml exec -T postgres pg_restore -U fabric -d fabric --clean < fabric.dump
# Qdrant: a snapshot of the "chunks" collection
curl.exe -X POST http://127.0.0.1:6333/collections/chunks/snapshots
```
- PostgreSQL is the source of truth: it holds every document's metadata and every chunk's
  text.
- Restoring from a Qdrant snapshot saves re-embedding time.
- Instead of restoring Qdrant and OpenSearch, you can rebuild both from PostgreSQL with
  `POST /api/v1/documents/reindex {"all": true}` (admin), which re-embeds and re-indexes
  everything.
- `audit_log` is append-only, enforced in the database by triggers that refuse UPDATE,
  DELETE and TRUNCATE.
  - Restore into a new, empty database, or with `--clean`, which drops and re-creates the
    tables as their owner. Rows are never edited one by one.
  - Never edit the audit log by hand.

## 6. Keys and secrets

- **Where secrets live:** only in `.env` (git-ignored) on the laptop, and in Kubernetes
  Secrets in a cluster. Never in git, images, logs or connector configuration.
  Connectors reference secrets by name.
- **Rotate the LLM key [procedure]:** create a new key at the provider, put it in `.env`
  or the Secret, restart the API (`kubectl rollout restart deploy/ef-api`), then revoke
  the old key.
- **Rotate a service password [procedure]:**
  1. Change it in the service: PostgreSQL `ALTER USER`, the Keycloak admin console, or
     the MinIO admin.
  2. Change it in `.env` or the Secret, including `DATABASE_URL`.
  3. Restart the API and the worker.
- **Rotate demo passwords [tested]:** `python -m scripts.keycloak_setup` sets
  `KEYCLOAK_DEMO_PASSWORD` for the synthetic users. `DEMO_PASSWORD` is the lite sign-in.
- **Webhook HMAC secret [procedure]:**
  - The webhook source's connector configuration names the secret as `env:<NAME>`, and
    the value lives in `.env` or the Secret.
  - Change it in the sender and under that name at the same time, then restart the API.
  - Requests signed with the old secret are then refused.
- **CI:** no repository secret is needed. `scripts/ci_env.py` generates random passwords
  per run and masks them in the logs. Images are pushed with the built-in
  `GITHUB_TOKEN`.

## 7. Add a source [tested]
- **From the admin console:** `/admin`, Sources, then Register (admin users only).
- **From the API:** `POST /api/v1/sources` with `{id, type, connector_config, owner,
  classification}`.
- The connector configuration is validated and the registration is written to the audit
  log.
- Check the source with `GET /api/v1/sources/{id}/status`: documents, chunks, last event
  and freshness lag.
- `POST /api/v1/sources/{id}/pause` and `/resume` stop and restart its ingestion.
- Re-index one document or one source with `POST /api/v1/documents/reindex {"doc_id": ...}`
  or `{"source_id": ...}`.
- The demo sources (Git, S3, webhook, DMS, and the mock Confluence, Jira and SharePoint) are
  registered by `python -m scripts.register_demo_sources`.

## 8. Roll back a prompt or routing policy [tested]
- **Prompt:** in the admin console, Prompts, pick a version that was active before, then
  Activate. Through the API: `POST /api/v1/admin/prompts/{id}/activate`.
  - A version that was active before needs no new replay: that is a rollback.
  - A NEW version needs a passing replay report:
    `python -m app.learning.prompt_replay --version-id <id> --confirm-paid` (paid model
    calls; without `--confirm-paid` it only counts them).
- **Routing policy:** `PUT /api/v1/routing-policy {"version": <n>}` (admin). The same
  rule applies: a new version needs a passing replay report, and a previously active one
  is a rollback.
- Every activation and rollback is written to `audit_log`.

## 9. Read the dashboards and alerts
- **Where:** Grafana at http://127.0.0.1:3001 (profile `observability`), dashboard
  "Enterprise Fabric AI". Prometheus is at http://127.0.0.1:9090 and Langfuse at
  http://127.0.0.1:3000.
- **Panels:**
  - query volume;
  - latency per agent (p50 and p95);
  - time to first draft token, and full answer time;
  - tokens;
  - estimated cost per answer, from token counts times list prices. This is an estimate,
    not the bill;
  - retrieval hit rate;
  - verification failure rate;
  - degradation rate;
  - cache hit rate;
  - ingestion lag per source;
  - pre-filter/OPA disagreements;
  - API errors.
- **Alerts** (`deploy/prometheus/alerts.yaml`; what to do):
  - *p95 latency above target:* look at the per-agent latency panel. Degradation should
    already be cutting optional work for low-risk questions.
  - *Verification failure rate above 20%:* recent prompt or routing activations are the
    first suspects. Roll back (section 8).
  - *Ingestion lag above 15 minutes:* check `GET /sources/{id}/status`, the worker's logs
    and Kafka/Debezium (`http://127.0.0.1:8083/connectors/dms-documents/status`).
  - *Any pre-filter/OPA disagreement:* a security error, and the chunk was already
    dropped. Compare the document's metadata with `policy/document_access.rego`. Do not
    disable either check.
  - *Error rate above 5%:* the API logs have the details. Clients only ever see a generic
    message.

## 10. Troubleshooting (all seen during development)
| Symptom | Cause and fix |
|---|---|
| Docker Desktop stops responding | Restart Docker Desktop, then `compose up -d --wait` and `scripts.keycloak_setup` again |
| `Storage folder ... is already accessed by another instance of Qdrant client` | Lite's embedded Qdrant allows one program: stop the demo or the other script |
| Vitest fails with `Invalid Chai property` from Git Bash | Run `npm test` from PowerShell (Git Bash's drive-letter casing breaks jest-dom) |
| A container says `couldn't connect to huggingface.co` | The model cache in the image is incomplete: rebuild; `deploy/docker/fetch_models.py` checks the models load offline during the build |
| `npm ci` fails with `ECONNRESET` during an image build | Network interruption; the Dockerfile retries fetches, so build again |
| `UnicodeDecodeError: 'charmap'` from a script that runs kind or helm | Windows' default code page; the scripts decode tool output as UTF-8 |
| Langfuse web restarts (out of memory) | It needs about 1 GB: run the `observability` profile without `full` |
| A Keycloak test times out right after the stack started | Keycloak was still starting; run it again once `/health/ready` answers |
| The indexer says `ICAI study material not found` | Expected on a clean clone: only the synthetic documents are indexed (the ICAI PDFs are licensed and never in git) |
