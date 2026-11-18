"""Shared pytest fixtures."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.db.session import init_db, make_engine, make_session_factory


@pytest.fixture
def env_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provide dummy settings via environment variables (no real .env needed)."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("LLM_BASE_URL", "https://openrouter.ai/api")
    monkeypatch.setenv("LLM_AUTH_TOKEN", "test-token")
    monkeypatch.setenv("MODEL_LIGHT", "model-light")
    monkeypatch.setenv("MODEL_SYNTH", "model-synth")
    monkeypatch.setenv("MODEL_VERIFY", "model-verify")


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    """A fresh SQLite database in a temporary folder, with all tables created."""
    eng = make_engine(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    init_db(eng)
    return eng


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    """A database session on the temporary database."""
    factory = make_session_factory(engine)
    with factory() as s:
        yield s
