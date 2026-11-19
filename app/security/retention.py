"""Trace retention (spec section 12; CLAUDE.md section 12 item 4; Gap Register
R-12-retention; M13a).

    .venv\\Scripts\\python.exe -m app.security.retention [--days 90]

Traces (one row per agent step: masked inputs and outputs) and retrieval records (scores
per sub-query) are kept for TRACE_RETENTION_DAYS (default 90), then deleted. The job runs
nightly (flows/jobs.py). What is NOT purged:
- audit_log: append-only, never purged (the database refuses it anyway);
- questions, answers, citations and feedback: the user's history and the evaluation's
  evidence; they are already masked and are not part of this retention rule.

A trace's age is its `created_at`; traces written before M11 have none, so their
question's time is used. Every purge writes one audit_log row with the counts.
"""

import argparse
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Query, Retrieval, Trace
from app.db.session import write_audit_log


@dataclass(frozen=True)
class PurgeReport:
    cutoff: datetime
    traces_deleted: int
    retrievals_deleted: int


def purge(sessions: sessionmaker[Session], days: int, now: datetime | None = None,
          actor: str = "retention") -> PurgeReport:
    """Delete traces and retrieval records older than `days`. Returns the counts."""
    if days < 1:
        raise ValueError("retention must be at least 1 day")
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=days)).astimezone(timezone.utc).replace(tzinfo=None)
    with sessions() as s:
        old_questions = select(Query.id).where(Query.created_at < cutoff)
        traces = s.execute(delete(Trace).where(or_(
            Trace.created_at < cutoff,
            and_(Trace.created_at.is_(None), Trace.query_id.in_(old_questions))))).rowcount
        retrievals = s.execute(delete(Retrieval).where(Retrieval.created_at < cutoff)).rowcount
        s.commit()
        write_audit_log(s, actor, "retention_purge",
                        f"{traces} traces, {retrievals} retrievals older than {days} days "
                        f"(before {cutoff:%Y-%m-%d %H:%M} UTC)")
    return PurgeReport(cutoff, traces, retrievals)


def main(argv: list[str] | None = None) -> int:
    from app.adapters import open_metadata_store
    from app.config import get_settings

    settings = get_settings()
    parser = argparse.ArgumentParser(description="Purge traces older than the retention.")
    parser.add_argument("--days", type=int, default=settings.trace_retention_days)
    args = parser.parse_args(argv)
    metadata = open_metadata_store(settings)
    metadata.initialise()
    report = purge(metadata.sessions, args.days)
    print(f"Deleted {report.traces_deleted} traces and {report.retrievals_deleted} retrieval "
          f"records older than {args.days} days (before {report.cutoff:%Y-%m-%d %H:%M} UTC).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
