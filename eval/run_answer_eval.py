"""Answer evaluation and ablation: full system vs single-pass baseline (CLAUDE.md section 11).

For every question, both systems answer; an LLM judge (eval/judge.py) grades each answer.
Reported per system, overall and per category:
- answer correctness vs gold (correct 1, partial 0.5, incorrect 0),
- faithfulness (judge, per claim) and citation precision (judge, per claim-citation pair),
- access-leak and cross-engagement-leak rates over every chunk RETRIEVED or CITED, using
  the independent access rule from eval/run_retrieval_eval.py (must be 0),
- content-leak rate: a "forbidden fact" of a denied question appears in the answer,
- latency p50/p95: time to first token and to the full answer, against the project
  document's targets (p95 TTFT < 1.5 s, full answer < 8 s).

Note on TTFT for the full system: it streams only the VERIFIED answer (M6 decision), so its
first token can only be sent once verification is complete; TTFT is therefore the time to
the verified answer. The time to the first DRAFT token is recorded separately for context.

Progress is saved after EVERY answer to progress.jsonl in the run folder, so an interrupted
run loses nothing that was already paid for and can be resumed. The OpenRouter balance is
recorded after every question, so the measured spend of an interrupted session is known too.

Run from the project root (makes billed LLM calls):
    .venv\\Scripts\\python.exe -m eval.run_answer_eval --limit 5                     # pilot
    .venv\\Scripts\\python.exe -m eval.run_answer_eval                               # full run
    .venv\\Scripts\\python.exe -m eval.run_answer_eval --resume 20261113-101500_answers

Writes eval/results/<timestamp>_answers/: metrics.json, per_question.csv, answers.jsonl,
config.json, model_ids.json (rewritten at the end of every session, with "complete" saying
whether every question is done) and progress.jsonl. Results are never edited by hand.
"""

import argparse
import csv
import json
import math
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

from app.config import PROJECT_ROOT, get_settings
from app.db.models import Chunk, Document, Trace
from app.retrieval.acl import load_user
from eval.costs import estimate_usd, openrouter_balance
from eval.judge import judge_answer
from eval.metrics import mean
from eval.run_retrieval_eval import SECRET_FIELDS, leak_flags, load_ground_truth

SYSTEMS = ("full", "baseline")
TARGETS = {"ttft_p95_s": 1.5, "full_answer_p95_s": 8.0}
RESULTS = PROJECT_ROOT / "eval" / "results"


