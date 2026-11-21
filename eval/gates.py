"""The CI quality gates (M17c; CLAUDE.md sections 13.1, 13.3 and 14). No LLM calls.

    python -m eval.gates retrieval [--run DIR]   # newest *_retrieval_synthetic run by default
    python -m eval.gates routing-replay          # replay now, write the run, compare
    python -m eval.gates prompts                 # changed seed prompts need a passing replay
    python -m eval.gates accept retrieval --run DIR
    python -m eval.gates accept routing-replay --run DIR
    python -m eval.gates accept prompts [--initial]

1. Retrieval regression gate (13.1). A run of `eval.run_retrieval_eval --corpus synthetic`
   (what a clean clone can measure) is compared with eval/baselines/retrieval_synthetic.json.
   The build fails if any gated metric, overall or per source type, is more than
   RETRIEVAL_TOLERANCE (default 0.02 absolute) below the baseline, if any mode leaks a
   document across roles or engagements, or if the question set differs from the
   baseline's (then a new baseline must be accepted from a real run).
2. Routing replay gate (13.3). The code's default routing (the rule router and the
   per-intent weights in app/config.py) is replayed on eval/replay/routing_replay.jsonl,
   synthetic records only, and must not lose more than REPLAY_TOLERANCE against
   eval/baselines/replay_synthetic.json. In the running system every NEW routing policy is
   also replay-gated by PUT /api/v1/routing-policy.
3. Prompt gate (13.3). Prompt replay calls the paid model, so CI does not run it. Instead,
   a seed prompt in the code whose text differs from the accepted one
   (eval/baselines/prompts.json) fails the build unless eval/results/ holds a PASSING
   `*_prompt_replay` report for exactly that text (matched by SHA-256). The running system
   also keeps a changed seed inactive until it passes replay (app/agents/versions.py).

Baselines are never typed by hand: `accept` copies the values from a run folder in
eval/results/, and the gates check that they still match that run's files.
"""

import argparse
import csv
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from app.config import PROJECT_ROOT, get_settings

RESULTS = PROJECT_ROOT / "eval" / "results"
BASELINES = PROJECT_ROOT / "eval" / "baselines"
RETRIEVAL_BASELINE = BASELINES / "retrieval_synthetic.json"
REPLAY_BASELINE = BASELINES / "replay_synthetic.json"
PROMPT_BASELINE = BASELINES / "prompts.json"

GATED_MODE = "adaptive"  # what the system uses (app/agents/plan.py)
GATED_METRICS = ("recall@5", "recall@10", "mrr", "ndcg@10", "superseded_order_accuracy")
PER_TYPE_METRICS = ("recall@5", "recall@10")
LEAK_METRICS = ("access_leak_rate", "cross_engagement_leak_rate")


# --------------------------------------------------------------------- reading runs

def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _question_ids(run_dir: Path) -> list[str]:
    with (run_dir / "per_question.csv").open(newline="", encoding="utf-8") as f:
        return sorted({row["id"] for row in csv.DictReader(f)})


def gated_values(metrics: dict) -> dict[str, float]:
    """The values the retrieval gate compares, flattened from a run's metrics.json."""
    m = metrics["by_mode"][GATED_MODE]
    values = {f"{GATED_MODE}.{k}": m[k] for k in GATED_METRICS if m.get(k) is not None}
    for source_type, v in sorted(m["per_source_type"].items()):
        for k in PER_TYPE_METRICS:
            values[f"{GATED_MODE}.{source_type}.{k}"] = v[k]
    return values


def newest_run(kind: str, results: Path = RESULTS) -> Path:
    runs = sorted(p for p in results.glob(f"*_{kind}") if (p / "metrics.json").is_file())
    if not runs:
        raise SystemExit(f"No *_{kind} run in {results}.")
    return runs[-1]


def _baseline_matches_its_run(name: str, baseline: dict, values_of_run, results: Path) -> list[str]:
    """A baseline must still equal the run it was accepted from. The run folder is
    committed with the baseline, so a missing folder is a failure too."""
    run = results / baseline["source_run"]
    if not (run / "metrics.json").is_file():
        return [f"{name}: its source run {run.name} is not in {results.name}/."]
    if values_of_run(run) != baseline["values"]:
        return [f"{name} does not match its source run {run.name}: baselines are accepted "
                f"from runs, never edited."]
    return []


# --------------------------------------------------------------------- 1. retrieval

