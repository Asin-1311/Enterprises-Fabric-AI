"""Write a throwaway .env for CI (M17c; .github/workflows/ci.yaml).

    python -m scripts.ci_env            # writes .env; refuses if one already exists

Every password is random for this run only and never printed in clear: on GitHub Actions
each one is registered with `::add-mask::`, so the logs show *** instead. The model ids
are placeholders because CI runs with LLM_MODE=mock (app/agents/mock_llm.py): no API key
exists in CI and no paid call is made. The service addresses are the ports published by
deploy/compose.yaml.

It never overwrites an existing .env, so it cannot destroy a developer's real settings.
"""

import os
import secrets
import sys
from pathlib import Path

from app.config import PROJECT_ROOT

ENV = PROJECT_ROOT / ".env"


def ci_values() -> dict[str, str]:
    pg_password = secrets.token_urlsafe(24)
    return {
        "LLM_MODE": "mock",
        "MODEL_LIGHT": "ci-mock-light",
        "MODEL_SYNTH": "ci-mock-synth",
        "MODEL_VERIFY": "ci-mock-verify",
        "MODEL_JUDGE": "ci-mock-judge",
        "DEMO_PASSWORD": secrets.token_urlsafe(18),
        "POSTGRES_USER": "fabric",
        "POSTGRES_PASSWORD": pg_password,
        "POSTGRES_DB": "fabric",
        "DATABASE_URL": f"postgresql+psycopg://fabric:{pg_password}@127.0.0.1:5432/fabric",
        "QDRANT_URL": "http://127.0.0.1:6333",
        "OPENSEARCH_URL": "http://127.0.0.1:9200",
        "REDIS_URL": "redis://127.0.0.1:6379",
        "KAFKA_BOOTSTRAP": "127.0.0.1:9092",
        "MINIO_ROOT_USER": "fabric-ci",
        "MINIO_ROOT_PASSWORD": secrets.token_urlsafe(24),
        "KEYCLOAK_ADMIN": "admin",
        "KEYCLOAK_ADMIN_PASSWORD": secrets.token_urlsafe(24),
        "KEYCLOAK_DEMO_PASSWORD": secrets.token_urlsafe(18),
    }


SECRET_KEYS = ("DEMO_PASSWORD", "POSTGRES_PASSWORD", "DATABASE_URL", "MINIO_ROOT_PASSWORD",
               "KEYCLOAK_ADMIN_PASSWORD", "KEYCLOAK_DEMO_PASSWORD")


def write(path: Path = ENV) -> dict[str, str]:
    if path.exists():
        raise SystemExit(f"{path} exists; scripts.ci_env never overwrites it.")
    values = ci_values()
    if os.environ.get("GITHUB_ACTIONS") == "true":
        for key in SECRET_KEYS:
            print(f"::add-mask::{values[key]}")
    path.write_text("".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")
    print(f"wrote {path.name} with {len(values)} settings (passwords random for this run)")
    return values


if __name__ == "__main__":
    write()
    sys.exit(0)
