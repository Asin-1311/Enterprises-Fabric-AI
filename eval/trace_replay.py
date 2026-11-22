"""Trace replay: the whole answer pipeline re-run on recorded traces, without a model call
(CLAUDE.md section 13.3 "Replay tests"; Gap Register R-13.3-replay; M18b).

    python -m eval.trace_replay record --confirm-paid   # in a clean clone: live model calls
    python -m eval.trace_replay check                    # anywhere, free: CI runs this

eval/replay/answer_traces.jsonl holds, for each recorded question:
- the inputs: question, user and the date it was asked;
- the chunk ids every retrieval returned;
- every model request (purpose and SHA-256 of the exact system + user text) with the
  model's response;
- the outcome: status, answer text, cited chunk ids and the verifier's flags.

`check` rebuilds the assistant with a ReplayLLM: each request is answered with the
recorded response for exactly that request (same purpose and hash), on the recording's
date. If retrieval, re-ranking, compression, routing, a prompt or any agent's code now
builds a different request, there is no recorded answer: the trace no longer replays
and counts as a mismatch. A trace whose requests all match must also reproduce the
recorded outcome. The score is the share of traces reproduced exactly. It must not fall
below the accepted baseline (eval/baselines/trace_replay.json) by more than
TRACE_REPLAY_TOLERANCE.

Two things are kept as they were recorded, so a replay can reproduce them:
- the model router: traces are recorded without a private model, and replayed behind a
  ModelRouter without one, so a request holding restricted text is refused in the replay
  exactly as it was when recorded;
- only deterministic runs: recording and replay run without the WALL-CLOCK step budget
  (whether it fires depends on how fast the machine and the model are that day); should a
  run still be stopped by it, the recorder leaves it out and says so. Runs stopped by a
  call or token budget are kept: they replay the same way.

This is a regression test of the deterministic parts around the model. It cannot score a
NEW prompt (a new prompt means new requests), which is what the paid prompt replay
(app/learning/prompt_replay.py) does. It is recorded on the synthetic corpus only, in a
clean clone, so CI can rebuild exactly the same index. It is run with degradation and the
response cache off, so every request is made.
"""

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from collections.abc import Callable
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

from app.agents.llm import LLMCaller, LLMResult
from app.config import PROJECT_ROOT, get_settings
from app.security.redaction import snapshot_json

TRACES = PROJECT_ROOT / "eval" / "replay" / "answer_traces.jsonl"
BASELINE = PROJECT_ROOT / "eval" / "baselines" / "trace_replay.json"
RESULTS = PROJECT_ROOT / "eval" / "results"
# Every request must be made, however fast the machine: no degradation, no cached answers,
# and no wall-clock step budget (a slow CI runner would otherwise turn a reproduced run into
# an abstention). Call and token budgets stay as configured: they replay deterministically.
ISOLATION = {"DEGRADATION_ENABLED": "false", "RESPONSE_CACHE_ENABLED": "false",
             "BUDGET_MAX_SECONDS": "86400"}


class ReplayMismatch(RuntimeError):
    """The pipeline built a request that the trace has no recorded response for."""


def request_hash(purpose: str, system: str, user: str) -> str:
    return hashlib.sha256(f"{purpose}\x00{system}\x00{user}".encode("utf-8")).hexdigest()


class RecordingLLM:
    """Passes every call through and records the request hash and the response."""

    def __init__(self, inner: LLMCaller) -> None:
        self.inner, self.calls = inner, []

    def call(self, *, purpose: str, role: str, system: str, user: str, max_tokens: int,
             schema: dict | None = None, on_text: Callable[[str], None] | None = None) -> LLMResult:
        r = self.inner.call(purpose=purpose, role=role, system=system, user=user,
                            max_tokens=max_tokens, schema=schema, on_text=on_text)
        self.calls.append({"purpose": purpose, "request": request_hash(purpose, system, user),
                           "text": r.text, "data": r.data, "model": r.model, "effort": r.effort})
        return r


