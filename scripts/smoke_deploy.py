"""Smoke test of a running deployment (M17b; CLAUDE.md section 14): kind/Helm, compose, CI.

    python -m scripts.smoke_deploy --base http://api:8000 --email riya.kapoor@example-associates.test
    (the password comes from SMOKE_PASSWORD, never from the command line)

Checks, in order, and stops at the first failure (exit code 1):
1. GET  /api/v1/health answers {"status": "ok"};
2. GET  /metrics answers in Prometheus text format (internal endpoint);
3. POST /api/v1/auth/login signs a synthetic user in (lite profile; for an OIDC
   deployment pass --token instead);
4. POST /api/v1/query streams progress events and a `final` event with status "answered",
   at least one citation and a verified confidence. With LLM_MODE=mock the answer is the
   extractive stand-in (app/agents/mock_llm.py): no key and no paid call.
"""

import argparse
import json
import os
import sys
import time

import httpx

QUESTION = "Within how many days must the final audit file be assembled?"


def events(body: str) -> list[tuple[str, dict]]:
    out, name, data = [], None, []
    for line in body.splitlines():
        if line.startswith("event:"):
            name = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].strip())
        elif not line.strip() and name:
            out.append((name, json.loads("\n".join(data)) if data else {}))
            name, data = None, []
    if name:
        out.append((name, json.loads("\n".join(data)) if data else {}))
    return out


def check(base: str, email: str | None, password: str | None, token: str | None,
          wait_seconds: float = 300, client: httpx.Client | None = None) -> list[str]:
    """Run the checks; returns what passed (raises AssertionError on the first failure).
    `client` replaces the HTTP client (tests pass FastAPI's TestClient)."""
    passed: list[str] = []
    with client or httpx.Client(base_url=base, timeout=120) as c:
        deadline = time.time() + wait_seconds
        while True:  # the API loads its models first
            try:
                health = c.get("/api/v1/health")
                if health.status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if time.time() > deadline:
                raise AssertionError(f"{base}/api/v1/health did not answer within {wait_seconds:.0f} s")
            time.sleep(3)
        assert health.json() == {"status": "ok"}, health.text
        passed.append("health")
        metrics = c.get("/metrics")
        assert metrics.status_code == 200 and "efa_http_requests_total" in metrics.text, metrics.status_code
        passed.append("metrics")
        if token is None:
            login = c.post("/api/v1/auth/login", json={"email": email, "password": password})
            assert login.status_code == 200, f"sign-in failed: {login.status_code} {login.text[:200]}"
            token = login.json()["token"]
            passed.append("sign-in")
        answer = c.post("/api/v1/query", json={"text": QUESTION}, headers={"Authorization": f"Bearer {token}"})
        assert answer.status_code == 200, f"/query: {answer.status_code} {answer.text[:200]}"
        got = events(answer.text)
        kinds = [k for k, _ in got]
        assert "progress" in kinds and kinds[-1] == "final", kinds
        final = got[-1][1]
        assert final["status"] == "answered", final
        assert final["citations"] and final["confidence"] is not None, final
        passed.append(f"query (answered, {len(final['citations'])} citation(s), confidence {final['confidence']})")
    return passed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Smoke test of a running deployment.")
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--email", default="riya.kapoor@example-associates.test")
    parser.add_argument("--token", default=None, help="a bearer token instead of signing in (OIDC)")
    parser.add_argument("--wait", type=float, default=300, help="seconds to wait for /health")
    args = parser.parse_args(argv)
    try:
        passed = check(args.base, args.email, os.environ.get("SMOKE_PASSWORD"), args.token, args.wait)
    except AssertionError as exc:
        print(f"SMOKE TEST FAILED: {exc}")
        return 1
    print("SMOKE TEST PASSED: " + "; ".join(passed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
