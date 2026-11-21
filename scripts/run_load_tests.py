"""Run the k6 and Locust load tests at 1, 5 and 10 concurrent users (M18c; CLAUDE.md
section 13.3) and write eval/results/<timestamp>_load/.

    python -m scripts.run_load_tests --confirm-paid            # live model calls (paid)
    python -m scripts.run_load_tests --confirm-paid --per-user 2 --levels 1,5,10

- It starts its own API (uvicorn on 127.0.0.1:8010) from the laptop's lite index. The
  response cache is off, so every request is answered, not served from a cache. The
  sign-in rate limit is raised, so 10 users can sign in at once. The background
  connectors and the folder watcher are off. Everything else, including the latency
  window and degradation, runs as configured: that is the system under load.
- Each level is a closed model. Every simulated user asks PER_USER questions, one after
  the other, and the level ends when all are answered. Locust runs first, then k6, never
  together.
- Measured on the client:
  - Locust: time to the first `draft_token` event (TTFT) and to the `final` event;
  - k6: the full-answer time only. k6 receives a Server-Sent Events response only once
    it is complete, so it cannot see the first token.
- Targets (CLAUDE.md 13.3): p95 TTFT < 1.5 s, p95 full answer < 8 s. They are reported as
  met or not met with the measured values; how latency is measured is never adjusted.
"""

import argparse
import csv
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

from app.config import PROJECT_ROOT, get_settings
from eval.run_answer_eval import TARGETS, percentile

RESULTS = PROJECT_ROOT / "eval" / "results"
PORT = 8010
BASE = f"http://127.0.0.1:{PORT}"
API_ENV = {"RESPONSE_CACHE_ENABLED": "false", "AUTH_RATE_PER_MINUTE": "1000",
           "API_RUN_CONNECTORS": "false", "API_WATCH_CORPUS": "false"}


def questions() -> list[str]:
    """Questions any staff user may ask and have answered: the staff questions of the sample."""
    from eval.question_sets import evaluation_sample, load_questions

    picked = [q["question"] for q in evaluation_sample(load_questions())
              if q["user_id"].startswith("S") and q["gold_doc_ids"] and q.get("category") != "engagement"]
    return picked or [q["question"] for q in load_questions() if q["user_id"] == "S01" and q["gold_doc_ids"]][:6]


def hardware() -> dict:
    import psutil

    return {"machine": platform.machine(), "processor": platform.processor(), "os": platform.platform(),
            "logical_cpus": psutil.cpu_count(), "physical_cpus": psutil.cpu_count(logical=False),
            "ram_gb": round(psutil.virtual_memory().total / 2**30, 1), "python": platform.python_version()}


def start_api(log: Path) -> subprocess.Popen:
    env = {**os.environ, **API_ENV}
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.api.main:app", "--host", "127.0.0.1",
                             "--port", str(PORT)], cwd=PROJECT_ROOT, env=env,
                            stdout=log.open("w", encoding="utf-8"), stderr=subprocess.STDOUT)
    deadline = time.time() + 300
    while time.time() < deadline:
        try:
            if httpx.get(f"{BASE}/api/v1/health", timeout=3).status_code == 200:
                return proc
        except httpx.HTTPError:
            pass
        if proc.poll() is not None:
            raise SystemExit(f"the API exited; see {log}")
        time.sleep(3)
    proc.terminate()
    raise SystemExit("the API did not answer /health within 300 s")


def k6_path() -> str | None:
    """k6 on PATH, or at the winget/MSI default location (an install may finish mid-run)."""
    default = Path(r"C:\Program Files\k6\k6.exe")
    return shutil.which("k6") or (str(default) if default.exists() else None)


def run_k6(users: int, per_user: int, qs: list[str], work: Path, password: str) -> list[dict]:
    points = work / f"k6_{users}.json"
    env = {**os.environ, "LOADTEST_PASSWORD": password}
    cmd = [k6_path(), "run", "--quiet", "--out", f"json={points}",
           "-e", f"BASE={BASE}", "-e", f"VUS={users}", "-e", f"PER_VU={per_user}",
           "-e", f"OUT={work / f'k6_{users}_summary.json'}", "-e", f"QUESTIONS={json.dumps(qs)}",
           str(PROJECT_ROOT / "loadtest" / "k6" / "query.js")]
    subprocess.run(cmd, cwd=PROJECT_ROOT, env=env, check=False, capture_output=True, timeout=1800)
    rows, checks = [], {"HTTP 200": [0, 0], "final event": [0, 0]}
    if points.exists():
        for line in points.read_text(encoding="utf-8").splitlines():
            p = json.loads(line)
            if p.get("type") != "Point":
                continue
            if p["metric"] == "full_answer_s":
                rows.append({"tool": "k6", "users": users, "ttft_s": None, "full_s": p["data"]["value"],
                             "status": "", "degraded": "", "error": ""})
            elif p["metric"] == "checks" and p["data"]["tags"].get("check") in checks:
                c = checks[p["data"]["tags"]["check"]]
                c[0] += 1
                c[1] += int(p["data"]["value"] == 0)
    failed = max(checks["final event"][1], checks["HTTP 200"][1])
    rows += [{"tool": "k6", "users": users, "ttft_s": None, "full_s": None, "status": "", "degraded": "",
              "error": "no final event or HTTP error"} for _ in range(failed)]
    return rows


