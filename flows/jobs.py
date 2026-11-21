"""The scheduled jobs (CLAUDE.md section 6, "Scheduled pipelines"; M12d).

This file has no Prefect import on purpose. Prefect lives in its own virtual environment,
.venv-flows (author's decision, M12d): every Prefect release pins fastapi<0.151 and
websockets<17, older than the versions the API uses. So each job is a command run in the
project's main .venv, exactly as a person would run it, and the scheduler only needs its
exit code and output. flows/serve.py turns this list into Prefect deployments.

| job                     | when (Asia/Kolkata) | what                                          |
|-------------------------|---------------------|-----------------------------------------------|
| nightly-retention-purge | 01:00 daily         | python -m app.security.retention (M13a)       |
| nightly-reconciliation  | 01:30 daily         | python -m app.ingest.reconcile                |
| nightly-retrieval-eval  | 02:00 daily         | python -m eval.run_retrieval_eval (no LLM calls) |
| nightly-retrieval-gate  | 02:15 daily         | python -m eval.gates retrieval --corpus full (M18a) |
| nightly-routing-policy  | 02:30 daily         | python -m app.adaptive.routing_policy --propose (M15b): learns a new INACTIVE version and replays it; an admin activates it |
| weekly-review-sample    | 09:00 Monday        | python -m scripts.sample_weekly_review draw (M18a): 5% of last week's answers for human review |

The retrieval regression gate runs in CI on the synthetic corpus (M17c) and here, nightly,
on the full corpus with the licensed ICAI chapters (eval/baselines/retrieval.json, M18a).
"""

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TIMEZONE = "Asia/Kolkata"
JOB_TIMEOUT_SECONDS = 3 * 60 * 60


@dataclass(frozen=True)
class Job:
    description: str
    cron: str               # minute hour day-of-month month day-of-week, in TIMEZONE
    argv: list[str]         # arguments after the main .venv's python


JOBS: dict[str, Job] = {
    "nightly-retention-purge": Job(
        "Delete traces and retrieval records older than TRACE_RETENTION_DAYS.", "0 1 * * *",
        ["-m", "app.security.retention"]),
    "nightly-reconciliation": Job(
        "Compare every source with the indexes and repair drift.", "30 1 * * *",
        ["-m", "app.ingest.reconcile"]),
    "nightly-retrieval-eval": Job(
        "Retrieval evaluation (recall, MRR, nDCG, leak checks); no LLM calls.", "0 2 * * *",
        ["-m", "eval.run_retrieval_eval"]),
    "nightly-retrieval-gate": Job(
        "Compare last night's full-corpus retrieval run with eval/baselines/retrieval.json.",
        "15 2 * * *", ["-m", "eval.gates", "retrieval", "--corpus", "full"]),
    "nightly-routing-policy": Job(
        "Learn a new routing policy version from the logs and replay it (never activates it).",
        "30 2 * * *", ["-m", "app.adaptive.routing_policy", "--propose"]),
    "weekly-review-sample": Job(
        "Draw a fixed share of last week's answers for human review (judge drift).",
        "0 9 * * 1", ["-m", "scripts.sample_weekly_review", "draw"]),
}


def app_python() -> str:
    """The main .venv's Python (APP_PYTHON overrides it, e.g. in a container)."""
    if os.environ.get("APP_PYTHON"):
        return os.environ["APP_PYTHON"]
    venv = PROJECT_ROOT / ".venv"
    return str(venv / "Scripts" / "python.exe") if sys.platform == "win32" else str(venv / "bin" / "python")


def run_job(job: Job) -> tuple[int, str]:
    """Run a job in the project root with the main .venv. Returns (exit code, the last
    lines of its output)."""
    done = subprocess.run([app_python(), *job.argv], cwd=PROJECT_ROOT, capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          timeout=JOB_TIMEOUT_SECONDS, check=False)
    tail = "\n".join((done.stdout + done.stderr).strip().splitlines()[-40:])
    return done.returncode, tail
