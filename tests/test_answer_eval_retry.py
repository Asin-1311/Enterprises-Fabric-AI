"""M18b: an answer-evaluation run can retry answers that ended in an error (a lost network
connection), and the outputs then use the latest attempt for each question and system.
"""

import json
from pathlib import Path

from eval.run_answer_eval import append_progress, latest_results, load_progress


def _row(qid: str, system: str, error: str = "", status: str = "answered") -> dict:
    return {"kind": "result", "session": "s", "row": {"id": qid, "system": system, "error": error,
                                                      "status": status}, "answer": {}}


def test_a_retry_replaces_the_error_and_keeps_every_attempt_on_disk(tmp_path: Path) -> None:
    progress = tmp_path / "progress.jsonl"
    for record in (_row("q1", "full"), _row("q2", "full", error="APIConnectionError: Connection error."),
                   _row("q2", "baseline"), _row("q2", "full", status="answered")):
        append_progress(progress, record)
    records = load_progress(progress)
    latest = latest_results(records)
    assert latest[("q2", "full")]["row"]["error"] == ""  # the retry wins
    assert len(latest) == 3 and len([r for r in records if r["kind"] == "result"]) == 4  # nothing deleted
    assert json.loads(progress.read_text(encoding="utf-8").splitlines()[1])["row"]["error"]