def run_locust(users: int, per_user: int, qs: list[str], work: Path, password: str) -> list[dict]:
    out = work / f"locust_{users}.jsonl"
    env = {**os.environ, "LOADTEST_PASSWORD": password, "LOADTEST_OUT": str(out),
           "LOADTEST_QUESTIONS": str(per_user), "LOADTEST_QUESTION_LIST": json.dumps(qs)}
    proc = subprocess.Popen([sys.executable, "-m", "locust", "-f", str(PROJECT_ROOT / "loadtest" / "locust" / "locustfile.py"),
                             "--headless", "-u", str(users), "-r", str(users), "--host", BASE,
                             "--run-time", "20m", "--only-summary"],
                            cwd=PROJECT_ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline, expected = time.time() + 1500, users * per_user
    while time.time() < deadline and proc.poll() is None:
        if out.exists() and len(out.read_text(encoding="utf-8").splitlines()) >= expected:
            break
        time.sleep(2)
    proc.terminate()
    proc.wait(timeout=60)
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()] if out.exists() else []
    return [{"tool": "locust", "users": users, "ttft_s": r["ttft_s"], "full_s": r["full_s"],
             "status": r["status"], "degraded": r["degraded"], "error": r["error"]} for r in rows]


def summarise(rows: list[dict]) -> dict:
    ok = [r for r in rows if not r["error"] and r["full_s"] is not None]
    ttft = [r["ttft_s"] for r in ok if r["ttft_s"] is not None]
    full = [r["full_s"] for r in ok]
    out = {"requests": len(rows), "errors": len(rows) - len(ok),
           "ttft_measured": len(ttft), "ttft_s_p50": percentile(ttft, 0.5), "ttft_s_p95": percentile(ttft, 0.95),
           "full_answer_s_p50": percentile(full, 0.5), "full_answer_s_p95": percentile(full, 0.95),
           "statuses": {s: sum(r["status"] == s for r in ok) for s in sorted({r["status"] for r in ok}) if s},
           "degraded": sum(1 for r in ok if r["degraded"] is True)}
    out["ttft_target_met"] = None if out["ttft_s_p95"] is None else out["ttft_s_p95"] < TARGETS["ttft_p95_s"]
    out["full_answer_target_met"] = (None if out["full_answer_s_p95"] is None
                                     else out["full_answer_s_p95"] < TARGETS["full_answer_p95_s"])
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="k6 and Locust load tests (paid model calls).")
    parser.add_argument("--confirm-paid", action="store_true")
    parser.add_argument("--levels", default="1,5,10")
    parser.add_argument("--per-user", type=int, default=2)
    args = parser.parse_args(argv)
    levels = [int(x) for x in args.levels.split(",")]
    if not args.confirm_paid:
        print(f"Would ask {sum(levels) * args.per_user * 2} questions with the live model; add --confirm-paid.")
        return 0
    password = get_settings().demo_password
    if not password:
        raise SystemExit("DEMO_PASSWORD is not set in .env")
    qs = questions()
    out = RESULTS / f"{datetime.now():%Y%m%d-%H%M%S}_load"
    work = out / "raw"
    work.mkdir(parents=True)
    api = start_api(work / "api.log")
    rows: list[dict] = []
    started = time.time()
    try:
        for tool, runner in (("locust", run_locust), ("k6", run_k6)):
            if tool == "k6" and k6_path() is None:
                print("k6 is not installed (winget install GrafanaLabs.k6): k6 levels not run", flush=True)
                continue
            for users in levels:
                t0 = time.time()
                got = runner(users, args.per_user, qs, work, password)
                rows += got
                print(f"{tool} {users} users: {len(got)} requests in {time.time() - t0:.0f} s", flush=True)
    finally:
        api.terminate()
        api.wait(timeout=60)
    results = {tool: {str(u): summarise([r for r in rows if r["tool"] == tool and r["users"] == u])
                      for u in levels} for tool in ("k6", "locust")}
    metrics = {"targets": TARGETS, "levels": levels, "per_user": args.per_user, "results": results,
               "minutes": round((time.time() - started) / 60, 1), "hardware": hardware(),
               "measured": "client side; TTFT by Locust only (k6 sees an SSE response only when complete)"}
    (out / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    with (out / "per_question.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["tool", "users", "ttft_s", "full_s", "status", "degraded", "error"])
        w.writeheader()
        w.writerows(rows)
    s = get_settings()
    (out / "config.json").write_text(json.dumps({"api_env": API_ENV, "port": PORT, "profile": s.profile,
                                                 "questions": qs, "latency_target_seconds": s.latency_target_seconds,
                                                 "degradation_enabled": s.degradation_enabled}, indent=2),
                                     encoding="utf-8")
    (out / "model_ids.json").write_text(json.dumps({"light": s.model_light, "synth": s.model_synth,
                                                    "verify": s.model_verify}, indent=2), encoding="utf-8")
    for p in work.glob("k6_*.json"):  # the raw k6 points are large; the summaries and rows stay
        if not p.name.endswith("_summary.json"):
            p.unlink()
    print(f"Results: {out.relative_to(PROJECT_ROOT)}")
    for tool, by in results.items():
        for u, r in by.items():
            print(f"  {tool:<6} {u:>2} users: n={r['requests']} err={r['errors']} TTFT p95={r['ttft_s_p95']} "
                  f"full p95={r['full_answer_s_p95']} degraded={r['degraded']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
