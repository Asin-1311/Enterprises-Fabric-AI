"""Database engine and session helpers."""

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import AuditLog, Base


def make_engine(url: str) -> Engine:
    """Create a SQLite engine with foreign-key enforcement switched on."""
    engine = create_engine(url)

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_conn, _record) -> None:  # noqa: ANN001
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    return engine


# Database-level guard: even raw SQL cannot change or remove audit_log rows.
_AUDIT_TRIGGERS = (
    """CREATE TRIGGER IF NOT EXISTS audit_log_no_update
       BEFORE UPDATE ON audit_log
       BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END""",
    """CREATE TRIGGER IF NOT EXISTS audit_log_no_delete
       BEFORE DELETE ON audit_log
       BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END""",
)


# Columns added after the first database was created: (table, column, SQL type).
# create_all() never alters an existing table, so these are added here if missing.
_ADDED_COLUMNS = [
    ("traces", "effort", "VARCHAR"),
    ("users", "email", "VARCHAR"),
    ("users", "password_hash", "VARCHAR"),
    # M11 (CLAUDE.md section 9). New tables come from create_all above.
    ("users", "is_admin", "BOOLEAN NOT NULL DEFAULT 0"),
    ("documents", "source_id", "VARCHAR REFERENCES sources(id)"),
    ("documents", "uri", "VARCHAR"),
    ("documents", "owner", "VARCHAR"),
    ("documents", "classification", "VARCHAR"),
    ("documents", "updated_at", "DATETIME"),
    ("chunks", "content_hash", "VARCHAR"),
    ("chunks", "chunking_profile", "VARCHAR"),
    ("chunks", "embedding_ref", "VARCHAR"),
    ("queries", "injection_hits", "JSON"),
    ("traces", "prompt_version_id", "INTEGER REFERENCES prompt_versions(id)"),
    ("traces", "policy_version_id", "INTEGER REFERENCES routing_policies(id)"),
    ("traces", "created_at", "DATETIME"),
    ("answers", "degraded", "BOOLEAN NOT NULL DEFAULT 0"),
    ("answers", "flagged_claims", "JSON"),
    ("citations", "span_start", "INTEGER"),
    ("citations", "span_end", "INTEGER"),
    # M12b
    ("chunks", "legacy_chunk_id", "VARCHAR"),
    # M12c
    ("sources", "sync_state", "JSON"),
    # M15b
    ("feedback", "created_at", "DATETIME"),
    # M15c
    ("prompt_versions", "eval_report", "JSON"),
    ("prompt_versions", "activated_at", "DATETIME"),
]
# Run once, right after the column is added to an older database.
_BACKFILLS = {
    # Before M15c every recorded prompt version was activated when it was recorded.
    ("prompt_versions", "activated_at"):
        "UPDATE prompt_versions SET activated_at = created_at WHERE activated_at IS NULL",
}
# SQLite cannot add a UNIQUE column with ALTER TABLE, so an older database gets a
# unique index instead (a new database has the constraint from create_all as well).
_ADDED_INDEXES = [
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_users_email ON users(email)",
    "CREATE INDEX IF NOT EXISTS ix_documents_source_id ON documents(source_id)",
    "CREATE INDEX IF NOT EXISTS ix_chunks_legacy_chunk_id ON chunks(legacy_chunk_id)",
]


def init_db(engine: Engine) -> None:
    """Create all tables (if missing), add newer columns, and the audit_log triggers."""
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        for table, column, sql_type in _ADDED_COLUMNS:
            existing = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
            if column not in existing:
                conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}")
                if (table, column) in _BACKFILLS:
                    conn.exec_driver_sql(_BACKFILLS[(table, column)])
        for ddl in _ADDED_INDEXES:
            conn.exec_driver_sql(ddl)
        for ddl in _AUDIT_TRIGGERS:
            conn.exec_driver_sql(ddl)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Return a session factory bound to `engine`."""
    return sessionmaker(bind=engine, expire_on_commit=False)


def write_audit_log(session: Session, actor: str, action: str, target: str | None) -> None:
    """Append one row to audit_log. This is the only write path for that table."""
    session.add(AuditLog(actor=actor, action=action, target=target))
    session.commit()
