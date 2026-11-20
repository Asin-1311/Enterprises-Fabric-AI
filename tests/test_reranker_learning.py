"""M15c: the re-ranker dataset from verification results and feedback, corrections as
candidate evaluation questions, and the fine-tuning gate.

Fine-tuning is tested on a TINY synthetic set with a tiny randomly initialised model, to
show the pipeline and the gate work; nothing here measures or claims an improvement.

Covers: R-7.9 (the dataset export contains labelled pairs; corrections become candidate
evaluation questions; signals change the routing rewards: tests/test_learned_routing.py).
"""

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

from app.agents.graph import AgentDeps, AuditAssistant
from app.agents.query import ClientMatcher
from app.db.models import Citation, Document, Feedback, Query
from app.learning import reranker_finetune as ft
from app.learning.corrections import correction_candidates
from app.learning.reranker_dataset import export_pairs
from app.retrieval.rerank import CrossEncoderReranker, RerankerNotPromoted, check_promoted
from scripts.export_reranker_dataset import export
from tests.helpers import make_indexer, make_searcher
from tests.test_agents import FakeLLM, FakeReranker, _settings
from tests.test_clean_clone import _clean_clone

QUESTIONS = ["What is our firm's overall materiality benchmark for manufacturing clients?",
             "Within how many days must the final audit file be assembled?",
             "Which NPA classification checks are on our bank branch audit checklist?",
             "How should audit sampling populations be checked for completeness?"]


@pytest.fixture(scope="module")
def answered(tmp_path_factory):
    """Four answered questions, aged past the feedback window, with feedback on two."""
    root = _clean_clone(tmp_path_factory.mktemp("learning"))
    indexer = make_indexer(root, synthetic_fallback=True)
    indexer.sync(actor="test")
    with indexer.sessions() as s:
        clients = ClientMatcher.from_db(s)
    bot = AuditAssistant(AgentDeps(settings=_settings(), llm=FakeLLM(), searcher=make_searcher(indexer, top_k=20),
                                   reranker=FakeReranker(), sessions=indexer.sessions, clients=clients))
    results = [bot.ask(q, "M01") for q in QUESTIONS]
    assert all(r.status == "answered" for r in results)
    old = (datetime.now(timezone.utc) - timedelta(days=2)).replace(tzinfo=None)
    with indexer.sessions() as s:
        s.execute(update(Query).where(Query.id.in_([r.query_id for r in results])).values(created_at=old))
        s.add(Feedback(answer_id=results[0].answer_id, user_id="M01", rating="not_helpful",
                       correction="The benchmark is in Materiality Policy v2.0, section 2.", created_at=old))
        s.add(Feedback(answer_id=results[1].answer_id, user_id="M01", rating="helpful", created_at=old))
        s.commit()
    return indexer, results


def test_the_export_labels_cited_chunks_positive_and_ranked_uncited_chunks_negative(answered) -> None:
    """Covers: R-7.9 (dataset export contains labelled pairs)."""
    indexer, results = answered
    pairs, summary = export_pairs(indexer.sessions, _settings())
    assert summary.answers_used == 3 and summary.skipped == {"not_helpful": 1}
    assert {p.answer_id for p in pairs} == {r.answer_id for r in results[1:]}
    with indexer.sessions() as s:
        cited = {(c.answer_id, c.chunk_id) for c in s.scalars(select(Citation))}
    for p in pairs:
        assert p.label == int((p.answer_id, p.chunk_id) in cited)
        assert p.text and p.query
    assert {p.label for p in pairs} == {0, 1}
    assert summary.positives == sum(p.label for p in pairs) and summary.negatives == len(pairs) - summary.positives
    assert {p.label_source for p in pairs if p.answer_id == results[1].answer_id and p.label} == {"verified_helpful"}
    assert {p.label_source for p in pairs if p.answer_id == results[2].answer_id and p.label} == {"verified"}
    assert {p.label_source for p in pairs if not p.label} == {"ranked_not_cited"}

    # Too recent: nothing is exported before the feedback window has passed.
    early, _ = export_pairs(indexer.sessions, _settings(), now=datetime.now(timezone.utc) - timedelta(days=3))
    assert early == []


