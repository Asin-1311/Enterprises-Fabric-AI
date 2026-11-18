"""Tests for the SQLite data model (app/db)."""

import pytest
from sqlalchemy import Engine, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import AuditLog, AuditLogImmutableError
from app.db.session import write_audit_log

EXPECTED_TABLES = {
    "users",
    "engagements",
    "engagement_members",
    "documents",
    "chunks",
    "queries",
    "traces",
    "answers",
    "citations",
    "feedback",
    "audit_log",
}


def test_all_tables_created(engine: Engine) -> None:
    assert EXPECTED_TABLES <= set(inspect(engine).get_table_names())


def test_init_db_upgrades_an_older_database(tmp_path) -> None:
    """A database created before the traces.effort column existed gets the column added."""
    from app.db.session import init_db, make_engine

    engine = make_engine(f"sqlite:///{(tmp_path / 'old.db').as_posix()}")
    with engine.begin() as conn:  # the M2-era traces table, without `effort`
        conn.exec_driver_sql(
            "CREATE TABLE traces (id INTEGER PRIMARY KEY, query_id INTEGER, agent VARCHAR, "
            "input TEXT, output TEXT, model VARCHAR, tokens_in INTEGER, tokens_out INTEGER, "
            "latency_ms FLOAT)")
    init_db(engine)
    init_db(engine)  # running twice is harmless
    assert "effort" in {c["name"] for c in inspect(engine).get_columns("traces")}


def test_audit_log_insert_works(session: Session) -> None:
    write_audit_log(session, actor="system", action="reindex", target="doc-1")
    row = session.scalars(select(AuditLog)).one()
    assert row.action == "reindex" and row.created_at is not None


def test_audit_log_orm_update_blocked(session: Session) -> None:
    write_audit_log(session, actor="system", action="reindex", target="doc-1")
    row = session.scalars(select(AuditLog)).one()
    row.action = "tampered"
    with pytest.raises(AuditLogImmutableError):
        session.commit()


def test_audit_log_orm_delete_blocked(session: Session) -> None:
    write_audit_log(session, actor="system", action="reindex", target="doc-1")
    row = session.scalars(select(AuditLog)).one()
    session.delete(row)
    with pytest.raises(AuditLogImmutableError):
        session.commit()


@pytest.mark.parametrize(
    "sql",
    ["UPDATE audit_log SET action = 'tampered'", "DELETE FROM audit_log"],
)
def test_audit_log_raw_sql_blocked(session: Session, sql: str) -> None:
    write_audit_log(session, actor="system", action="reindex", target="doc-1")
    with pytest.raises(IntegrityError, match="append-only"):
        session.execute(text(sql))
