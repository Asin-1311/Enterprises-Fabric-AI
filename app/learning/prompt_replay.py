"""Prompt replay: a new prompt version must do as well as the active one before it can go
live (CLAUDE.md section 13.3; Gap Register R-13.3-replay; M15c).

    .venv\\Scripts\\python.exe -m app.learning.prompt_replay --version-id 42
    .venv\\Scripts\\python.exe -m app.learning.prompt_replay --version-id 42 --confirm-paid

Replay calls the real model (paid), so without --confirm-paid it only says how many calls
it would make. It runs the agent's own code twice on the same recorded inputs
(eval/replay/prompt_replay.jsonl, made by scripts/make_prompt_replay.py): once with the
candidate prompt and once with the active one, and scores both the same way:

  query_understanding  the labelled intent is found (1/0)
  routing_tiebreak     a gold document's source type is among those chosen (1/0)
  reformulate          a search with the reworded queries finds a gold document (top 10)
  compress             the gold chunk keeps at least one sentence (1/0)
  synthesis            the answer cites the gold chunk and no chunk it was not given (1/0)
  verification         share of the recorded claims given the expected verdict

The candidate passes when its mean score is at most PROMPT_REPLAY_TOLERANCE below the
active version's. The report (scores, calls, tokens, estimated and measured cost, how
many labels are reviewed) is stored on the version, so the activation endpoint can check
it; with --confirm-paid it is also written to eval/results/<timestamp>_prompt_replay/.
Model outputs vary from run to run: a report is one run, not a fixed property.
"""

import argparse
import csv
import json
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.agents.llm import LLMCaller, LLMResult
from app.agents.query import ClientMatcher, understand
from app.agents.reranker import Ranked, compress_evidence, whole_chunks
from app.agents.retriever import reformulate
from app.agents.router import tiebreak
from app.agents.synthesizer import DocInfo, synthesize
from app.agents.verifier import verify
from app.config import PROJECT_ROOT, Settings
from app.db.models import Document, PromptVersion
from app.db.session import write_audit_log
from app.learning.prompt_registry import PromptedLLM
from app.retrieval.acl import UserContext, load_user

REPLAY_FILE = PROJECT_ROOT / "eval" / "replay" / "prompt_replay.jsonl"
RESULTS_DIR = PROJECT_ROOT / "eval" / "results"
SCORED_AGENTS = ("query_understanding", "routing_tiebreak", "reformulate", "compress",
                 "synthesis", "verification")


def load_records(path: Path = REPLAY_FILE) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def records_for(agent: str, records: list[dict]) -> list[dict]:
    """The records an agent is replayed on: every record for the intent classifier, the
    retrieval records (with their recorded inputs) for the others."""
    if agent == "query_understanding":
        return records
    if agent == "verification":
        return [r for r in records if r.get("claims")]
    return [r for r in records if r.get("chunk_ids")]


@dataclass
class MeteredLLM:
    """Counts the calls and tokens of a replay, per model."""

    llm: LLMCaller
    calls: int = 0
    tokens: dict[str, list[int]] = field(default_factory=dict)  # model -> [in, out]

    def call(self, **kwargs) -> LLMResult:
        result = self.llm.call(**kwargs)
        self.calls += 1
        used = self.tokens.setdefault(result.model, [0, 0])
        used[0] += result.tokens_in
        used[1] += result.tokens_out
        return result


@dataclass
class ReplayContext:
    """What the scorers need besides the model: the index and the people."""

    searcher: object  # HybridSearcher
    clients: ClientMatcher
    settings: Settings
    users: dict[str, UserContext] = field(default_factory=dict)

    def user(self, user_id: str) -> UserContext:
        if user_id not in self.users:
            with self.searcher.sessions() as s:
                self.users[user_id] = load_user(s, user_id)
        return self.users[user_id]

    def chunks(self, record: dict) -> list:
        return self.searcher.fetch(record["chunk_ids"], self.user(record["user_id"]))

    def docs(self, doc_ids: set[str]) -> dict[str, DocInfo]:
        with self.searcher.sessions() as s:
            rows = s.scalars(select(Document).where(Document.doc_id.in_(doc_ids))).all()
        return {d.doc_id: DocInfo(d.title, d.version, d.effective_date, d.source_type) for d in rows}


# ------------------------------------------------------------------- the scorers

def _intent(r: dict, llm: LLMCaller, ctx: ReplayContext) -> tuple[float, str]:
    analysis, _ = understand(r["question"], llm, ctx.clients)
    return float(analysis.intent == r["intent"]), analysis.intent


def _tiebreak(r: dict, llm: LLMCaller, ctx: ReplayContext) -> tuple[float, str]:
    picked, _ = tiebreak(r["question"], llm)
    return float(bool(set(picked) & set(r["gold_source_types"]))), ",".join(picked)


