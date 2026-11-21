"""M17b: what the container images and the Helm chart promise, checked without Docker or
Kubernetes (the kind install itself: scripts/kind_smoke.py and
tests/integration/test_helm.py).

Covers: R-14 (containers: non-root, health checks, no secrets or licensed material in
images; the smoke test the chart runs).
"""

import re

import yaml
from fastapi.testclient import TestClient

from app.config import PROJECT_ROOT
from scripts.smoke_deploy import check
from tests.test_api import PASSWORD, client  # noqa: F401  (the API fixture)

DOCKER = PROJECT_ROOT / "deploy" / "docker"
CHART = PROJECT_ROOT / "deploy" / "helm" / "enterprise-fabric"


def test_images_run_unprivileged_with_health_checks() -> None:
    app = (DOCKER / "app.Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"^USER 10001$", app, re.M)
    assert app.count("HEALTHCHECK") == 2  # api and worker targets
    assert re.search(r"^FROM base AS api$", app, re.M) and re.search(r"^FROM base AS worker$", app, re.M)
    assert "HF_HUB_OFFLINE=1" in app  # models are baked in; pods never fetch them
    fetch = (DOCKER / "fetch_models.py").read_text(encoding="utf-8")
    assert 'refs / "main"' in fetch and "HF_HUB_OFFLINE" in fetch  # offline lookups by name work
    from app.config import Settings

    defaults = Settings.model_fields
    from deploy.docker.fetch_models import MODELS  # the image holds exactly the configured models

    assert set(MODELS) == {defaults["embedding_model"].default, defaults["reranker_model"].default,
                           defaults["nli_model"].default}
    for line in re.findall(r"^FROM (\S+)", app + (DOCKER / "frontend.Dockerfile").read_text(encoding="utf-8"), re.M):
        assert line == "base" or re.search(r":\d+\.\d+\.\d+", line), f"unpinned base image {line}"
    frontend = (DOCKER / "frontend.Dockerfile").read_text(encoding="utf-8")
    assert "nginx-unprivileged" in frontend and "HEALTHCHECK" in frontend and "npm ci" in frontend


def test_images_never_contain_secrets_or_licensed_material() -> None:
    ignored = (PROJECT_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    for must in (".env", "data/corpus/standards/", "*.pdf", "data/*.db", "data/qdrant/", ".venv/",
                 "data/learning/", "models/", ".git/"):
        assert must in ignored, must
    nginx = (DOCKER / "nginx.conf.template").read_text(encoding="utf-8")
    assert "location /api/" in nginx and "/metrics" not in nginx.replace("/metrics is NOT forwarded", "")
    assert "proxy_buffering off" in nginx  # Server-Sent Events stream through


def test_chart_defaults_are_a_safe_demo() -> None:
    values = yaml.safe_load((CHART / "values.yaml").read_text(encoding="utf-8"))
    assert values["profile"] == "lite" and values["llm"]["mode"] == "mock"
    assert values["secrets"]["values"] == {} and values["secrets"]["existingSecret"] == ""
    assert values["hpa"]["enabled"] is False and values["api"]["replicas"] == 1
    helpers = (CHART / "templates" / "_helpers.tpl").read_text(encoding="utf-8")
    for setting in ("runAsNonRoot: true", "allowPrivilegeEscalation: false", "readOnlyRootFilesystem: true",
                    'drop: ["ALL"]'):
        assert setting in helpers, setting
    api = (CHART / "templates" / "api.yaml").read_text(encoding="utf-8")
    for probe in ("startupProbe", "readinessProbe", "livenessProbe", "HorizontalPodAutoscaler"):
        assert probe in api, probe


def test_the_smoke_test_passes_against_a_running_api(client) -> None:  # noqa: F811
    """Covers: R-14 (what `helm test` runs: health, metrics, sign-in, one answered question)."""
    passed = check("http://testserver", "s1@test.example", PASSWORD, None, wait_seconds=5, client=client)
    assert passed[:3] == ["health", "metrics", "sign-in"] and passed[3].startswith("query (answered")


def test_the_smoke_test_fails_loudly_on_a_bad_password(client) -> None:  # noqa: F811
    import pytest

    with pytest.raises(AssertionError, match="sign-in failed"):
        check("http://testserver", "s1@test.example", "wrong-password", None, wait_seconds=5,
              client=TestClient(client.app))
