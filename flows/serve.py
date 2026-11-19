"""Serve the scheduled jobs with Prefect (M12d). Runs in .venv-flows, not .venv:

    python -m venv .venv-flows
    .venv-flows\\Scripts\\python.exe -m pip install -r requirements-flows.txt
    .venv-flows\\Scripts\\python.exe -m flows.serve                 # schedule; Ctrl+C stops
    .venv-flows\\Scripts\\python.exe -m flows.serve --run-once nightly-reconciliation

`serve` keeps the deployments in flows/jobs.py running on their cron schedules, using
Prefect's built-in temporary local server (author's decision: no extra container). Each
flow runs its job as a command in the main .venv; a non-zero exit code fails the flow
run, so failures are visible in Prefect's run history and logs.
"""

import argparse
import sys

from prefect import flow, get_run_logger, serve
from prefect.schedules import Cron

from flows.jobs import JOBS, TIMEZONE, Job, run_job


def make_flow(name: str, job: Job):
    @flow(name=name, description=job.description, log_prints=True)
    def job_flow() -> int:
        logger = get_run_logger()
        code, tail = run_job(job)
        logger.info("%s finished with exit code %d\n%s", name, code, tail)
        if code != 0:
            raise RuntimeError(f"{name} failed with exit code {code}")
        return code

    return job_flow


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve the scheduled jobs with Prefect.")
    parser.add_argument("--run-once", choices=sorted(JOBS), help="run one job now, then exit")
    args = parser.parse_args(argv)
    if args.run_once:
        make_flow(args.run_once, JOBS[args.run_once])()
        return 0
    serve(*[make_flow(name, job).to_deployment(name=name, schedule=Cron(job.cron, timezone=TIMEZONE))
            for name, job in JOBS.items()])
    return 0


if __name__ == "__main__":
    sys.exit(main())