class ReplayLLM:
    """Answers each request with its recorded response; a request never seen is a mismatch."""

    def __init__(self, calls: list[dict]) -> None:
        self.responses = {(c["purpose"], c["request"]): c for c in calls}
        self.used: set[tuple[str, str]] = set()

    def call(self, *, purpose: str, role: str, system: str, user: str, max_tokens: int,
             schema: dict | None = None, on_text: Callable[[str], None] | None = None) -> LLMResult:
        key = (purpose, request_hash(purpose, system, user))
        if key not in self.responses:
            raise ReplayMismatch(f"no recorded response for a {purpose} request")
        self.used.add(key)
        c = self.responses[key]
        if on_text is not None and c["text"]:
            on_text(c["text"])  # streamed as one piece: the draft view gets the same text
        return LLMResult(c["text"], c["data"], c["model"], c["effort"], 0, 0, 0.0)


def outcome(result) -> dict:
    return {"status": result.status, "text": result.text,
            "citations": sorted(c.chunk_id for c in result.citations),
            "flagged_claims": len(result.flagged_claims)}


def instrument(assistant) -> list[str]:
    """Record the chunk ids every retrieval of `assistant` returns (into the list returned)."""
    retrieved: list[str] = []
    search = assistant.deps.searcher.search

    def recording_search(*args, **kwargs):
        hits = search(*args, **kwargs)
        retrieved.extend(h.chunk_id for h in hits)
        return hits

    assistant.deps.searcher.search = recording_search  # type: ignore[method-assign]
    return retrieved


def _live_assistant():
    from app.agents.graph import build_assistant

    os.environ.update(ISOLATION)
    get_settings.cache_clear()
    assistant = build_assistant(get_settings())
    return assistant, instrument(assistant)


def _frozen_today(day: date):
    class Frozen(date):
        @classmethod
        def today(cls):  # noqa: D102
            return day
    return mock.patch("app.retrieval.hybrid.date", Frozen)


# --------------------------------------------------------------------------- record

def budget_limit(assistant, query_id: int) -> str | None:
    """Which step budget stopped this query, if one did (the `budget_exceeded` trace row)."""
    from sqlalchemy import select

    from app.db.models import Trace

    with assistant.deps.sessions() as s:
        row = s.scalars(select(Trace).where(Trace.query_id == query_id,
                                            Trace.agent == "budget_exceeded")).first()
    if row is None:
        return None
    data = row.input if isinstance(row.input, dict) else json.loads(row.input or "{}")
    return data.get("limit", "unknown")


def record_one(q: dict, assistant, retrieved: list[str], day: date) -> dict:
    """Answer one question through a RecordingLLM and return its trace."""
    recorder = RecordingLLM(assistant.deps.llm)
    assistant.deps.llm = recorder
    retrieved.clear()
    try:
        with _frozen_today(day):
            result = assistant.ask(q["question"], q["user_id"])
    finally:
        assistant.deps.llm = recorder.inner
    return {"id": q["id"], "question": q["question"], "user_id": q["user_id"],
            "asked_on": day.isoformat(), "retrieved": list(retrieved),
            "calls": recorder.calls, "outcome": outcome(result),
            "budget_limit": budget_limit(assistant, result.query_id)}


