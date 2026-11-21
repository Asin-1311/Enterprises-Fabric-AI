"""Locust load test of POST /api/v1/query (M18c; CLAUDE.md section 13.3).

Run by scripts/run_load_tests.py, which starts it headless at 1, 5 and 10 users:
    locust -f loadtest/locust/locustfile.py --headless -u 5 -r 5 --host http://127.0.0.1:8000

Each simulated user signs in as one of the synthetic demo users (round robin; the
password comes from LOADTEST_PASSWORD, never from this file) and asks LOADTEST_QUESTIONS
questions, one after the other (a closed model: a user asks again only after the answer).
The answer is read as it streams, so two times are measured on the client:
- TTFT: until the first `draft_token` event (the first token of the draft);
- full answer: until the `final` event (the verified answer).
Every request is appended as one JSON line to LOADTEST_OUT for the report.
"""

import json
import os
import time
from itertools import count

from locust import HttpUser, between, events, task

USERS = ["riya.kapoor", "kabir.nair", "meera.iyer", "arjun.desai", "neha.kulkarni",
         "vikram.sethi", "anjali.bose", "kiran.rao"]
QUESTIONS = json.loads(os.environ.get("LOADTEST_QUESTION_LIST", "[]")) or [
    "What are the sufficiency and appropriateness requirements for audit evidence under SA 500?"]
PER_USER = int(os.environ.get("LOADTEST_QUESTIONS", "2"))
OUT = os.environ.get("LOADTEST_OUT", "locust_requests.jsonl")
_next_user = count()


def _record(row: dict) -> None:
    with open(OUT, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


class QueryUser(HttpUser):
    wait_time = between(0.5, 1.5)

    def on_start(self) -> None:
        self.n = next(_next_user)
        email = f"{USERS[self.n % len(USERS)]}@example-associates.test"
        r = self.client.post("/api/v1/auth/login", json={"email": email,
                                                         "password": os.environ["LOADTEST_PASSWORD"]})
        r.raise_for_status()
        self.headers = {"Authorization": f"Bearer {r.json()['token']}"}
        self.asked = 0

    @task
    def ask(self) -> None:
        if self.asked >= PER_USER:
            self.stop(force=False)
            return
        question = QUESTIONS[(self.n * PER_USER + self.asked) % len(QUESTIONS)]
        self.asked += 1
        start = time.perf_counter()
        ttft = final = None
        status, error, event, degraded = "", "", None, False
        try:
            with self.client.post("/api/v1/query", json={"text": question}, headers=self.headers,
                                  stream=True, catch_response=True, timeout=600, name="/api/v1/query") as r:
                if r.status_code != 200:
                    error = f"HTTP {r.status_code}"
                    r.failure(error)
                else:
                    for raw in r.iter_lines(decode_unicode=True):
                        if raw.startswith("event:"):
                            event = raw[6:].strip()
                            if event == "draft_token" and ttft is None:
                                ttft = time.perf_counter() - start
                        elif raw.startswith("data:") and event == "final":
                            final = time.perf_counter() - start
                            data = json.loads(raw[5:])
                            status = data.get("status", "")
                            degraded = bool(data.get("degraded"))
                        elif raw.startswith("data:") and event == "error":
                            error = "error event"
                    if final is None and not error:
                        error = "stream ended without a final event"
                    r.success() if not error else r.failure(error)
        except Exception as exc:  # noqa: BLE001 - recorded, the test goes on
            error = type(exc).__name__
        _record({"tool": "locust", "user": self.n, "question": question, "ttft_s": ttft,
                 "full_s": final, "status": status, "degraded": degraded,
                 "error": error, "started_at": time.time()})
        if ttft is not None:
            events.request.fire(request_type="SSE", name="time to first draft token",
                                response_time=ttft * 1000, response_length=0, exception=None, context={})