def test_chunk_ids_from_before_m12b_are_mapped_to_todays_chunks(answered) -> None:
    """Answers recorded before the stable chunk ids (M12b) cite the old ids, which the
    migration kept in chunks.legacy_chunk_id."""
    from app.db.models import Chunk

    indexer, results = answered
    with indexer.sessions() as s:
        cite = s.scalars(select(Citation).where(Citation.answer_id == results[3].answer_id)).first()
        current = cite.chunk_id
        s.get(Chunk, current).legacy_chunk_id = "OLDDOC:0007"
        cite.chunk_id = "OLDDOC:0007"
        s.commit()
    try:
        pairs, _ = export_pairs(indexer.sessions, _settings())
        mine = [p for p in pairs if p.answer_id == results[3].answer_id and p.label == 1]
        assert current in {p.chunk_id for p in mine} and "OLDDOC:0007" not in {p.chunk_id for p in pairs}
    finally:
        with indexer.sessions() as s:
            s.scalars(select(Citation).where(Citation.chunk_id == "OLDDOC:0007")).first().chunk_id = current
            s.get(Chunk, current).legacy_chunk_id = None
            s.commit()


def test_restricted_text_never_enters_the_dataset(answered) -> None:
    indexer, results = answered
    with indexer.sessions() as s:
        chunk_id = s.scalars(select(Citation.chunk_id).where(Citation.answer_id == results[2].answer_id)).first()
        doc = s.get(Document, chunk_id.split(":")[0])
        before, doc.classification = doc.classification, "restricted"
        s.commit()
    try:
        pairs, summary = export_pairs(indexer.sessions, _settings())
        assert results[2].answer_id not in {p.answer_id for p in pairs}
        assert summary.skipped.get("restricted_text", 0) >= 1
    finally:
        with indexer.sessions() as s:
            s.get(Document, doc.doc_id).classification = before
            s.commit()


def test_corrections_become_unreviewed_candidate_questions_and_files_are_written(answered, tmp_path) -> None:
    """Covers: R-7.9 (corrections become candidate evaluation questions, "reviewed": false)."""
    indexer, results = answered
    candidates = correction_candidates(indexer.sessions)
    assert len(candidates) == 1
    c = candidates[0]
    assert c["reviewed"] is False and c["source"] == "feedback_correction"
    assert c["gold_answer"].startswith("The benchmark is in Materiality Policy")
    assert c["answer_id"] == results[0].answer_id and c["question"] == QUESTIONS[0]
    assert correction_candidates(indexer.sessions, {QUESTIONS[0].upper()}) == []  # already in the set

    questions = tmp_path / "questions.jsonl"
    questions.write_text(json.dumps({"question": "unrelated"}) + "\n", encoding="utf-8")
    written = export(indexer.sessions, _settings(), out_dir=tmp_path / "learning", questions=questions)
    rows = [json.loads(line) for line in written["pairs"].read_text(encoding="utf-8").splitlines()]
    assert {"query", "question", "chunk_id", "text", "label", "label_source", "answer_id"} <= set(rows[0])
    assert written["pairs"].parent == tmp_path / "learning" / "reranker"
    cand = [json.loads(line) for line in written["candidates"].read_text(encoding="utf-8").splitlines()]
    assert [x["reviewed"] for x in cand] == [False]


# ------------------------------------------------------------------ fine-tuning and the gate

WORDS = ("materiality benchmark profit before tax revenue manufacturing percent policy "
         "assembly final audit file days archive sixty sample population complete period "
         "weather cricket lunch canteen parking holiday").split()


