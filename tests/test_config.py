"""Tests for app/config.py and app/llm_client.py (no network calls)."""

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.llm_client import make_client


def test_settings_load_from_env(env_settings: None) -> None:
    s = Settings(_env_file=None)
    assert s.model_light == "model-light"
    assert s.effort_light == "low"
    assert s.rrf_k == 60
    assert s.rerank_top_n == 5
    assert s.confidence_threshold == 0.6
    assert s.chunk_max_tokens == 400 and s.chunk_overlap_tokens == 50


def test_no_temperature_setting_exists(env_settings: None) -> None:
    """Sampling parameters were removed by the API for the current models."""
    assert not any("temperature" in name for name in Settings.model_fields)


def test_verify_model_must_differ_from_synth(
    env_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MODEL_VERIFY", "model-synth")
    with pytest.raises(ValidationError, match="MODEL_VERIFY must differ"):
        Settings(_env_file=None)


def test_a_credential_is_required(env_settings: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ValidationError, match="LLM_AUTH_TOKEN"):
        Settings(_env_file=None)


def test_invalid_effort_rejected(env_settings: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EFFORT_SYNTH", "extreme")
    with pytest.raises(ValidationError, match="EFFORT_SYNTH"):
        Settings(_env_file=None)


def test_model_ids_have_no_hardcoded_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_AUTH_TOKEN", "test-token")
    for var in ("MODEL_LIGHT", "MODEL_SYNTH", "MODEL_VERIFY"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_client_uses_openrouter_gateway(env_settings: None) -> None:
    client = make_client(Settings(_env_file=None))
    assert str(client.base_url).startswith("https://openrouter.ai/api")
    assert client.auth_token == "test-token"


def test_client_direct_anthropic_api(env_settings: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_AUTH_TOKEN")
    monkeypatch.delenv("LLM_BASE_URL")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    client = make_client(Settings(_env_file=None))
    assert client.api_key == "sk-ant-test"
