"""M13a: trace retention.

Covers: R-12-retention. Traces and retrieval records older than the setting are deleted;
newer ones, questions, answers and the append-only audit_log are untouched.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select, update

from app.db.models import AuditLog, Query, Retrieval, Trace, User
from app.db.session import init_db, make_engine, make_session_factory
from app.security.retention import purge

NOW = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)


def _naive(days_ago: float) -> datetime:
    return (NOW - timedelta(days=days_ago)).replace(tzinfo=None)


@pytest.fixture()
def sessions(tmp_path):
    engine = make_engine(f"sqlite:///{(tmp_path / 'r.db').as_posix()}")
    init_db(engine)
    factory = make_session_factory(engine)
    with factory() as s:
        s.add(User(user_id="S1", name="S", role="staff", synthetic=True))
        s.flush()
        old_q = Query(user_id="S1", text="old question", created_at=_naive(200))
        new_q = Query(user_id="S1", text="new question", created_at=_naive(5))
        s.add_all([old_q, new_q])
        s.flush()
        s.add_all([
            Trace(query_id=old_q.id, agent="synthesis", created_at=_naive(120)),   # purged
            Trace(query_id=old_q.id, agent="verification", created_at=None),      # pre-M11: by question
            Trace(query_id=new_q.id, agent="synthesis", created_at=_naive(89)),   # kept
            Trace(query_id=new_q.id, agent="verification", created_at=None),      # kept (new question)
            Retrieval(query_id=old_q.id, sub_query="q", method="dense", scores={}, created_at=_naive(91)),
            Retrieval(query_id=new_q.id, sub_query="q", method="dense", scores={}, created_at=_naive(2)),
        ])
        s.add(AuditLog(actor="test", action="sign_in", target="S1", created_at=_naive(400)))
        s.commit()
        # Traces written before M11 have no created_at (the ORM would fill in a default).
        s.execute(update(Trace).where(Trace.agent == "verification").values(created_at=None))
        s.commit()
    return factory


def test_purge_deletes_only_what_is_older_than_the_setting(sessions) -> None:
    """Covers: R-12-retention."""
    report = purge(sessions, days=90, now=NOW)
    assert (report.traces_deleted, report.retrievals_deleted) == (2, 1)
    with sessions() as s:
        assert sorted(t.agent for t in s.scalars(select(Trace))) == ["synthesis", "verification"]
        assert s.scalar(select(func.count()).select_from(Retrieval)) == 1
        assert s.scalar(select(func.count()).select_from(Query)) == 2  # history untouched
        actions = [a.action for a in s.scalars(select(AuditLog).order_by(AuditLog.id))]
    assert actions == ["sign_in", "retention_purge"]  # the old audit row survives; one new row
    assert purge(sessions, days=90, now=NOW).traces_deleted == 0  # idempotent


def test_retention_must_be_at_least_a_day(sessions) -> None:
    with pytest.raises(ValueError):
        purge(sessions, days=0, now=NOW)
