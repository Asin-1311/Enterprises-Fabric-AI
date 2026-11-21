"""M17b: the Helm chart with the real `helm` tool: lint, render both profiles, refuse the
unsafe combinations, and `helm test` on the kind release that scripts/kind_smoke.py installs
(run in CI; its result on the laptop is recorded in docs/NOTES.md).

Covers: R-14 (Helm chart).
"""

import shutil
import subprocess

import pytest
import yaml

from app.config import PROJECT_ROOT

pytestmark = pytest.mark.integration
CHART = str(PROJECT_ROOT / "deploy" / "helm" / "enterprise-fabric")
FULL = ["--set", "profile=full", "--set", "external.qdrantUrl=http://qdrant:6333",
        "--set", "external.opensearchUrl=http://opensearch:9200", "--set", "external.redisUrl=redis://redis:6379",
        "--set", "external.kafkaBootstrap=kafka:9092", "--set", "secrets.existingSecret=ef-secrets"]


def helm(*args: str) -> subprocess.CompletedProcess:
    if shutil.which("helm") is None:
        pytest.skip("helm is not installed (winget install Helm.Helm)")
    return subprocess.run(["helm", *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)


def rendered(*args: str) -> list[dict]:
    result = helm("template", "ef", CHART, *args)
    assert result.returncode == 0, result.stderr
    return [d for d in yaml.safe_load_all(result.stdout) if d]


def test_the_chart_lints_and_renders_both_profiles() -> None:
    """Covers: R-14 (Helm): lint passes; lite renders the API (with its indexing init
    container), the UI and the smoke-test hook; full adds the worker and the API autoscaler."""
    lint = helm("lint", CHART)
    assert lint.returncode == 0 and "0 chart(s) failed" in lint.stdout, lint.stdout + lint.stderr

    lite = rendered("--set", "secrets.values.DEMO_PASSWORD=x")
    kinds = sorted(f"{d['kind']}/{d['metadata']['name']}" for d in lite)
    assert kinds == ["ConfigMap/ef-config", "Deployment/ef-api", "Deployment/ef-frontend", "Pod/ef-smoke-test",
                     "Secret/ef-secrets", "Service/ef-api", "Service/ef-frontend"]
    api = next(d for d in lite if d["kind"] == "Deployment" and d["metadata"]["name"] == "ef-api")
    pod = api["spec"]["template"]["spec"]
    assert pod["initContainers"][0]["command"][:3] == ["python", "-m", "app.ingest.indexer"]
    container = pod["containers"][0]
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    assert {"startupProbe", "readinessProbe", "livenessProbe"} <= set(container)
    config = next(d for d in lite if d["kind"] == "ConfigMap")["data"]
    assert (config["PROFILE"], config["LLM_MODE"]) == ("lite", "mock")

    full = rendered(*FULL, "--set", "worker.enabled=true", "--set", "hpa.enabled=true")
    names = {f"{d['kind']}/{d['metadata']['name']}" for d in full}
    assert {"Deployment/ef-worker", "HorizontalPodAutoscaler/ef-api"} <= names
    assert "Secret/ef-secrets" not in names  # an existing Secret is used, none is created
    full_config = next(d for d in full if d["kind"] == "ConfigMap")["data"]
    assert full_config["API_RUN_WORKER"] == "false" and full_config["QDRANT_URL"] == "http://qdrant:6333"


def test_the_chart_refuses_unsafe_combinations() -> None:
    lite_hpa = helm("template", "ef", CHART, "--set", "hpa.enabled=true")
    assert lite_hpa.returncode != 0 and "use one API replica" in lite_hpa.stderr
    full_without_servers = helm("template", "ef", CHART, "--set", "profile=full")
    assert full_without_servers.returncode != 0 and "needs external.qdrantUrl" in full_without_servers.stderr
    lite_worker = helm("template", "ef", CHART, "--set", "worker.enabled=true")
    assert lite_worker.returncode != 0 and "profile full" in lite_worker.stderr


def test_the_release_on_kind_passes_its_smoke_test() -> None:
    """Covers: R-14 (Helm install on kind passes the smoke test): `helm test` on the release
    that scripts/kind_smoke.py installed (health, metrics, sign-in, one answered question,
    run inside the cluster). Skipped when that cluster or release is absent."""
    context = "kind-enterprise-fabric"
    status = helm("status", "ef", "--kube-context", context, "--namespace", "default")
    if status.returncode != 0:
        pytest.skip("no 'ef' release on the kind cluster (python -m scripts.kind_smoke)")
    result = subprocess.run(["helm", "test", "ef", "--kube-context", context, "--namespace", "default",
                             "--logs", "--timeout", "10m"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=700)
    assert result.returncode == 0 and "SMOKE TEST PASSED" in result.stdout, result.stdout + result.stderr
