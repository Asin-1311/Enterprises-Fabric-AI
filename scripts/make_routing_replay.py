"""Record the routing replay set (M15b): eval/replay/routing_replay.jsonl.

    .venv\\Scripts\\python.exe -m scripts.make_routing_replay

For every evaluation question with gold documents (eval/questions.jsonl), records the
inputs a routing policy is replayed on: the question as its one sub-query, the user, the
labelled intent, the source types the rule router chooses, and the gold documents.
Routing is recorded with the rules only, no LLM: where no rule fires, the router's
default sources are recorded (the LLM tie-breaker is not called). Questions the router
denies (a client the user is not assigned to) and greetings are left out.
The file is committed, so every replay uses the same inputs.
"""

import json
import sys

from app.agents.query import ClientMatcher, QueryAnalysis, normalise_sa
from app.agents.router import route
from app.config import PROJECT_ROOT, get_settings
from app.ingest.chunker import extract_sa_refs
from app.retrieval.acl import load_user

QUESTIONS = PROJECT_ROOT / "eval" / "questions.jsonl"
OUT = PROJECT_ROOT / "eval" / "replay" / "routing_replay.jsonl"


class NoLLM:
    """The tie-breaker is never called during recording: the router falls back."""

    def call(self, **kwargs):
        raise RuntimeError("no LLM while recording the replay set")


def main() -> int:
    from app.adapters import open_metadata_store

    metadata = open_metadata_store(get_settings())
    metadata.initialise()
    rows = [json.loads(line) for line in QUESTIONS.read_text(encoding="utf-8").splitlines() if line.strip()]
    records = []
    with metadata.sessions() as s:
        clients = ClientMatcher.from_db(s)
        for q in rows:
            if q["intent"] == "no_retrieval" or not q.get("gold_doc_ids"):
                continue
            user = load_user(s, q["user_id"])
            analysis = QueryAnalysis(q["intent"], normalise_sa(extract_sa_refs(q["question"])),
                                     clients.find(q["question"]), sub_queries=[q["question"]])
            decision, _ = route(q["question"], analysis, user, NoLLM())
            if decision.denied:
                continue
            records.append({"id": q["id"], "question": q["question"], "user_id": q["user_id"],
                            "intent": q["intent"], "sub_queries": [q["question"]],
                            "source_types": decision.source_types, "gold_doc_ids": q["gold_doc_ids"]})
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    print(f"Wrote {OUT.relative_to(PROJECT_ROOT)} ({len(records)} records from {len(rows)} questions).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