def retrieval_failures(metrics: dict, question_ids: list[str], baseline: dict,
                       tolerance: float) -> list[str]:
    """Why a retrieval run fails the gate (empty list = it passes)."""
    fails = []
    if metrics.get("corpus") != baseline["corpus"]:
        fails.append(f"corpus {metrics.get('corpus')!r} differs from the baseline's {baseline['corpus']!r}.")
    if question_ids != baseline["question_ids"]:
        fails.append("The question set differs from the baseline's: accept a new baseline from a real run.")
    for mode, m in metrics["by_mode"].items():
        for k in LEAK_METRICS:
            if m[k] > 0:
                fails.append(f"{mode}.{k} = {m[k]:.3f}: documents leaked across roles or engagements.")
    values = gated_values(metrics)
    for key, base in baseline["values"].items():
        if key not in values:
            fails.append(f"{key}: missing from the run (baseline {base:.3f}).")
        elif values[key] < base - tolerance - 1e-9:
            fails.append(f"{key}: {values[key]:.3f} is below the baseline {base:.3f} - {tolerance}.")
    return fails


def gate_retrieval(run_dir: Path, results: Path = RESULTS) -> int:
    settings = get_settings()
    baseline = _read_json(RETRIEVAL_BASELINE)
    metrics = _read_json(run_dir / "metrics.json")
    fails = _baseline_matches_its_run(RETRIEVAL_BASELINE.name, baseline,
                                      lambda r: gated_values(_read_json(r / "metrics.json")), results)
    fails += retrieval_failures(metrics, _question_ids(run_dir), baseline, settings.retrieval_tolerance)
    print(f"Retrieval gate: {run_dir.name} against {baseline['source_run']} "
          f"(tolerance {settings.retrieval_tolerance}, {len(baseline['values'])} values)")
    for key, base in baseline["values"].items():
        print(f"  {key:<45} baseline {base:.3f}   run {gated_values(metrics).get(key, float('nan')):.3f}")
    return _verdict("RETRIEVAL GATE", fails)


# ---------------------------------------------------------------- 2. routing replay

def synthetic_replay_records(records: list[dict], docs: dict) -> list[dict]:
    """Replay records whose gold documents are all committed synthetic documents."""
    return [r for r in records if r["gold_doc_ids"]
            and all(docs.get(g, {}).get("synthetic") == "true" for g in r["gold_doc_ids"])]


