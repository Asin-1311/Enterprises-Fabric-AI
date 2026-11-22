"""Retrieval evaluation: recall@5, recall@10, MRR, nDCG@10 (overall and per source type),
access-leak and cross-engagement-leak rates, and superseded ordering, for five modes:

    dense       dense only               (the single-pass baseline's retriever)
    bm25        BM25 only
    hybrid_raw  dense + BM25 with RRF
    hybrid      RRF + superseded weight + recency boost (the Phase 1 system)
    adaptive    hybrid with the retriever weights and candidate counts of the question's
                labelled intent (M15a: what the system now uses; app/agents/plan.py)

Leak checks use their OWN access rule, read directly from manifest.csv, users.csv and
engagements.csv, so a bug in app/retrieval/acl.py cannot hide itself.

Run from the project root (no LLM calls, no cost):
    .venv\\Scripts\\python.exe -m eval.run_retrieval_eval
    .venv\\Scripts\\python.exe -m eval.run_retrieval_eval --corpus synthetic

--corpus synthetic (M17c) is what a clean clone and CI can measure: only the questions
whose gold documents are all committed SYNTHETIC documents are scored (questions without
gold documents still run, for the leak checks), and the index must hold synthetic
documents only (the run refuses otherwise). Those runs are written as
`<timestamp>_retrieval_synthetic/`, a separate type: they never mix with the full-corpus
figures in /metrics/quality or the report. The CI regression gate (eval/gates.py)
compares them with eval/baselines/retrieval_synthetic.json.

Writes eval/results/<timestamp>_retrieval/ with metrics.json, per_question.csv,
config.json and model_ids.json. Results are never edited by hand.
"""

import argparse
import csv
import json
import time
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path

from app.config import PROJECT_ROOT, get_settings
from app.retrieval.acl import load_user
from app.agents.plan import intent_profile
from app.retrieval.hybrid import MODES as SEARCH_MODES
from app.retrieval.hybrid import build_searcher
from app.security.redaction import snapshot_json
from eval.question_sets import load_questions
from eval.metrics import (
    dedupe_docs,
    mean,
    ndcg_at_k,
    recall_at_k,
    reciprocal_rank,
    superseded_order_ok,
)

MODES = (*SEARCH_MODES, "adaptive")
DATA = PROJECT_ROOT / "data"
LEVEL = {"staff": 0, "manager": 1, "partner": 2}


# ------------------------------------------------- independent access rule (for leaks)

def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_ground_truth() -> tuple[dict, dict, dict]:
    """(docs, roles, teams) straight from the CSV files."""
    docs = {r["doc_id"]: r for r in _read_csv(DATA / "manifest.csv")}
    roles = {u["user_id"]: u["role"] for u in _read_csv(DATA / "users.csv")}
    teams = {e["engagement_id"]: set(e["member_ids"].split(";")) | {e["partner_id"]}
             for e in _read_csv(DATA / "engagements.csv")}
    return docs, roles, teams


def leak_flags(user_id: str, doc_id: str, docs: dict, roles: dict, teams: dict) -> tuple[bool, bool]:
    """(access_leak, cross_engagement_leak) for one retrieved document."""
    d, role = docs[doc_id], roles[user_id]
    access_leak = LEVEL[d["access_level"]] > LEVEL[role]
    eng = d["engagement_id"]
    cross = bool(eng) and role != "partner" and user_id not in teams.get(eng, set())
    return access_leak, cross


# --------------------------------------------------------------------------- run

CORPORA = ("full", "synthetic")


def synthetic_questions(questions: list[dict], docs: dict) -> tuple[list[dict], int]:
    """(questions a synthetic-only index can score, how many were left out): those whose
    gold documents are all synthetic, plus those without gold documents."""
    kept = [q for q in questions
            if all(docs.get(g, {}).get("synthetic") == "true" for g in q["gold_doc_ids"])]
    return kept, len(questions) - len(kept)


def _non_synthetic_documents(sessions) -> int:
    from sqlalchemy import func, select

    from app.db.models import Document

    with sessions() as s:
        return s.scalar(select(func.count()).select_from(Document).where(Document.synthetic.is_(False)))


