"""M18c: the k6 and Locust load tests (loadtest/, scripts/run_load_tests.py). The numbers
below are test inputs; the measured results are in eval/results/<ts>_load/.

Covers: R-13.3-load (k6 and Locust scripts at 1, 5 and 10 users; p95 TTFT and full answer
against the targets, reported as met or not met, never adjusted).
"""

from app.config import PROJECT_ROOT
from scripts.run_load_tests import API_ENV, summarise


def test_the_load_test_reports_p95_against_the_targets() -> None:
    """Covers: R-13.3-load (p95 TTFT and full answer per level, target met or not)."""
    rows = [{"tool": "locust", "users": 5, "ttft_s": t, "full_s": f, "status": "answered",
             "degraded": d, "error": ""}
            for t, f, d in ((0.9, 5.0, False), (1.2, 6.0, False), (2.5, 12.0, True))]
    rows.append({"tool": "locust", "users": 5, "ttft_s": None, "full_s": None, "status": "",
                 "degraded": False, "error": "HTTP 429"})
    s = summarise(rows)
    assert (s["requests"], s["errors"], s["ttft_measured"], s["degraded"]) == (4, 1, 3, 1)
    assert s["ttft_s_p95"] == 2.5 and s["full_answer_s_p95"] == 12.0
    assert s["ttft_target_met"] is False and s["full_answer_target_met"] is False
    fast = summarise([{**r, "ttft_s": 0.5, "full_s": 4.0} for r in rows[:3]])
    assert fast["ttft_target_met"] is True and fast["full_answer_target_met"] is True


def test_both_tools_measure_the_streamed_answer() -> None:
    locust = (PROJECT_ROOT / "loadtest" / "locust" / "locustfile.py").read_text(encoding="utf-8")
    k6 = (PROJECT_ROOT / "loadtest" / "k6" / "query.js").read_text(encoding="utf-8")
    assert "draft_token" in locust and "stream=True" in locust and '"final"' in locust
    assert "/api/v1/query" in k6 and "event: final" in k6 and "per-vu-iterations" in k6
    for script in (locust, k6):
        assert "LOADTEST_PASSWORD" in script  # the password comes from the environment only
    # The cache is off so every request is answered; nothing that times requests is changed.
    assert API_ENV["RESPONSE_CACHE_ENABLED"] == "false" and "LATENCY_TARGET_SECONDS" not in API_ENV