def run_routing_replay(results: Path = RESULTS) -> Path:
    """Replay the code's default routing on the synthetic records; write the run folder."""
    from app.adaptive.replay import load_records, score
    from app.retrieval.hybrid import build_searcher
    from eval.run_retrieval_eval import _non_synthetic_documents, load_ground_truth

    settings = get_settings()
    docs, _, _ = load_ground_truth()
    records = synthetic_replay_records(load_records(), docs)
    searcher = build_searcher(settings)
    if n := _non_synthetic_documents(searcher.sessions):
        raise SystemExit(f"The routing replay gate needs a synthetic-only index; this one holds {n} other document(s).")
    value = score(searcher, None, records, settings)
    out = results / f"{datetime.now():%Y%m%d-%H%M%S}_routing_replay_synthetic"
    out.mkdir(parents=True)
    report = {"metric": "document recall@10", "score": round(value, 4), "policy": "code default (rules)",
              "records": len(records), "record_ids": [r["id"] for r in records], "corpus": "synthetic",
              "ran_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    (out / "metrics.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    with (out / "per_question.csv").open("w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows([["id"], *[[r["id"]] for r in records]])
    (out / "config.json").write_text(json.dumps({"replay_tolerance": settings.replay_tolerance,
                                                 "embedding_model": settings.embedding_model}, indent=2),
                                     encoding="utf-8")
    return out


def _replay_values(run_dir: Path) -> dict:
    m = _read_json(run_dir / "metrics.json")
    return {"score": m["score"], "record_ids": m["record_ids"]}


def replay_failures(report: dict, baseline: dict, tolerance: float) -> list[str]:
    fails = []
    if report["record_ids"] != baseline["values"]["record_ids"]:
        fails.append("The replay records differ from the baseline's: accept a new baseline from a real run.")
    if report["score"] < baseline["values"]["score"] - tolerance - 1e-9:
        fails.append(f"Routing replay {report['score']:.4f} is below the baseline "
                     f"{baseline['values']['score']:.4f} - {tolerance}.")
    return fails


def gate_routing_replay(run_dir: Path | None = None, results: Path = RESULTS) -> int:
    settings = get_settings()
    run_dir = run_dir or run_routing_replay(results)
    baseline = _read_json(REPLAY_BASELINE)
    report = _read_json(run_dir / "metrics.json")
    fails = _baseline_matches_its_run(REPLAY_BASELINE.name, baseline, _replay_values, results)
    fails += replay_failures(report, baseline, settings.replay_tolerance)
    print(f"Routing replay gate: {run_dir.name} score {report['score']:.4f}, baseline "
          f"{baseline['values']['score']:.4f} ({baseline['source_run']}), tolerance {settings.replay_tolerance}")
    return _verdict("ROUTING REPLAY GATE", fails)


# ------------------------------------------------------------------------ 3. prompts

def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def passing_prompt_replay(agent: str, digest: str, results: Path = RESULTS) -> Path | None:
    """The newest PASSING prompt replay run for exactly this agent and text, if any."""
    for run in sorted(results.glob("*_prompt_replay"), reverse=True):
        try:
            m = _read_json(run / "metrics.json")
        except (OSError, ValueError):
            continue
        if m.get("agent") == agent and m.get("template_sha256") == digest and m.get("passed") is True:
            return run
    return None


def prompt_failures(prompts: dict[str, str], accepted: dict, results: Path = RESULTS) -> list[str]:
    fails = []
    for agent, text in sorted(prompts.items()):
        digest = sha256(text)
        if accepted["agents"].get(agent, {}).get("template_sha256") == digest:
            continue
        if passing_prompt_replay(agent, digest, results) is None:
            fails.append(f"{agent}: the seed prompt changed ({digest[:12]}) and there is no passing "
                         f"prompt replay report for it (python -m app.learning.prompt_replay, paid).")
    for agent in sorted(set(accepted["agents"]) - set(prompts)):
        fails.append(f"{agent}: in {PROMPT_BASELINE.name} but no longer in the code.")
    return fails


def _code_prompts() -> dict[str, str]:
    from app.agents.versions import PROMPTS

    return dict(PROMPTS)


def gate_prompts(results: Path = RESULTS) -> int:
    prompts = _code_prompts()
    fails = prompt_failures(prompts, _read_json(PROMPT_BASELINE), results)
    print(f"Prompt gate: {len(prompts)} seed prompts checked against {PROMPT_BASELINE.name}")
    return _verdict("PROMPT GATE", fails)


# ------------------------------------------------------------------------- accepting

def accept_retrieval(run_dir: Path) -> None:
    metrics = _read_json(run_dir / "metrics.json")
    if metrics.get("corpus") != "synthetic":
        raise SystemExit("Only a --corpus synthetic run can become the CI baseline.")
    _write(RETRIEVAL_BASELINE, {
        "source_run": run_dir.name, "corpus": "synthetic", "mode": GATED_MODE,
        "question_ids": _question_ids(run_dir), "values": gated_values(metrics),
        "n_reviewed": metrics["n_reviewed"], "accepted_at": _today(),
    })


def accept_routing_replay(run_dir: Path) -> None:
    _write(REPLAY_BASELINE, {"source_run": run_dir.name, "metric": "document recall@10",
                             "values": _replay_values(run_dir), "accepted_at": _today()})


def accept_prompts(initial: bool = False, results: Path = RESULTS) -> None:
    prompts = _code_prompts()
    if initial:
        if PROMPT_BASELINE.exists():
            raise SystemExit(f"{PROMPT_BASELINE.name} exists; a changed prompt needs a passing replay.")
        agents = {a: {"template_sha256": sha256(t), "replay_run": None,
                      "reason": "reference text when the gate was introduced (M17c): the seed that "
                                "becomes version 1 on a new database"} for a, t in prompts.items()}
    else:
        agents = _read_json(PROMPT_BASELINE)["agents"]
        for agent, text in prompts.items():
            digest = sha256(text)
            if agents.get(agent, {}).get("template_sha256") == digest:
                continue
            run = passing_prompt_replay(agent, digest, results)
            if run is None:
                raise SystemExit(f"{agent}: no passing prompt replay report for the new text.")
            agents[agent] = {"template_sha256": digest, "replay_run": run.name}
    _write(PROMPT_BASELINE, {"agents": agents, "accepted_at": _today()})


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {path.relative_to(PROJECT_ROOT)}")


def _verdict(name: str, fails: list[str]) -> int:
    for f in fails:
        print(f"  FAIL: {f}")
    print(f"{name} {'PASSED' if not fails else 'FAILED'}")
    return 0 if not fails else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CI quality gates (no LLM calls).")
    sub = parser.add_subparsers(dest="gate", required=True)
    r = sub.add_parser("retrieval")
    r.add_argument("--run", type=Path)
    rr = sub.add_parser("routing-replay")
    rr.add_argument("--run", type=Path, help="compare an existing run instead of replaying now")
    sub.add_parser("prompts")
    a = sub.add_parser("accept")
    a.add_argument("what", choices=["retrieval", "routing-replay", "prompts"])
    a.add_argument("--run", type=Path)
    a.add_argument("--initial", action="store_true")
    args = parser.parse_args(argv)

    if args.gate == "retrieval":
        return gate_retrieval(args.run or newest_run("retrieval_synthetic"))
    if args.gate == "routing-replay":
        return gate_routing_replay(args.run)
    if args.gate == "prompts":
        return gate_prompts()
    if args.what == "prompts":
        accept_prompts(initial=args.initial)
    elif args.run is None:
        raise SystemExit("accept retrieval / routing-replay needs --run <eval/results folder>")
    elif args.what == "retrieval":
        accept_retrieval(args.run)
    else:
        accept_routing_replay(args.run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
