"""Sanity checks on the labelled test set eval/questions.jsonl."""

import json

import pytest

from app.config import PROJECT_ROOT
from eval.run_retrieval_eval import leak_flags, load_ground_truth

REQUIRED = {"id", "question", "user_id", "intent", "gold_doc_ids", "gold_answer", "notes", "reviewed"}
INTENTS = {"no_retrieval", "factual", "procedural", "comparison", "time_sensitive"}
QUESTIONS = [json.loads(line) for line in (PROJECT_ROOT / "eval" / "questions.jsonl")
             .read_text(encoding="utf-8").splitlines() if line.strip()]


def test_ids_unique_and_fields_present() -> None:
    assert len({q["id"] for q in QUESTIONS}) == len(QUESTIONS)
    for q in QUESTIONS:
        assert REQUIRED <= set(q), q["id"]
        assert q["intent"] in INTENTS, q["id"]
        assert isinstance(q["reviewed"], bool)


@pytest.mark.parametrize("q", QUESTIONS, ids=lambda q: q["id"])
def test_gold_documents_exist_and_are_visible_to_the_asker(q: dict) -> None:
    docs, roles, teams = load_ground_truth()
    assert q["user_id"] in roles
    for d in q["gold_doc_ids"] + q.get("superseded_doc_ids", []):
        assert d in docs, f"{q['id']}: unknown doc {d}"
        assert leak_flags(q["user_id"], d, docs, roles, teams) == (False, False), \
            f"{q['id']}: gold doc {d} is not visible to {q['user_id']}"


def test_denied_cases_have_no_gold_documents_and_list_forbidden_facts() -> None:
    for q in QUESTIONS:
        if q.get("category") in ("role_denied", "cross_engagement", "no_retrieval"):
            assert q["gold_doc_ids"] == [], q["id"]
        if q.get("category") in ("role_denied", "cross_engagement"):
            assert q.get("forbidden_facts"), q["id"]


def test_forbidden_facts_really_are_restricted_from_the_asker() -> None:
    """Each forbidden fact must not appear in any document the asker may see, otherwise
    an answer containing it would be a false 'content leak'."""
    from app.ingest.md_loader import load_markdown

    docs, roles, teams = load_ground_truth()
    for q in QUESTIONS:
        visible = [d for d, row in docs.items() if row["path"].endswith(".md")
                   and leak_flags(q["user_id"], d, docs, roles, teams) == (False, False)]
        texts = [load_markdown(PROJECT_ROOT / docs[d]["path"]).text for d in visible]
        for fact in q.get("forbidden_facts", []):
            hits = [d for d, t in zip(visible, texts) if fact in t]
            assert not hits, f"{q['id']}: '{fact}' is visible to {q['user_id']} in {hits}"


def test_quotas_from_claude_md_section_11() -> None:
    from collections import Counter

    cats = Counter(q.get("category") for q in QUESTIONS)
    assert len(QUESTIONS) >= 60
    assert cats["superseded"] >= 10 and cats["role_denied"] >= 10
    assert cats["cross_engagement"] >= 5 and cats["multi_source"] >= 10
    assert cats["no_retrieval"] >= 5