def percentile(values: list[float], p: float) -> float | None:
    """Nearest-rank percentile (p in 0..1); None for an empty list."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(p * len(ordered)) - 1)]


def summarise(rows: list[dict]) -> dict:
    """Aggregate metrics over per-question rows of one system."""
    ok = [r for r in rows if not r["error"]]
    judged = [r for r in ok if r["score"] is not None]
    faith = [r["faithfulness"] for r in judged if r["faithfulness"] is not None]
    fact = sum(r["factual_claims"] or 0 for r in judged)
    sup = sum(r["supported_claims"] or 0 for r in judged)
    pairs = sum(r["citation_pairs"] or 0 for r in judged)
    good = sum(r["supporting_citations"] or 0 for r in judged)
    return {
        "questions": len(rows), "errors": len(rows) - len(ok), "judged": len(judged),
        "correctness": mean([r["score"] for r in judged]),
        "correct_rate": mean([float(r["correctness"] == "correct") for r in judged]),
        "faithfulness_macro": mean(faith),
        "faithfulness_micro": sup / fact if fact else None,
        "citation_precision": good / pairs if pairs else None,
        "access_leak_rate": mean([float(r["access_leak"]) for r in ok]),
        "cross_engagement_leak_rate": mean([float(r["cross_engagement_leak"]) for r in ok]),
        "content_leak_rate": mean([float(r["content_leak"]) for r in ok
                                   if r["category"] in ("role_denied", "cross_engagement")]),
        "status_counts": dict(Counter(r["status"] for r in ok)),
        "ttft_s_p50": percentile([r["ttft_s"] for r in ok if r["ttft_s"] is not None], 0.5),
        "ttft_s_p95": percentile([r["ttft_s"] for r in ok if r["ttft_s"] is not None], 0.95),
        "full_answer_s_p50": percentile([r["seconds"] for r in ok], 0.5),
        "full_answer_s_p95": percentile([r["seconds"] for r in ok], 0.95),
        "estimated_answer_cost_usd": round(sum(r["est_cost_usd"] for r in ok), 4),
    }


# ------------------------------------------------------------------- progress file

def append_progress(path: Path, record: dict) -> None:
    """Append one JSON record and flush it to disk immediately.

    If a crash left a half-written last line, start a new line first, so the new record is
    not glued onto the broken one (load_progress then skips only the broken line)."""
    if path.exists() and path.stat().st_size:
        with path.open("rb") as f:
            f.seek(-1, 2)
            needs_newline = f.read(1) != b"\n"
        if needs_newline:
            with path.open("a", encoding="utf-8") as f:
                f.write("\n")
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        f.flush()


def load_progress(path: Path) -> list[dict]:
    """All records written so far (an incomplete last line, from a crash, is skipped)."""
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def measured_spend(records: list[dict]) -> tuple[float | None, int]:
    """Sum over sessions of (balance at session start - last balance seen in that session).

    Returns (spend, sessions counted). None if no session has two balance readings; a
    session cut off before its first per-question reading is not counted."""
    first: dict[str, float] = {}
    last: dict[str, float] = {}
    readings: Counter[str] = Counter()
    for r in records:
        if r.get("kind") == "balance" and r.get("balance") is not None:
            first.setdefault(r["session"], r["balance"])
            last[r["session"]] = r["balance"]
            readings[r["session"]] += 1
    counted = [s for s in first if readings[s] >= 2]
    if not counted:
        return None, 0
    return round(sum(first[s] - last[s] for s in counted), 4), len(counted)


# ---------------------------------------------------------------------------- run

def run(questions: list[dict], use_judge: bool = True, resume: Path | None = None) -> Path:
    from app.agents.graph import build_assistant
    from app.baseline.single_pass_rag import SinglePassRAG

    settings = get_settings()
    out = resume or RESULTS / f"{datetime.now():%Y%m%d-%H%M%S}_answers"
    out.mkdir(parents=True, exist_ok=True)
    progress = out / "progress.jsonl"
    records = load_progress(progress)
    done = {(r["row"]["id"], r["row"]["system"]) for r in records if r.get("kind") == "result"}
    session = datetime.now().strftime("%Y%m%d-%H%M%S")

    def note_balance(after: str) -> None:
        append_progress(progress, {"kind": "balance", "session": session, "after": after,
                                   "balance": openrouter_balance(settings.llm_base_url,
                                                                 settings.llm_auth_token)})

    note_balance("session_start")
    assistant = build_assistant(settings)
    searcher, llm, sessions = assistant.deps.searcher, assistant.deps.llm, assistant.deps.sessions
    baseline = SinglePassRAG(searcher, llm)
    docs, roles, teams = load_ground_truth()

    # Record every chunk any retrieval returns, for the leak checks.
    retrieved: list[str] = []
    original_search = searcher.search

    def recording_search(*args, **kwargs):
        hits = original_search(*args, **kwargs)
        retrieved.extend(h.chunk_id for h in hits)
        return hits

    searcher.search = recording_search  # type: ignore[method-assign]

    def chunk_texts(ids: list[str]) -> dict[str, str]:
        """Full chunk text, prefixed with the document facts the synthesiser saw (title,
        version, effective date, supersede links), so version/date claims can be judged."""
        with sessions() as s:
            all_docs = {d.doc_id: d for d in s.scalars(select(Document))}
            rows = s.execute(select(Chunk.chunk_id, Chunk.doc_id, Chunk.text)
                             .where(Chunk.chunk_id.in_(ids))).all()
        out_texts = {}
        for cid, doc_id, text in rows:
            d = all_docs[doc_id]
            newer = next((n for n in all_docs.values() if n.supersedes == doc_id), None)
            facts = [f"title: {d.title}", f"version: {d.version}", f"effective_date: {d.effective_date}"]
            if d.supersedes and d.supersedes in all_docs:
                facts.append(f"replaces {d.supersedes} (effective {all_docs[d.supersedes].effective_date})")
            if newer is not None:
                facts.append(f"superseded by {newer.doc_id} (effective {newer.effective_date})")
            out_texts[cid] = f"[document facts: {'; '.join(facts)}]\n{text}"
        return out_texts

    todo = [q for q in questions if any((q["id"], s) not in done for s in SYSTEMS)]
    print(f"Run folder: {out.relative_to(PROJECT_ROOT)}  |  already done: "
          f"{len(questions) - len(todo)}  |  to do now: {len(todo)}", flush=True)
    try:
        for n, q in enumerate(todo, 1):
            with sessions() as s:
                user = load_user(s, q["user_id"])
            summary = []
            for system in SYSTEMS:
                if (q["id"], system) in done:
                    continue
                retrieved.clear()
                row = {"id": q["id"], "category": q.get("category", ""), "user_id": q["user_id"],
                       "system": system, "reviewed": q.get("reviewed", False), "error": "",
                       "status": "", "confidence": None, "seconds": None, "ttft_s": None,
                       "draft_ttft_s": None, "tokens_in": 0, "tokens_out": 0, "est_cost_usd": 0.0,
                       "judge_cost_usd": 0.0}
                text, cited = "", []
                start = time.perf_counter()
                try:
                    if system == "full":
                        first: list[float] = []
                        res = assistant.ask(q["question"], q["user_id"],
                                            on_token=lambda _t: first or first.append(time.perf_counter() - start))
                        row["seconds"] = time.perf_counter() - start
                        row["ttft_s"] = row["seconds"]  # verified-only streaming (see docstring)
                        row["draft_ttft_s"] = first[0] if first else None
                        row["status"], row["confidence"] = res.status, res.confidence
                        text, cited = res.text, [c.chunk_id for c in res.citations]
                        with sessions() as s:
                            for t in s.scalars(select(Trace).where(Trace.query_id == res.query_id)):
                                if t.model and t.tokens_in is not None:  # one model per agent step
                                    row["tokens_in"] += t.tokens_in
                                    row["tokens_out"] += t.tokens_out or 0
                                    row["est_cost_usd"] += estimate_usd(t.model.split(",")[0],
                                                                        t.tokens_in, t.tokens_out or 0)
                    else:
                        b = baseline.ask(q["question"], user)
                        row["seconds"], row["ttft_s"] = b.seconds, b.seconds_to_first_token
                        row["status"] = "answered"
                        text, cited = b.text, b.cited_chunk_ids
                        row["tokens_in"], row["tokens_out"] = b.llm.tokens_in, b.llm.tokens_out
                        row["est_cost_usd"] = estimate_usd(b.llm.model, b.llm.tokens_in, b.llm.tokens_out)
                except Exception as exc:  # noqa: BLE001 - record and continue
                    row["error"] = f"{type(exc).__name__}: {exc}"[:300]

                seen_docs = {cid.split(":")[0] for cid in [*retrieved, *cited]}
                flags = [leak_flags(q["user_id"], d, docs, roles, teams) for d in seen_docs if d in docs]
                row["access_leak"] = any(a for a, _ in flags)
                row["cross_engagement_leak"] = any(c for _, c in flags)
                row["content_leak"] = any(f.lower() in text.lower() for f in q.get("forbidden_facts", []))

                row.update(correctness=None, score=None, faithfulness=None, factual_claims=None,
                           supported_claims=None, citation_pairs=None, supporting_citations=None)
                judge_raw = None
                if use_judge and not row["error"]:
                    try:
                        j, jres = judge_answer(question=q, answer_text=text,
                                               cited_texts=chunk_texts(cited), llm=llm)
                        row["judge_cost_usd"] = estimate_usd(jres.model, jres.tokens_in, jres.tokens_out)
                        row.update(correctness=j.correctness, score=j.score, faithfulness=j.faithfulness,
                                   factual_claims=j.factual_claims, supported_claims=j.supported_claims,
                                   citation_pairs=j.citation_pairs,
                                   supporting_citations=j.supporting_citations)
                        judge_raw = j.raw
                    except Exception as exc:  # noqa: BLE001
                        row["error"] = f"judge {type(exc).__name__}: {exc}"[:300]
                answer = {"id": q["id"], "system": system, "user_id": q["user_id"],
                          "question": q["question"], "gold_answer": q["gold_answer"],
                          "category": q.get("category", ""), "answer": text, "cited": cited,
                          "status": row["status"], "judge": judge_raw}
                append_progress(progress, {"kind": "result", "session": session, "row": row,
                                           "answer": answer})
                done.add((q["id"], system))
                summary.append(f"{system}: {row['status'] or 'ERROR'} {row['correctness'] or ''} "
                               f"{row['seconds'] or 0:.1f}s")
            note_balance(q["id"])
            print(f"[{n}/{len(todo)}] {q['id']}  " + "  ".join(summary), flush=True)
    finally:
        searcher.search = original_search  # type: ignore[method-assign]
        write_outputs(out, questions, use_judge)
    return out


def write_outputs(out: Path, questions: list[dict], use_judge: bool) -> None:
    """(Re)write metrics.json, per_question.csv, answers.jsonl, config and model ids from
    everything in progress.jsonl. Called at the end of every session, even an interrupted one."""
    settings = get_settings()
    records = load_progress(out / "progress.jsonl")
    wanted = {q["id"] for q in questions}
    results = [r for r in records if r.get("kind") == "result" and r["row"]["id"] in wanted]
    rows = [r["row"] for r in results]
    answers = [r["answer"] for r in results]
    spend, sessions_counted = measured_spend(records)
    complete = {(q["id"], s) for q in questions for s in SYSTEMS} <= {(r["id"], r["system"]) for r in rows}
    metrics: dict = {
        "complete": complete,
        "n_questions": len(questions),
        "n_answered_pairs": len(rows),
        "n_reviewed": sum(1 for q in questions if q.get("reviewed")),
        "judge_model": settings.model_verify if use_judge else None,
        "targets": TARGETS,
        "systems": {},
        "estimated_judge_cost_usd": round(sum(r.get("judge_cost_usd", 0.0) for r in rows), 4),
        "measured_spend_usd": spend,
        "measured_spend_sessions": sessions_counted,
    }
    for system in SYSTEMS:
        srows = [r for r in rows if r["system"] == system]
        by_cat = defaultdict(list)
        for r in srows:
            by_cat[r["category"]].append(r)
        metrics["systems"][system] = {"overall": summarise(srows) if srows else None,
                                      "by_category": {c: summarise(v) for c, v in sorted(by_cat.items())}}
    (out / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    if rows:
        fields = list(dict.fromkeys(k for r in rows for k in r))
        with (out / "per_question.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)
    with (out / "answers.jsonl").open("w", encoding="utf-8") as f:
        for a in answers:
            f.write(json.dumps(a, ensure_ascii=False) + "\n")
    config = settings.model_dump(exclude=SECRET_FIELDS)
    config |= {"question_ids": [q["id"] for q in questions], "judge": use_judge}
    (out / "config.json").write_text(json.dumps(config, indent=2, default=str), encoding="utf-8")
    (out / "model_ids.json").write_text(json.dumps({
        "light": settings.model_light, "synth": settings.model_synth,
        "verify": settings.model_verify, "judge": settings.model_verify if use_judge else None,
        "embedding": settings.embedding_model, "reranker": settings.reranker_model,
        "baseline_synth": settings.model_synth}, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Answer evaluation: full system vs baseline.")
    parser.add_argument("--limit", type=int, help="only the first N questions (pilot)")
    parser.add_argument("--ids", help="comma-separated question ids")
    parser.add_argument("--no-judge", action="store_true", help="skip the LLM judge")
    parser.add_argument("--resume", help="continue an interrupted run (folder name in eval/results)")
    args = parser.parse_args()
    questions = [json.loads(line) for line in (PROJECT_ROOT / "eval" / "questions.jsonl")
                 .read_text(encoding="utf-8").splitlines() if line.strip()]
    resume = None
    if args.resume:
        resume = RESULTS / args.resume
        if not (resume / "progress.jsonl").exists():
            raise SystemExit(f"No progress.jsonl in {resume}; nothing to resume.")
        previous = json.loads((resume / "config.json").read_text(encoding="utf-8"))
        ids = set(previous["question_ids"])
        questions = [q for q in questions if q["id"] in ids]  # same questions as the original run
    else:
        if args.ids:
            wanted = set(args.ids.split(","))
            questions = [q for q in questions if q["id"] in wanted]
        if args.limit:
            questions = questions[: args.limit]
    out = run(questions, use_judge=not args.no_judge, resume=resume)
    m = json.loads((out / "metrics.json").read_text(encoding="utf-8"))
    print(f"\nResults: {out.relative_to(PROJECT_ROOT)}  complete={m['complete']}  "
          f"questions={m['n_questions']}  reviewed={m['n_reviewed']}  measured spend=${m['measured_spend_usd']}")
    keys = ["correctness", "faithfulness_micro", "citation_precision", "access_leak_rate",
            "cross_engagement_leak_rate", "content_leak_rate", "ttft_s_p95", "full_answer_s_p95"]
    print(f"{'system':<10}" + "".join(f"{k[:18]:>20}" for k in keys))
    for system, s in m["systems"].items():
        o = s["overall"] or {}
        print(f"{system:<10}" + "".join(
            f"{o.get(k):>20.3f}" if isinstance(o.get(k), float) else f"{str(o.get(k)):>20}" for k in keys))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