def run(k_docs: int = 10, chunk_limit: int = 40, corpus: str = "full") -> Path:
    if corpus not in CORPORA:
        raise ValueError(f"corpus must be one of {CORPORA}")
    settings = get_settings()
    # The general set and the per-source files (eval/question_sets.py, M18a).
    questions = load_questions()
    docs, roles, teams = load_ground_truth()
    as_of = date.today()
    excluded = 0
    if corpus == "synthetic":
        questions, excluded = synthetic_questions(questions, docs)

    searcher = build_searcher(settings)
    sessions = searcher.sessions  # the active profile's metadata store
    if corpus == "synthetic" and (n := _non_synthetic_documents(sessions)):
        raise SystemExit(f"--corpus synthetic needs an index of synthetic documents only; this one "
                         f"holds {n} other document(s). Index into an empty store with "
                         f"MANIFEST_PATH=data/manifest.synthetic.csv (see docs/OPERATIONS.md).")

    rows: list[dict] = []
    latencies: dict[str, list[float]] = defaultdict(list)
    for q in questions:
        with sessions() as s:
            user = load_user(s, q["user_id"])
        for mode in MODES:
            t0 = time.perf_counter()
            weights, top_k = intent_profile(q["intent"], settings) if mode == "adaptive" else (None, None)
            hits = searcher.search(q["question"], user, mode="hybrid" if mode == "adaptive" else mode,
                                   limit=chunk_limit, time_sensitive=q["intent"] == "time_sensitive",
                                   as_of=as_of, weights=weights, top_k=top_k)
            latencies[mode].append((time.perf_counter() - t0) * 1000)
            ranked = dedupe_docs(h.doc_id for h in hits)[:k_docs]
            gold = set(q["gold_doc_ids"])
            flags = [leak_flags(q["user_id"], d, docs, roles, teams) for d in ranked]
            row = {
                "id": q["id"], "mode": mode, "user_id": q["user_id"], "intent": q["intent"],
                "category": q.get("category", ""), "set": q["set"], "reviewed": q.get("reviewed", False),
                "gold": ";".join(sorted(gold)), "retrieved_top10": ";".join(ranked),
                "access_leak": any(a for a, _ in flags), "cross_engagement_leak": any(c for _, c in flags),
                "recall@5": None, "recall@10": None, "mrr": None, "ndcg@10": None,
                "superseded_order_ok": None,
            }
            if gold:
                row.update({"recall@5": recall_at_k(ranked, gold, 5),
                            "recall@10": recall_at_k(ranked, gold, 10),
                            "mrr": reciprocal_rank(ranked, gold),
                            "ndcg@10": ndcg_at_k(ranked, gold, 10)})
            old = q.get("superseded_doc_ids") or []
            if old:
                # The specific newer version (via the manifest `supersedes` link) must rank
                # above its predecessor; other gold documents do not count here.
                newer = {r["supersedes"]: d for d, r in docs.items() if r["supersedes"]}
                row["superseded_order_ok"] = superseded_order_ok(ranked, [(newer[o], o) for o in old])
            rows.append(row)

    metrics = {"by_mode": {}, "corpus": corpus, "n_questions": len(questions),
               "n_questions_excluded": excluded,
               "n_reviewed": sum(1 for q in questions if q.get("reviewed")),
               "questions_by_set": dict(sorted(Counter(q["set"] for q in questions).items())),
               "as_of": as_of.isoformat(), "k_docs": k_docs}
    for mode in MODES:
        mrows = [r for r in rows if r["mode"] == mode]
        scored = [r for r in mrows if r["recall@5"] is not None]
        sup = [r for r in mrows if r["superseded_order_ok"] is not None]
        per_type: dict[str, dict] = {}
        for st in sorted({docs[g]["source_type"] for r in scored for g in r["gold"].split(";")}):
            found5 = found10 = total = 0
            for r in scored:
                for g in r["gold"].split(";"):
                    if docs[g]["source_type"] == st:
                        total += 1
                        top = r["retrieved_top10"].split(";")
                        found5 += g in top[:5]
                        found10 += g in top[:10]
            per_type[st] = {"gold_docs": total, "recall@5": found5 / total, "recall@10": found10 / total}
        lat = sorted(latencies[mode])
        metrics["by_mode"][mode] = {
            "questions_scored": len(scored),
            "recall@5": mean([r["recall@5"] for r in scored]),
            "recall@10": mean([r["recall@10"] for r in scored]),
            "mrr": mean([r["mrr"] for r in scored]),
            "ndcg@10": mean([r["ndcg@10"] for r in scored]),
            "access_leak_rate": mean([float(r["access_leak"]) for r in mrows]),
            "cross_engagement_leak_rate": mean([float(r["cross_engagement_leak"]) for r in mrows]),
            "superseded_order_accuracy": mean([float(r["superseded_order_ok"]) for r in sup]),
            "superseded_questions": len(sup),
            "per_source_type": per_type,
            "latency_ms_p50": lat[len(lat) // 2],
            "latency_ms_p95": lat[min(len(lat) - 1, int(round(0.95 * (len(lat) - 1))))],
        }

    kind = "retrieval" if corpus == "full" else "retrieval_synthetic"
    out = PROJECT_ROOT / "eval" / "results" / f"{datetime.now():%Y%m%d-%H%M%S}_{kind}"
    out.mkdir(parents=True)
    (out / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    with (out / "per_question.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    config = settings.model_dump()
    config |= {"chunk_limit": chunk_limit, "k_docs": k_docs, "modes": list(MODES), "corpus": corpus}
    (out / "config.json").write_text(snapshot_json(config), encoding="utf-8")
    (out / "model_ids.json").write_text(json.dumps({
        "embedding_model": settings.embedding_model,
        "llm_models_used": "none (retrieval-only evaluation)",
    }, indent=2), encoding="utf-8")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the retrieval evaluation.")
    parser.add_argument("--corpus", choices=CORPORA, default="full",
                        help="synthetic: score only what the committed synthetic corpus can answer (CI)")
    args = parser.parse_args()
    out = run(corpus=args.corpus)
    metrics = json.loads((out / "metrics.json").read_text(encoding="utf-8"))
    print(f"Results: {out.relative_to(PROJECT_ROOT)}  "
          f"(corpus={metrics['corpus']}, questions={metrics['n_questions']}, "
          f"excluded={metrics['n_questions_excluded']}, reviewed={metrics['n_reviewed']})")
    cols = {"recall@5": "R@5", "recall@10": "R@10", "mrr": "MRR", "ndcg@10": "nDCG@10",
            "access_leak_rate": "role_leak", "cross_engagement_leak_rate": "eng_leak",
            "superseded_order_accuracy": "superseded_ok", "latency_ms_p95": "p95_ms"}
    print(f"{'mode':<11}" + "".join(f"{h:>14}" for h in cols.values()))
    for mode, m in metrics["by_mode"].items():
        print(f"{mode:<11}" + "".join(
            f"{m[c]:>14.3f}" if isinstance(m[c], float) else f"{str(m[c]):>14}" for c in cols))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
