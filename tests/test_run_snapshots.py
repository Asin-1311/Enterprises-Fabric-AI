"""M20 T1: run snapshots are committed to a public repository and must hold no secrets.

Covers: R-ops-secrets.
"""

import ast
import json
from pathlib import Path

import pytest

from app.config import PROJECT_ROOT, Settings
from app.security.redaction import REDACTED, redact, snapshot_json

PASSWORD = "known-demo-password-4711"
DB_PASSWORD = "known-db-password-0815"


def _settings() -> Settings:
    return Settings(_env_file=None, llm_mode="mock", model_light="l", model_synth="s", model_verify="v",
                    demo_password=PASSWORD, llm_auth_token="known-token-123",
                    anthropic_api_key="known-api-key-456",
                    database_url=f"postgresql+psycopg://fabric:{DB_PASSWORD}@127.0.0.1:5432/fabric")


def test_a_snapshot_holds_neither_the_password_nor_the_url_credentials() -> None:
    """Covers: R-ops-secrets (a snapshot built from settings with a known password and a
    URL with credentials contains neither)."""
    text = snapshot_json(_settings().model_dump())
    for secret in (PASSWORD, DB_PASSWORD, "known-token-123", "known-api-key-456", "fabric:"):
        assert secret not in text
    config = json.loads(text)
    assert config["demo_password"] == config["llm_auth_token"] == config["anthropic_api_key"] == REDACTED
    assert config["database_url"] == "postgresql+psycopg://127.0.0.1:5432/fabric"


def test_settings_that_are_not_secrets_are_kept() -> None:
    settings = _settings()
    config = json.loads(snapshot_json(settings.model_dump()))
    for name in ("precise_keyword_weight", "budget_max_tokens", "chunk_max_tokens",
                 "langfuse_secret_key_ref", "langfuse_public_key_ref"):
        assert config[name] == settings.model_dump()[name], name
    assert redact({"demo_password": None}) == {"demo_password": None}  # unset is not a secret


@pytest.mark.parametrize("password", ["p@ss", "p/ss", "p?ss", "p#ss", "p ss", "p:ss"])
def test_credentials_are_removed_whatever_the_password_contains(password: str) -> None:
    for config in ({"redis_url": f"redis://user:{password}@host:6379"},
                   {"nested": [{"endpoint": f"https://user:{password}@host/path"}]},
                   {"redis_url": f"redis://:{password}@host:6379"}):
        assert password not in snapshot_json(config)
    assert "p-in-query" not in snapshot_json({"url": "postgresql://host/db?password=p-in-query"})


def _config_writers() -> list[tuple[Path, str]]:
    """Every `(<dir> / "config.json").write_text(<argument>)` in the code."""
    found = []
    for folder in ("app", "eval", "scripts"):
        for path in (PROJECT_ROOT / folder).rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "write_text" and "'config.json'" in ast.unparse(node.func.value)):
                    found.append((path, ast.unparse(node.args[0])))
    return found


def test_every_run_writer_uses_the_redaction_helper() -> None:
    """Covers: R-ops-secrets (no writer can bypass the helper)."""
    writers = _config_writers()
    assert len(writers) >= 9
    for path, argument in writers:
        assert argument.startswith("snapshot_json("), path.name


# Committed before the fix. Their two passwords are retired by changing them, not by editing
# the files (eval/results/ is never edited after a run).
EXPOSED_BEFORE_THE_FIX = {
    "20261119-145815_retrieval", "20261119-151731_retrieval", "20261119-222706_retrieval",
    "20261120-150532_retrieval", "20261120-150853_retrieval", "20261121-144722_retrieval",
    "20261121-151459_answers", "20261121-153910_retrieval", "20261121-171000_answers",
    "20261121-181723_answers", "20261121-183054_answers",
}


def test_no_other_run_snapshot_holds_a_secret() -> None:
    """Covers: R-ops-secrets (a run folder added from now on cannot bring a secret in)."""
    for path in sorted((PROJECT_ROOT / "eval" / "results").glob("*/config.json")):
        if path.parent.name not in EXPOSED_BEFORE_THE_FIX:
            config = json.loads(path.read_text(encoding="utf-8"))
            assert redact(config) == config, path.parent.name


def test_the_old_snapshots_expose_only_the_two_retired_passwords() -> None:
    """Covers: R-ops-secrets (changing the demo and PostgreSQL passwords retired everything
    the runs committed before the fix exposed)."""
    for run in sorted(EXPOSED_BEFORE_THE_FIX):
        config = json.loads((PROJECT_ROOT / "eval" / "results" / run / "config.json").read_text(encoding="utf-8"))
        clean = redact(config)
        assert {k for k in config if clean[k] != config[k]} <= {"demo_password", "database_url"}, run
