"""Alembic migrations build exactly the schema the models describe (no drift).

Runs on SQLite in the normal suite; tests/integration/test_full_backends.py repeats the
checks on PostgreSQL. Covers: R-6-stack (PostgreSQL + Alembic part).
"""

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.exc import DatabaseError

from app.adapters.full.metadata import migrate
from app.db.models import Base
from app.db.session import make_engine


def schema_drift(engine) -> list:
    """Differences between the database and the ORM models (empty list = none)."""
    with engine.connect() as conn:
        return compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}),
                                Base.metadata)


@pytest.fixture()
def migrated(tmp_path):
    engine = make_engine(f"sqlite:///{(tmp_path / 'migrated.db').as_posix()}")
    migrate(engine)
    return engine


def test_migrations_build_exactly_the_models_schema(migrated) -> None:
    """Covers: R-6-stack. A model change without a migration fails this test."""
    assert schema_drift(migrated) == []


def test_migrated_audit_log_is_append_only(migrated) -> None:
    with migrated.begin() as conn:
        conn.execute(text("INSERT INTO audit_log (actor, action, target, created_at) "
                          "VALUES ('t', 'a', NULL, '2026-11-18 00:00:00')"))
    for statement in ("UPDATE audit_log SET actor = 'x'", "DELETE FROM audit_log"):
        with pytest.raises(DatabaseError, match="append-only"):
            with migrated.begin() as conn:
                conn.execute(text(statement))


def test_migrations_are_idempotent_and_reversible(migrated) -> None:
    migrate(migrated)  # already at head: nothing to do
    assert schema_drift(migrated) == []
    migrate_down(migrated)
    assert set(inspect(migrated).get_table_names()) == {"alembic_version"}


def migrate_down(engine) -> None:
    from alembic import command
    from alembic.config import Config

    from app.adapters.full.metadata import MIGRATIONS

    cfg = Config()
    cfg.set_main_option("script_location", MIGRATIONS.as_posix())
    with engine.begin() as connection:
        cfg.attributes["connection"] = connection
        command.downgrade(cfg, "base")
