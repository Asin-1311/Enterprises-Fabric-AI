"""The readers-writer lock that replaced Phase 1's global question lock.

Covers: R-5-par (the /query part; parallel sub-query search comes with M14).
"""

import threading
import time

import pytest

from app.concurrency import ReadWriteLock


def _run(*targets) -> None:
    threads = [threading.Thread(target=t) for t in targets]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
        assert not t.is_alive(), "a thread is stuck"


def test_readers_run_at_the_same_time() -> None:
    """Covers: R-5-par. Two questions (readers) are inside the lock together."""
    lock, both_inside = ReadWriteLock(), threading.Barrier(2, timeout=5)

    def reader() -> None:
        with lock.reading():
            both_inside.wait()  # raises BrokenBarrierError if the other cannot get in

    _run(reader, reader)


def test_a_writer_waits_for_running_readers_and_then_runs_alone() -> None:
    lock, events = ReadWriteLock(), []
    reader_in = threading.Event()

    def reader() -> None:
        with lock.reading():
            reader_in.set()
            time.sleep(0.2)
            events.append("reader done")

    def writer() -> None:
        reader_in.wait(5)
        with lock.writing():
            events.append("writer in")

    _run(reader, writer)
    assert events == ["reader done", "writer in"]


def test_new_readers_wait_while_a_writer_is_waiting() -> None:
    """Writer preference: a stream of questions cannot postpone a re-index for ever."""
    lock, events = ReadWriteLock(), []
    first_in, writer_waiting = threading.Event(), threading.Event()

    def first_reader() -> None:
        with lock.reading():
            first_in.set()
            writer_waiting.wait(5)
            time.sleep(0.2)
            events.append("first reader done")

    def writer() -> None:
        first_in.wait(5)
        writer_waiting.set()
        with lock.writing():
            events.append("writer")

    def late_reader() -> None:
        writer_waiting.wait(5)
        time.sleep(0.05)  # arrive after the writer started waiting
        with lock.reading():
            events.append("late reader")

    _run(first_reader, writer, late_reader)
    assert events == ["first reader done", "writer", "late reader"]


def test_the_writer_is_reentrant_and_may_read() -> None:
    lock = ReadWriteLock()
    with lock.writer:
        with lock.writer:  # a sync calls other locked indexer methods
            with lock.reading():
                pass
    with lock.reading():  # fully released afterwards
        pass


def test_release_by_another_thread_is_an_error() -> None:
    lock = ReadWriteLock()
    lock.acquire_write()
    errors = []

    def other() -> None:
        try:
            lock.release_write()
        except RuntimeError as exc:
            errors.append(exc)

    _run(other)
    lock.release_write()
    assert errors and pytest is not None
