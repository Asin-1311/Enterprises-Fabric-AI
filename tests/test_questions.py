"""Sanity checks on the labelled evaluation questions: the general set eval/questions.jsonl
and, from M18a, the per-source files eval/questions/<source_type>.jsonl.

Covers: R-13.1-sets (one evaluation file per source type, at least 10 questions each),
R-3-eval (the reviewed count is reported).
"""

import json
from collections import Counter

import pytest

from eval.question_sets import (MIN_PER_SOURCE, SOURCE_TYPES, load_questions, problems, summary)
from eval.question_sets import INTENTS as KNOWN_INTENTS
from eval.run_retrieval_eval import leak_flags, load_ground_truth

REQUIRED = {"id", "question", "user_id", "intent", "gold_doc_ids", "gold_answer", "notes", "reviewed"}
INTENTS = set(KNOWN_INTENTS)
QUESTIONS = load_questions()
GENERAL = [q for q in QUESTIONS if q["set"] == "general"]


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
    from app.config import PROJECT_ROOT
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
    cats = Counter(q.get("category") for q in GENERAL)
    assert len(GENERAL) >= 60
    assert cats["superseded"] >= 10 and cats["role_denied"] >= 10
    assert cats["cross_engagement"] >= 5 and cats["multi_source"] >= 10
    assert cats["no_retrieval"] >= 5


def test_one_file_per_source_type_with_at_least_ten_questions() -> None:
    """Covers: R-13.1-sets (eval/questions/<source_type>.jsonl, at least 10 questions each,
    every gold-labelled one with a gold document of that type; at least 10 time-sensitive
    questions overall)."""
    docs, roles, teams = load_ground_truth()
    assert problems(QUESTIONS, docs, roles, teams) == []
    for source_type in SOURCE_TYPES:
        scored = [q for q in QUESTIONS if q["set"] == source_type and q["gold_doc_ids"]]
        assert len(scored) >= MIN_PER_SOURCE, source_type
        assert all(any(docs[d]["source_type"] == source_type for d in q["gold_doc_ids"]) for q in scored)
    assert sum(q["intent"] == "time_sensitive" for q in QUESTIONS) >= 10


def test_the_validator_catches_bad_questions() -> None:
    docs, roles, teams = load_ground_truth()
    good = next(q for q in QUESTIONS if q["set"] == "firm_policy" and q["user_id"].startswith("M")
                and any(docs[d]["access_level"] == "manager" for d in q["gold_doc_ids"]))
    as_staff = {**good, "id": "x1", "user_id": "S01"}  # a manager-level gold doc for a staff user
    unknown = {**good, "id": "x2", "gold_doc_ids": ["NO-SUCH-DOC"]}
    wrong_type = {**good, "id": "x3", "set": "calendar"}
    found = problems([*QUESTIONS, as_staff, unknown, wrong_type], docs, roles, teams)
    assert any("x1" in f and "may not see" in f for f in found)
    assert any("x2" in f and "not in the manifest" in f for f in found)
    assert any("x3" in f and "no gold document of type calendar" in f for f in found)
    assert any(f == "duplicate id x1" for f in problems([*QUESTIONS, as_staff, as_staff], docs, roles, teams))


def test_the_reviewed_count_is_reported() -> None:
    """Covers: R-3-eval (how many questions the author has reviewed is counted, never assumed)."""
    s = summary(QUESTIONS)
    assert s["questions"] == len(QUESTIONS) and s["reviewed"] == sum(q["reviewed"] for q in QUESTIONS)
    assert sum(s["by_set"].values()) == len(QUESTIONS)
    assert set(s["by_set"]) == {"general", *SOURCE_TYPES}
    for q in QUESTIONS:
        assert json.loads(json.dumps(q)) == q  # plain JSON, nothing the report cannot store
