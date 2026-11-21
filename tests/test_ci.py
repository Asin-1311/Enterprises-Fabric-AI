"""M17c: what the CI/CD workflows promise (.github/workflows/). Whether they PASS is shown by
the GitHub Actions runs themselves (recorded in docs/NOTES.md, M17c); these tests keep the
definitions from silently losing a check.

Covers: R-14 (CI/CD with scans, gates and staging promotion), R-ops-clone (the fresh-clone
CI job).
"""

import re

import yaml

from app.config import PROJECT_ROOT

WORKFLOWS = PROJECT_ROOT / ".github" / "workflows"


def _load(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def _runs(job: dict) -> str:
    return "\n".join(step.get("run", "") for step in job.get("steps", []))


def test_ci_runs_every_required_check() -> None:
    """Covers: R-14 (CI: lint, unit tests, Vitest and type check, opa test, pip-audit,
    npm audit, Trivy, retrieval regression gate, replay gates, helm lint, integration)."""
    ci = _load("ci.yaml")
    jobs = ci["jobs"]
    triggers = ci[True] if True in ci else ci["on"]  # YAML 1.1 reads the key `on` as True
    assert "push" in triggers and "pull_request" in triggers
    required = {
        "python": ["ruff check .", "python -m pytest", "python -m scripts.check_compliance"],
        "frontend": ["npm ci", "npm run typecheck", "npm test", "npm audit --audit-level=high"],
        "policy": ["test /policy"],
        "dependency-audit": ["pip-audit"],
        "eval-gates": ["eval.run_retrieval_eval --corpus synthetic", "eval.gates retrieval",
                       "eval.gates routing-replay", "eval.gates prompts"],
        "helm": ["helm lint", "helm template"],
        "images": ["--exit-code 1", "--severity HIGH,CRITICAL", "smoke_deploy.py"],
        "integration": ["scripts.ci_env", "--profile full up", "pytest -m integration",
                        "check_compliance --run --run-integration"],
    }
    for job, commands in required.items():
        assert job in jobs, job
        text = _runs(jobs[job])
        for command in commands:
            assert command in text, f"{job}: {command}"
    assert "LLM_MODE" in ci["env"] and ci["env"]["LLM_MODE"] == "mock"  # no paid call, no key
    assert ci["permissions"] == {"contents": "read"}


def test_release_runs_only_after_every_ci_job_and_deploys_to_staging() -> None:
    """Covers: R-14 (images tagged per commit; staging on kind only after CI passes)."""
    ci = _load("ci.yaml")
    release_job = ci["jobs"]["release"]
    others = set(ci["jobs"]) - {"release"}
    assert set(release_job["needs"]) == others
    assert release_job["uses"] == "./.github/workflows/release.yaml"
    condition = release_job["if"]
    assert "github.event_name == 'push'" in condition
    assert "refs/heads/phase2" in condition and "refs/heads/main" in condition

    release = _load("release.yaml")
    publish, staging = release["jobs"]["publish"], release["jobs"]["staging"]
    tags = [s["with"]["tags"] for s in publish["steps"] if "build-push-action" in s.get("uses", "")]
    assert len(tags) == 3 and all("${{ github.sha }}" in t for t in tags)
    assert staging["needs"] == "publish" and staging["environment"] == "staging"
    assert "scripts.kind_smoke --namespace staging --tag" in _runs(staging)


def test_third_party_actions_and_tools_are_pinned() -> None:
    """Covers: R-14 (supply chain: actions by commit SHA, images by digest, tools by checksum)."""
    files = [*WORKFLOWS.glob("*.yaml"), PROJECT_ROOT / ".github" / "actions" / "python-env" / "action.yml"]
    text = "\n".join(f.read_text(encoding="utf-8") for f in files)
    uses = re.findall(r"uses:\s*(\S+)", text)
    external = [u for u in uses if not u.startswith("./")]
    assert external and all(re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", u) for u in external), external
    ci_env = _load("ci.yaml")["env"]
    for image in ("TRIVY_IMAGE", "OPA_IMAGE"):
        assert re.search(r"@sha256:[0-9a-f]{64}$", ci_env[image]), image
    assert "sha256sum -c" in text and re.search(r"HELM_SHA256: [0-9a-f]{64}", text)
    assert "aquasecurity/trivy-action" not in text  # Trivy runs from its pinned image instead


def test_the_fresh_clone_job_indexes_the_synthetic_corpus_without_secrets() -> None:
    """Covers: R-ops-clone (a fresh clone, with no .env and no ICAI material, indexes the
    synthetic corpus and passes the tests)."""
    jobs = _load("ci.yaml")["jobs"]
    gates = _runs(jobs["eval-gates"])
    assert gates.index("app.ingest.indexer") < gates.index("eval.run_retrieval_eval")
    for job in ("python", "eval-gates"):
        text = _runs(jobs[job])
        assert "ci_env" not in text and ".env" not in text  # nothing but the committed files
        assert any("actions/checkout" in s.get("uses", "") for s in jobs[job]["steps"])
    assert "python -m pytest" in _runs(jobs["python"])
