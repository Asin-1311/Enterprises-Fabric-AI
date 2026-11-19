"""MetadataStore on PostgreSQL 16 (full profile), with Alembic migrations.

The same SQLAlchemy models as the lite profile; only the database differs. The schema is
created and upgraded by the migrations in app/db/migrations/, never by create_all, so the
database can change safely over time. The audit log is append-only through PostgreSQL
triggers (created by the first migration).
"""

from pathlib import Path

from sqlalchemy import Engine, create_engine

from app.db.session import make_session_factory

MIGRATIONS = Path(__file__).resolve().parents[2] / "db" / "migrations"


def migrate(engine: Engine, revision: str = "head") -> None:
    """Upgrade the database behind `engine` to `revision` with Alembic."""
    from alembic import command
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", MIGRATIONS.as_posix())
    with engine.begin() as connection:
        cfg.attributes["connection"] = connection
        command.upgrade(cfg, revision)


class PostgresMetadataStore:
    def __init__(self, url: str) -> None:
        self.url = url
        # Times are stored as naive UTC (as in SQLite); pin the session time zone so
        # PostgreSQL never shifts them to the server's local time.
        self.engine = create_engine(url, pool_pre_ping=True,
                                    connect_args={"options": "-c timezone=UTC"})
        self.sessions = make_session_factory(self.engine)

    def initialise(self) -> None:
        migrate(self.engine)
