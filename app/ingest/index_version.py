"""The index version (M16a): a counter in the metadata store, raised after every write to
the indexes, read by the response cache (app/api/cache.py).

It lives in the metadata store (SQLite or PostgreSQL) rather than in memory, so the
ingestion worker and every API worker see the same number in the full profile.
"""

import threading
from datetime import datetime, timezone

from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import IndexState


def current(sessions: sessionmaker[Session]) -> int:
    with sessions() as s:
        row = s.get(IndexState, 1)
        return row.version if row is not None and row.version is not None else 0


def bump(sessions: sessionmaker[Session]) -> None:
    """Raise the version by one (atomically: UPDATE ... SET version = version + 1)."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with sessions() as s:
        done = s.execute(update(IndexState).where(IndexState.id == 1)
                         .values(version=IndexState.version + 1, updated_at=now)).rowcount
        if not done:
            s.add(IndexState(id=1, version=1, updated_at=now))
        s.commit()


class VersionedLock:
    """Wraps the indexer's write lock: when the outermost `with` block ends, the index
    version is raised. Every index write runs inside that lock, so every write (sync,
    re-index, watcher change, worker batch, repair) moves the version. A block that ended
    up writing nothing still raises it: one needless cache miss, never a stale answer."""

    def __init__(self, inner, sessions: sessionmaker[Session]) -> None:
        self.inner, self.sessions = inner, sessions
        self._depth = threading.local()

    def __enter__(self):
        self.inner.__enter__()
        self._depth.n = getattr(self._depth, "n", 0) + 1
        return self

    def __exit__(self, *exc) -> None:
        try:
            self._depth.n -= 1
            if self._depth.n == 0:
                bump(self.sessions)
        finally:
            self.inner.__exit__(*exc)
