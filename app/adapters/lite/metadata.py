"""MetadataStore on SQLite (Phase 1 code, unchanged behaviour)."""

from app.db.session import init_db, make_engine, make_session_factory


class SqliteMetadataStore:
    """SQLite file via SQLAlchemy. The schema is created by `init_db` (Phase 1), which
    also adds newer columns and the append-only triggers on audit_log."""

    def __init__(self, url: str) -> None:
        self.url = url
        self.engine = make_engine(url)
        self.sessions = make_session_factory(self.engine)

    def initialise(self) -> None:
        init_db(self.engine)