def _reformulate(r: dict, llm: LLMCaller, ctx: ReplayContext) -> tuple[float, str]:
    queries, _ = reformulate(r["question"], llm)
    found = ctx.searcher.search(queries, ctx.user(r["user_id"]), limit=10) if queries else []
    return float(any(c.doc_id in r["gold_doc_ids"] for c in found)), " | ".join(queries)


def _ranked(r: dict, ctx: ReplayContext) -> list[Ranked]:
    return [Ranked(c, 1.0, r["question"]) for c in ctx.chunks(r)]


def _compress(r: dict, llm: LLMCaller, ctx: ReplayContext) -> tuple[float, str]:
    evidence, _ = compress_evidence(r["question"], _ranked(r, ctx), llm, 0.0)
    kept = {e.chunk_id: len(e.sentences) for e in evidence}
    return float(kept.get(r["gold_chunk_id"], 0) > 0), json.dumps(kept)


def _synthesis(r: dict, llm: LLMCaller, ctx: ReplayContext) -> tuple[float, str]:
    ranked = _ranked(r, ctx)
    chunks = {x.chunk.chunk_id: x.chunk for x in ranked}
    evidence = whole_chunks(ranked, 0.0)
    result, _ = synthesize(r["question"], evidence, chunks, ctx.docs({c.doc_id for c in chunks.values()}), llm)
    ok = (result.status == "answered" and r["gold_chunk_id"] in result.cited_chunk_ids
          and not result.invalid_citations)
    return float(ok), f"{result.status}; cited {result.cited_chunk_ids}; invalid {result.invalid_citations}"


def _verification(r: dict, llm: LLMCaller, ctx: ReplayContext) -> tuple[float, str]:
    gold = [c for c in ctx.chunks(r) if c.chunk_id == r["gold_chunk_id"]]
    chunks = {c.chunk_id: c for c in gold}
    cid = r["gold_chunk_id"]
    draft = "\n".join(f"{c['text'].rstrip('.')} [{cid}]." for c in r["claims"])
    v, _ = verify(r["question"], draft, chunks, llm, ctx.docs({c.doc_id for c in gold}), [cid])
    got = [v.verdicts.get(i + 1, {}).get("verdict", "missing") for i in range(len(r["claims"]))]
    right = sum(g == c["expected"] for g, c in zip(got, r["claims"], strict=True))
    return right / len(r["claims"]), ",".join(got)


SCORERS: dict[str, Callable[[dict, LLMCaller, ReplayContext], tuple[float, str]]] = {
    "query_understanding": _intent, "routing_tiebreak": _tiebreak, "reformulate": _reformulate,
    "compress": _compress, "synthesis": _synthesis, "verification": _verification,
}


def score(agent: str, template: str, records: list[dict], llm: LLMCaller,
          ctx: ReplayContext) -> list[dict]:
    """Per-record scores of one prompt text. A failed call (refusal, invalid output, error)
    scores 0 for that record: a prompt that breaks the agent must not pass."""
    prompted = PromptedLLM(llm, {agent: template})
    out = []
    for r in records_for(agent, records):
        try:
            value, detail = SCORERS[agent](r, prompted, ctx)
        except Exception as exc:  # noqa: BLE001 - recorded as a failure of this prompt
            value, detail = 0.0, f"error: {type(exc).__name__}"
        out.append({"id": r["id"], "score": round(value, 4), "detail": detail})
    return out


def _mean(rows: list[dict]) -> float:
    return round(sum(r["score"] for r in rows) / len(rows), 4) if rows else 0.0


def planned_calls(agent: str, records: list[dict]) -> int:
    """Model calls a replay makes: one per record, for the candidate and the active prompt."""
    return 2 * len(records_for(agent, records))