def record(questions: list[dict], wanted: int, path: Path = TRACES) -> Path:
    """Answer questions with the live model until `wanted` deterministic traces exist (paid)."""
    if (PROJECT_ROOT / "data" / "corpus" / "standards").exists():
        raise SystemExit("Record traces in a clean clone: the licensed ICAI chapters are present here, "
                         "and CI could not rebuild this index.")
    if get_settings().private_model:
        raise SystemExit("Record without PRIVATE_MODEL: CI replays behind a router with no private model.")
    assistant, retrieved = _live_assistant()
    rows = []
    for q in questions:
        if len(rows) == wanted:
            break
        trace = record_one(q, assistant, retrieved, date.today())
        if trace["budget_limit"] == "time":
            print(f"left out {q['id']}: stopped by the wall-clock budget (timing-dependent)", flush=True)
            continue
        rows.append(trace)
        print(f"recorded {q['id']}: {trace['outcome']['status']}, {len(trace['calls'])} model calls",
              flush=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------- check

def replay_one(trace: dict, assistant, retrieved: list[str]) -> dict:
    """Replay one trace; returns why it does or does not reproduce."""
    from app.agents.model_router import ModelRouter

    inner = assistant.deps.llm
    assistant.deps.llm = ModelRouter(ReplayLLM(trace["calls"]), None)  # as recorded: no private model
    retrieved.clear()
    try:
        with _frozen_today(date.fromisoformat(trace["asked_on"])):
            result = assistant.ask(trace["question"], trace["user_id"])
    except ReplayMismatch as exc:
        return {"id": trace["id"], "reproduced": False, "reason": str(exc)}
    finally:
        assistant.deps.llm = inner
    got = outcome(result)
    if got != trace["outcome"]:
        diff = sorted(k for k in got if got[k] != trace["outcome"].get(k))
        return {"id": trace["id"], "reproduced": False, "reason": f"different outcome: {diff}"}
    # Parallel sub-queries finish in any order, so the retrieved chunks are compared as a
    # multiset: the same chunks, as many times, in whatever order the searches completed.
    if Counter(retrieved) != Counter(trace["retrieved"]):
        return {"id": trace["id"], "reproduced": False, "reason": "different retrieved chunks"}
    return {"id": trace["id"], "reproduced": True, "reason": ""}


def check(path: Path = TRACES, results: Path = RESULTS, write: bool = True) -> tuple[float, list[dict]]:
    traces = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assistant, retrieved = _live_assistant()  # its model is replaced by each trace's ReplayLLM
    rows = [replay_one(t, assistant, retrieved) for t in traces]
    score = sum(r["reproduced"] for r in rows) / len(rows) if rows else 0.0
    if write:
        out = results / f"{datetime.now():%Y%m%d-%H%M%S}_trace_replay"
        out.mkdir(parents=True)
        (out / "metrics.json").write_text(json.dumps({
            "score": round(score, 4), "traces": len(rows), "reproduced": sum(r["reproduced"] for r in rows),
            "trace_file": path.name, "ran_at": datetime.now(timezone.utc).isoformat(timespec="seconds")},
            indent=2), encoding="utf-8")
        (out / "per_question.csv").write_text(
            "id,reproduced,reason\n" + "".join(f"{r['id']},{r['reproduced']},{r['reason']}\n" for r in rows),
            encoding="utf-8")
        (out / "config.json").write_text(snapshot_json(ISOLATION), encoding="utf-8")
    return score, rows


def run_values(run_dir: Path) -> dict:
    """What a trace-replay baseline holds, read from a run folder."""
    m = json.loads((run_dir / "metrics.json").read_text(encoding="utf-8"))
    return {"score": m["score"], "traces": m["traces"]}


def gate(score: float, traces: int, baseline: dict, tolerance: float) -> list[str]:
    base = baseline["values"]
    fails = []
    if traces != base["traces"]:
        fails.append(f"{traces} traces, the baseline has {base['traces']}: accept a new baseline from a run")
    if score < base["score"] - tolerance - 1e-9:
        fails.append(f"trace replay {score:.4f} is below the baseline {base['score']:.4f} - {tolerance}")
    return fails


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Trace replay (free) or trace recording (paid).")
    sub = parser.add_subparsers(dest="command", required=True)
    r = sub.add_parser("record")
    r.add_argument("--confirm-paid", action="store_true")
    r.add_argument("--limit", type=int, default=20)
    sub.add_parser("check")
    args = parser.parse_args(argv)
    if args.command == "record":
        from eval.question_sets import load_questions
        from eval.run_retrieval_eval import load_ground_truth, synthetic_questions

        docs, _, _ = load_ground_truth()
        questions, _ = synthetic_questions(load_questions(), docs)
        questions = [q for q in questions if q["gold_doc_ids"]]
        if not args.confirm_paid:
            print(f"Would record up to {args.limit} traces with live model calls; add --confirm-paid.")
            return 0
        print(f"wrote {record(questions, args.limit)}")
        return 0
    score, rows = check()
    for row in rows:
        if not row["reproduced"]:
            print(f"  {row['id']}: {row['reason']}")
    from eval.gates import _baseline_matches_its_run

    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    fails = _baseline_matches_its_run(BASELINE.name, baseline, run_values, RESULTS)
    fails += gate(score, len(rows), baseline, get_settings().trace_replay_tolerance)
    print(f"Trace replay: {sum(r['reproduced'] for r in rows)}/{len(rows)} reproduced (score {score:.4f})")
    for f in fails:
        print(f"  FAIL: {f}")
    print("TRACE REPLAY GATE", "PASSED" if not fails else "FAILED")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
