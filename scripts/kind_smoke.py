"""Install the Helm chart on a local kind cluster and run its smoke test (M17b; CLAUDE.md
section 14: "Verified on a local kind cluster").

    .venv\\Scripts\\python.exe -m scripts.kind_smoke              # build nothing, use :dev images
    .venv\\Scripts\\python.exe -m scripts.kind_smoke --delete     # remove the cluster afterwards
    .venv\\Scripts\\python.exe -m scripts.kind_smoke --namespace staging --tag <sha>   # CI staging

Steps: create the kind cluster (if missing) -> load the api, worker and frontend images
into it -> `helm lint` -> `helm upgrade --install` (lite profile, LLM_MODE=mock, a random
demo password generated for this run and never printed) -> wait for the pods -> `helm
test` (scripts/smoke_deploy.py inside the cluster: health, metrics, sign-in, one
question). Needs docker, kind, helm and kubectl on PATH; no API key, no paid call.
On the 3.5 GB Docker VM, stop the compose stacks first.
"""

import argparse
import secrets
import shutil
import subprocess
import sys

from app.config import PROJECT_ROOT

CHART = PROJECT_ROOT / "deploy" / "helm" / "enterprise-fabric"
CLUSTER = "enterprise-fabric"
IMAGES = ("api", "worker", "frontend")


def run(cmd: list[str], check: bool = True, quiet: bool = False) -> subprocess.CompletedProcess:
    if not quiet:
        print("+", " ".join(cmd), flush=True)
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if not quiet and result.stdout.strip():
        print(result.stdout.strip()[-3000:], flush=True)
    if check and result.returncode != 0:
        raise SystemExit(f"FAILED ({result.returncode}): {' '.join(cmd)}\n{result.stderr.strip()[-3000:]}")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Helm install on kind + smoke test.")
    parser.add_argument("--tag", default="dev", help="image tag to load and deploy")
    parser.add_argument("--namespace", default="default")
    parser.add_argument("--release", default="ef")
    parser.add_argument("--delete", action="store_true", help="delete the kind cluster at the end")
    args = parser.parse_args(argv)
    for tool in ("docker", "kind", "helm", "kubectl"):
        if shutil.which(tool) is None:
            raise SystemExit(f"{tool} is not on PATH")

    clusters = run(["kind", "get", "clusters"], quiet=True).stdout.split()
    if CLUSTER not in clusters:
        run(["kind", "create", "cluster", "--name", CLUSTER, "--wait", "120s"])
    context = f"kind-{CLUSTER}"
    for name in IMAGES:
        run(["kind", "load", "docker-image", f"enterprise-fabric/{name}:{args.tag}", "--name", CLUSTER])
    run(["helm", "lint", str(CHART)])
    password = secrets.token_urlsafe(18)  # this run's demo password; never printed
    install = ["helm", "upgrade", "--install", args.release, str(CHART), "--kube-context", context,
               "--namespace", args.namespace, "--create-namespace", "--wait", "--timeout", "20m",
               "--set", f"images.api.tag={args.tag}", "--set", f"images.worker.tag={args.tag}",
               "--set", f"images.frontend.tag={args.tag}", "--set-string",
               f"secrets.values.DEMO_PASSWORD={password}"]
    print("+ helm upgrade --install ... (demo password hidden)", flush=True)
    result = subprocess.run(install, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        run(["kubectl", "--context", context, "-n", args.namespace, "get", "pods", "-o", "wide"], check=False)
        run(["kubectl", "--context", context, "-n", args.namespace, "logs",
             f"deploy/{args.release}-api", "--all-containers", "--tail", "80"], check=False)
        raise SystemExit(f"helm install failed:\n{result.stderr[-3000:]}")
    run(["kubectl", "--context", context, "-n", args.namespace, "get", "pods"])
    test = run(["helm", "test", args.release, "--kube-context", context, "--namespace", args.namespace,
                "--logs", "--timeout", "10m"], check=False)
    passed = test.returncode == 0 and "SMOKE TEST PASSED" in test.stdout
    print("\nKIND SMOKE TEST", "PASSED" if passed else "FAILED", flush=True)
    if args.delete:
        run(["kind", "delete", "cluster", "--name", CLUSTER], check=False)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
