# Enterprise Fabric AI

A multi-agent adaptive RAG assistant for the audit staff of a chartered accountancy
firm. It is an internship project implementing "Autonomous Multi-Agent Fabric: Real-Time
Adaptive RAG for Enterprise Intelligence".

- **Answers carry citations:** every answer cites the document, section, page and the
  exact supporting span.
- **Access rules hold:** answers respect the user's role and engagement assignment.
- **Current versions come first:** a current policy or circular is preferred over a
  superseded one.
- **Answers are checked:** each one carries a confidence score from per-claim
  verification.
- **Decision support only:** professional judgement and sign-off stay with the
  engagement partner.

All firm and client content in this repository is SYNTHETIC. The ICAI study material used
on the development laptop is licensed and is not in this repository.

## Quick start (lite profile, from a clean clone)
```powershell
python -m venv .venv; .venv\Scripts\Activate.ps1
pip install --index-url https://download.pytorch.org/whl/cpu torch==2.15.0
pip install -r requirements.txt
copy .env.example .env        # then set LLM_AUTH_TOKEN (or ANTHROPIC_API_KEY) and DEMO_PASSWORD
python -m app.ingest.indexer --actor setup
scripts\run_demo.bat          # http://127.0.0.1:8000
```
Without a key, `LLM_MODE=mock` runs the whole pipeline with a labelled stand-in for the
model. It is meant for smoke tests, never for evaluation.

## Documentation
- **[Operations guide](docs/OPERATIONS.md):** run, deploy (Docker, Helm, kind), CI/CD
  and its quality gates, back up and restore, rotate keys, add a source, roll back a prompt
  or routing policy, dashboards and alerts, troubleshooting.
- **[Compliance with the specification](docs/report/COMPLIANCE.md):** every requirement
  with its status, evidence and acceptance test.
- **[API (OpenAPI)](docs/api/openapi.json):** the `/api/v1` schema, checked by the
  contract tests.
- **[Development notes](docs/NOTES.md):** what each milestone built and why.
- **Measured results:** `docs/report/RESULTS.md`, generated from `eval/results/` by
  `scripts/make_report.py`.

## Tests
```powershell
python -m pytest                       # unit tests (lite)
python -m pytest -m integration        # needs: docker compose -f deploy/compose.yaml --profile full up -d
cd frontend; npm test                  # Vitest (run from PowerShell)
python -m scripts.check_compliance --run --run-integration
```
CI runs all of these on every push (`.github/workflows/ci.yaml`).
