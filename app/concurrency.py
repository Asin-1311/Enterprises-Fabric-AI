"""A readers-writer lock: many questions at once, but never during an index write.

Phase 1 held one lock for the whole of every question, so two users waited for each
other for the full 20-40 seconds of LLM work (audit finding R-5-par). Now:

- answering a question takes the READ side: any number of questions run together;
- changing the indexes (re-index, folder watcher) takes the WRITE side: it waits until
  the running questions finish, and new questions wait until the write is done.

Writers are preferred (a waiting writer blocks new readers), so a steady stream of
questions cannot postpone a re-index for ever. The write side is re-entrant for the
thread that holds it, because one sync calls other locked indexer methods.
"""

import threading
from collections.abc import Iterator
from contextlib import contextmanager


class ReadWriteLock:
    """Many readers or one (re-entrant) writer."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._readers = 0
        self._writer: int | None = None  # thread id of the writer
        self._write_depth = 0
        self._writers_waiting = 0
        self.writer = _WriterHandle(self)  # `with lock.writer:` (what the indexer uses)

    @contextmanager
    def reading(self) -> Iterator[None]:
        """Hold the read side for the duration of a `with` block."""
        me = threading.get_ident()
        with self._cond:
            if self._writer == me:  # the writer may also read
                self._readers += 1
            else:
                while self._writer is not None or self._writers_waiting:
                    self._cond.wait()
                self._readers += 1
        try:
            yield
        finally:
            with self._cond:
                self._readers -= 1
                self._cond.notify_all()

    def acquire_write(self) -> None:
        me = threading.get_ident()
        with self._cond:
            if self._writer == me:
                self._write_depth += 1
                return
            self._writers_waiting += 1
            try:
                while self._writer is not None or self._readers:
                    self._cond.wait()
            finally:
                self._writers_waiting -= 1
            self._writer, self._write_depth = me, 1

    def release_write(self) -> None:
        with self._cond:
            if self._writer != threading.get_ident():
                raise RuntimeError("release_write by a thread that does not hold the lock")
            self._write_depth -= 1
            if self._write_depth == 0:
                self._writer = None
                self._cond.notify_all()

    @contextmanager
    def writing(self) -> Iterator[None]:
        """Hold the write side for the duration of a `with` block."""
        self.acquire_write()
        try:
            yield
        finally:
            self.release_write()


class _WriterHandle:
    """Lets `with lock.writer:` be used wherever a plain lock object is expected."""

    def __init__(self, lock: ReadWriteLock) -> None:
        self._lock = lock

    def __enter__(self) -> None:
        self._lock.acquire_write()

    def __exit__(self, *exc: object) -> None:
        self._lock.release_write()