@pytest.fixture(scope="module")
def tiny_model(tmp_path_factory):
    """A 1-layer BERT cross-encoder with random weights and a tiny vocabulary (offline)."""
    from transformers import BertConfig, BertForSequenceClassification, BertTokenizer

    folder = tmp_path_factory.mktemp("tiny") / "base"
    folder.mkdir()
    vocab = folder / "vocab.txt"
    vocab.write_text("\n".join(["[PAD]", "[UNK]", "[CLS]", "[SEP]", "[MASK]", *WORDS]) + "\n", encoding="utf-8")
    tokenizer = BertTokenizer(str(vocab))
    import torch

    torch.manual_seed(0)
    model = BertForSequenceClassification(BertConfig(
        vocab_size=len(WORDS) + 5, hidden_size=16, num_hidden_layers=1, num_attention_heads=2,
        intermediate_size=32, max_position_embeddings=64, num_labels=1))
    model.save_pretrained(folder)
    tokenizer.save_pretrained(folder)
    return folder


def _pairs() -> list[dict]:
    topics = [("materiality benchmark", "materiality benchmark profit before tax percent policy"),
              ("final audit file days", "assembly final audit file sixty days archive"),
              ("sample population", "sample population complete period")]
    noise = "weather cricket lunch canteen parking holiday"
    out = []
    for q, good in topics:
        out += [{"query": q, "text": good, "label": 1}, {"query": q, "text": noise, "label": 0}]
    return out * 4


def test_finetuning_runs_on_a_tiny_set_and_the_gate_decides_use(tiny_model, tmp_path) -> None:
    """The pipeline end to end on a tiny synthetic set. Whatever the numbers, the decision
    written to promotion.json is the gate's, and the loader obeys it."""
    leaks = [{"query": "An Eval Question?", "text": "x", "label": 1},
             {"query": "a reworded sub-query", "question": "An eval question?", "text": "x", "label": 0}]
    pairs = ft.without_eval_questions(_pairs() + leaks, ["an eval   question?"])
    assert len(pairs) == len(_pairs())  # evaluation questions never train the model
    out = ft.finetune(str(tiny_model), pairs, tmp_path / "tuned", epochs=2, batch_size=4,
                      learning_rate=1e-3, max_length=32)
    assert (out / "config.json").exists()
    cases = [ft.EvalCase("c1", "materiality benchmark", frozenset({"D1"}),
                         (("n:1", "D9", "weather cricket lunch"), ("g:1", "D1", "materiality benchmark percent"))),
             ft.EvalCase("c2", "final audit file days", frozenset({"D2"}),
                         (("n:2", "D8", "canteen parking holiday"), ("g:2", "D2", "final audit file sixty days")))]
    current, _ = ft.rerank_eval(CrossEncoderReranker(str(tiny_model), gate=False), cases)
    candidate, rows = ft.rerank_eval(CrossEncoderReranker(str(out), gate=False), cases)
    assert set(current) == set(candidate) == set(ft.METRICS) and len(rows) == 2
    decision = ft.write_promotion(out, base_model=str(tiny_model), dataset="tiny", pairs=len(pairs),
                                  cases=len(cases), current=current, candidate=candidate)
    assert decision["beats_current"] == ft.beats(current, candidate)[0]
    if decision["beats_current"]:
        check_promoted(str(out))
    else:
        with pytest.raises(RerankerNotPromoted):
            CrossEncoderReranker(str(out))


def test_the_gate_needs_a_higher_ndcg_and_no_metric_lower(tmp_path) -> None:
    base = {"recall@5": 0.8, "mrr": 0.6, "ndcg@10": 0.7}
    assert ft.beats(base, {"recall@5": 0.8, "mrr": 0.65, "ndcg@10": 0.72}) == (True, "higher nDCG@10 and no metric lower")
    assert ft.beats(base, dict(base))[0] is False                                   # equal is not better
    assert ft.beats(base, {"recall@5": 0.7, "mrr": 0.9, "ndcg@10": 0.9})[0] is False  # recall fell
    folder = tmp_path / "m"
    folder.mkdir()
    with pytest.raises(RerankerNotPromoted, match="promotion.json"):
        check_promoted(str(folder))  # never evaluated
    ft.write_promotion(folder, base_model="b", dataset="d", pairs=1, cases=1, current=base, candidate=base)
    with pytest.raises(RerankerNotPromoted, match="not higher"):
        check_promoted(str(folder))
    check_promoted("BAAI/bge-reranker-base")  # a hub model name is not a local folder: no gate
