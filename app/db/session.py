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
]
# SQLite cannot add a UNIQUE column with ALTER TABLE, so an older database gets a
# unique index instead (a new database has the constraint from create_all as well).
_ADDED_INDEXES = ["CREATE UNIQUE INDEX IF NOT EXISTS ux_users_email ON users(email)"]


def init_db(engine: Engine) -> None:
    """Create all tables (if missing), add newer columns, and the audit_log triggers."""
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        for table, column, sql_type in _ADDED_COLUMNS:
            existing = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
            if column not in existing:
                conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}")
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
