"""Alembic environment for the metadata store (CLAUDE.md section 9).

Used by the full profile (PostgreSQL) and by the schema-drift test on SQLite. The lite
profile itself keeps Phase 1's `init_db` (author's decision, M10).

The database comes from, in order: a connection passed by the caller
(`config.attributes["connection"]`, used by app/adapters/full/metadata.py and the tests),
the `sqlalchemy.url` option, or DATABASE_URL from .env.
"""

from alembic import context
from sqlalchemy import create_engine

from app.db.models import Base

config = context.config
target_metadata = Base.metadata


def _url() -> str:
    url = config.get_main_option("sqlalchemy.url")
    if url:
        return url
    from app.config import get_settings

    url = get_settings().database_url
    if not url:
        raise RuntimeError("Set DATABASE_URL in .env (see .env.example) to run migrations.")
    return url


def _configure(**kwargs) -> None:
    context.configure(target_metadata=target_metadata, compare_type=True, **kwargs)


def run_offline() -> None:
    """Write the SQL to stdout instead of running it (`alembic upgrade head --sql`)."""
    _configure(url=_url(), literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        _configure(connection=connection,
                   render_as_batch=connection.dialect.name == "sqlite")
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = create_engine(_url())
    with engine.connect() as conn:
        _configure(connection=conn, render_as_batch=conn.dialect.name == "sqlite")
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_offline()
else:
    run_online()