def replay_version(sessions: sessionmaker[Session], version_id: int, llm: LLMCaller,
                   ctx: ReplayContext, records: list[dict] | None = None,
                   actor: str = "learning") -> tuple[dict, list[dict]]:
    """Replay a candidate version against its agent's active version, store the report on
    the candidate and return (report, per-record rows)."""
    from app.agents.versions import ensure_active_versions

    records = records if records is not None else load_records()
    ensure_active_versions(sessions)  # the seed versions exist to compare against
    with sessions() as s:
        candidate = s.get(PromptVersion, version_id)
        if candidate is None:
            raise LookupError(f"There is no prompt version {version_id}.")
        if candidate.agent not in SCORERS:
            raise ValueError(f"No replay scorer for agent {candidate.agent!r}.")
        active = s.scalars(select(PromptVersion).where(PromptVersion.agent == candidate.agent,
                                                       PromptVersion.is_active.is_(True))).one()
        if active.id == candidate.id:
            raise ValueError(f"{candidate.agent} v{candidate.version} is already active.")
        agent, cand_text, base_text = candidate.agent, candidate.template, active.template
        cand_no, base_id, base_no = candidate.version, active.id, active.version
    metered = MeteredLLM(llm)
    started = time.perf_counter()
    cand_rows = score(agent, cand_text, records, metered, ctx)
    base_rows = score(agent, base_text, records, metered, ctx)
    used = records_for(agent, records)
    labels = ([c for r in used for c in r["claims"]] if agent == "verification" else used)
    tolerance = ctx.settings.prompt_replay_tolerance
    cand_score, base_score = _mean(cand_rows), _mean(base_rows)
    report = {
        "kind": "prompt", "agent": agent, "version": cand_no, "baseline_version": base_no,
        "baseline_version_id": base_id, "score": cand_score, "baseline_score": base_score,
        "tolerance": tolerance, "passed": cand_score >= base_score - tolerance,
        "records": len(used), "labels_reviewed": sum(bool(x.get("reviewed")) for x in labels),
        "labels": len(labels), "replay_file": REPLAY_FILE.name, "calls": metered.calls,
        "tokens": metered.tokens, "seconds": round(time.perf_counter() - started, 1),
        "ran_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    with sessions() as s:
        candidate = s.get(PromptVersion, version_id)
        candidate.eval_report = {**(candidate.eval_report or {}), "replay": report}
        candidate.eval_score = cand_score
        s.flush()
        write_audit_log(s, actor, "prompt_replayed", f"{agent} v{cand_no}: "
                        f"{'passed' if report['passed'] else 'FAILED'} ({cand_score} vs {base_score})")
    rows = [{"id": c["id"], "candidate": c["score"], "baseline": b["score"],
             "candidate_detail": c["detail"], "baseline_detail": b["detail"]}
            for c, b in zip(cand_rows, base_rows, strict=True)]
    return report, rows


def write_results(report: dict, rows: list[dict], settings: Settings,
                  results_dir: Path = RESULTS_DIR) -> Path:
    """eval/results/<timestamp>_prompt_replay/: metrics.json, per_question.csv, config.json."""
    out = results_dir / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}_prompt_replay"
    out.mkdir(parents=True)
    (out / "metrics.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    with (out / "per_question.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["id"])
        writer.writeheader()
        writer.writerows(rows)
    config = {"model_light": settings.model_light, "model_synth": settings.model_synth,
              "model_verify": settings.model_verify, "effort_light": settings.effort_light,
              "effort_synth": settings.effort_synth, "effort_verify": settings.effort_verify,
              "prompt_replay_tolerance": settings.prompt_replay_tolerance}
    (out / "config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
    return out


def _cost(report: dict) -> dict:
    """Estimated spend from token counts and the listed prices (eval/costs.py)."""
    from eval.costs import estimate_usd

    def listed(model: str) -> str:  # "claude-haiku-5-5" -> "anthropic/claude-haiku-5.5"
        if model.startswith("anthropic/"):
            return model
        name, _, minor = model.rpartition("-")
        return f"anthropic/{name}.{minor}"

    return {"estimated_usd": round(sum(estimate_usd(listed(m), i, o)
                                       for m, (i, o) in report["tokens"].items()), 4),
            "basis": "token counts x eval/costs.py list prices (an estimate)"}


def main(argv: list[str] | None = None) -> int:
    from app.agents.graph import build_assistant
    from app.config import get_settings
    from app.retrieval.dense import QdrantInUseError
    from eval.costs import openrouter_balance

    parser = argparse.ArgumentParser(description="Replay a prompt version (paid model calls).")
    parser.add_argument("--version-id", type=int, required=True, help="prompt_versions.id")
    parser.add_argument("--confirm-paid", action="store_true",
                        help="make the model calls (without it, only the plan is shown)")
    args = parser.parse_args(argv)
    settings = get_settings()
    records = load_records()
    try:
        assistant = build_assistant(settings)
    except QdrantInUseError as exc:
        print(exc)
        return 2
    deps = assistant.deps
    with deps.sessions() as s:
        version = s.get(PromptVersion, args.version_id)
        if version is None:
            print(f"There is no prompt version {args.version_id}.")
            return 1
        agent = version.agent
    calls = planned_calls(agent, records)
    if not args.confirm_paid:
        print(f"Replaying {agent} v{version.version} makes {calls} model calls "
              f"({calls // 2} records x candidate and active prompt). Re-run with --confirm-paid.")
        return 3
    before = openrouter_balance(settings.llm_base_url, settings.llm_auth_token)
    ctx = ReplayContext(deps.searcher, deps.clients, settings)
    report, rows = replay_version(deps.sessions, args.version_id, deps.llm, ctx, records)
    after = openrouter_balance(settings.llm_base_url, settings.llm_auth_token)
    report["cost"] = _cost(report) | {
        "measured_usd": round(before - after, 4) if before is not None and after is not None else None}
    with deps.sessions() as s:  # the cost belongs in the stored report too
        v = s.get(PromptVersion, args.version_id)
        v.eval_report = {**v.eval_report, "replay": report}
        s.commit()
    out = write_results(report, rows, settings)
    print(f"{agent} v{report['version']}: {report['score']} vs active v{report['baseline_version']} "
          f"{report['baseline_score']} -> {'PASSED' if report['passed'] else 'FAILED'} "
          f"({report['calls']} calls; cost {report['cost']}). Results: {out.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
