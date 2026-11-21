"""M17c: the throwaway CI .env (scripts/ci_env.py).

Covers: R-14 (CI runs without stored secrets: random per-run passwords, masked in logs,
and a developer's real .env is never overwritten).
"""

from pathlib import Path

import pytest

from scripts import ci_env


def test_ci_env_writes_random_masked_secrets_and_never_overwrites(tmp_path: Path, monkeypatch,
                                                                  capsys) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    target = tmp_path / ".env"
    values = ci_env.write(target)
    printed = capsys.readouterr().out
    masks = {line for line in printed.splitlines() if line.startswith("::add-mask::")}
    rest = "\n".join(line for line in printed.splitlines() if line not in masks)
    for key in ci_env.SECRET_KEYS:
        assert f"::add-mask::{values[key]}" in masks  # GitHub shows *** instead
        assert values[key] not in rest  # nothing else printed carries a secret
    assert values["LLM_MODE"] == "mock" and "LLM_AUTH_TOKEN" not in values
    assert values["POSTGRES_PASSWORD"] in values["DATABASE_URL"]
    assert ci_env.ci_values()["POSTGRES_PASSWORD"] != values["POSTGRES_PASSWORD"]  # random per run

    target.write_text("REAL=keep\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="never overwrites"):
        ci_env.write(target)
    assert target.read_text(encoding="utf-8") == "REAL=keep\n"
