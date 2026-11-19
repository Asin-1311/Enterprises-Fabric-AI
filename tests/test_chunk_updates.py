"""Chunk-level incremental updates and chunking profiles (M12b; CLAUDE.md section 6).

Stable chunk ids plus a content hash per chunk let the indexer recompute only what a
change affects: an edit to one section re-embeds that section's chunks and nothing else,
in SQLite, Qdrant and the keyword index alike. Real SQLite, in-memory Qdrant and BM25;
only the embedding model is a deterministic fake that records what it embedded.
"""

from collections.abc import Sequence
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db.models import Chunk
from app.ingest import chunking_profiles
from app.ingest.indexer import Indexer
from tests.helpers import HashingEmbedder, make_indexer, write_doc, write_manifest, write_people


class RecordingEmbedder(HashingEmbedder):
    """Remembers every text it was asked to embed."""

    def __init__(self) -> None:
        super().__init__()
        self.texts: list[str] = []

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        self.texts.extend(texts)
        return super().embed_documents(texts)


def _sections(n: int, changed: dict[int, str] | None = None, skip: set[int] = frozenset()) -> str:
    changed = changed or {}
    parts = ["# Long Policy"]
    for i in range(1, n + 1):
        if i not in skip:
            body = changed.get(i, f"Rule {i} keeps word{i}a and word{i}b in the file.")
            parts.append(f"## {i}. Topic {i}\n{body}")
    return "\n\n".join(parts)


LONG = {"doc_id": "POL-L", "path": "data/corpus/firm_policy/long.md", "source_type": "firm_policy",
        "title": "Long Policy", "version": "1.0", "effective_date": "2025-04-01",
        "access_level": "staff", "synthetic": "true"}
FAQ = {"doc_id": "FAQ-1", "path": "data/corpus/faq/faq.md", "source_type": "faq",
       "title": "Helpdesk FAQ", "version": "1.0", "effective_date": "2025-04-01",
       "access_level": "staff", "synthetic": "true"}
FAQ_TEXT = ("# Helpdesk FAQ\n\n## Files\n\n### Q: When is the file assembled?\nA: Within 45 days.\n\n"
            "### Q: How long is it kept?\nA: Seven years.")


@pytest.fixture
def env(tmp_path: Path):
    (tmp_path / "data").mkdir()
    write_people(tmp_path, "user_id,name,role,synthetic\nP1,Test Partner,partner,true\n",
                 "engagement_id,client_name,industry,fy,partner_id,member_ids\n")
    write_doc(tmp_path, "firm_policy/long.md", _sections(20))
    write_doc(tmp_path, "faq/faq.md", FAQ_TEXT)
    write_manifest(tmp_path, [LONG, FAQ])
    embedder = RecordingEmbedder()
    indexer = make_indexer(tmp_path, embedder)
    indexer.sync(actor="test")
    return tmp_path, indexer, embedder


def _rows(indexer: Indexer, doc_id: str) -> dict[str, Chunk]:
    with indexer.sessions() as s:
        return {c.chunk_id: c for c in s.scalars(select(Chunk).where(Chunk.doc_id == doc_id))}


def _in_step(indexer: Indexer, doc_id: str) -> set[str]:
    """SQLite, Qdrant and BM25 hold exactly the same chunk ids for the document."""
    ids = set(_rows(indexer, doc_id))
    assert indexer.store.chunk_ids(doc_id) == ids
    assert {c for c in indexer.bm25.chunk_ids if c.startswith(f"{doc_id}:")} == ids
    return ids


def test_one_section_edit_re_embeds_only_that_sections_chunks(env) -> None:
    """Covers: R-7.1-b"""
    root, indexer, embedder = env
    before_ids = _in_step(indexer, "POL-L")
    assert len(before_ids) == 20  # one chunk per section
    untouched = sorted(before_ids - {"POL-L:7:1"})
    vectors_before = indexer.store.vectors(untouched)

    write_doc(root, "firm_policy/long.md",
              _sections(20, changed={7: "Rule 7 now says quokkaword instead."}))
    embedder.texts.clear()
    report = indexer.sync(actor="test")

    assert report.indexed == ["POL-L"] and report.unchanged == ["FAQ-1"]
    assert len(embedder.texts) == 1 and "quokkaword" in embedder.texts[0]  # only section 7
    assert report.chunks == {"kept": 19, "embedded": 1}
    assert _in_step(indexer, "POL-L") == before_ids  # no id changed
    assert indexer.store.vectors(untouched) == vectors_before  # the other 19 untouched
    assert [c for c, _ in indexer.bm25.search("quokkaword")] == ["POL-L:7:1"]
    assert indexer.bm25.search("word7a") == []  # the old text is gone from BM25 too
    assert "quokkaword" in _rows(indexer, "POL-L")["POL-L:7:1"].text


def test_removed_section_is_deleted_from_every_index_and_others_kept(env) -> None:
    """Covers: R-7.1-b"""
    root, indexer, embedder = env
    write_doc(root, "firm_policy/long.md", _sections(20, skip={5}))
    embedder.texts.clear()
    report = indexer.sync(actor="test")

    assert embedder.texts == []
    assert report.chunks == {"kept": 19, "deleted": 1}
    ids = _in_step(indexer, "POL-L")
    assert "POL-L:5:1" not in ids and len(ids) == 19
    assert indexer.store.count("POL-L") == 19


def test_inserted_section_does_not_renumber_the_others(env) -> None:
    root, indexer, embedder = env
    body = _sections(20).replace("## 4. Topic 4", "## 3.5. Inserted topic\nNew wombatword.\n\n## 4. Topic 4")
    write_doc(root, "firm_policy/long.md", body)
    embedder.texts.clear()
    report = indexer.sync(actor="test")

    assert report.chunks == {"kept": 20, "embedded": 1}
    assert len(embedder.texts) == 1 and "wombatword" in embedder.texts[0]
    assert "POL-L:3.5:1" in _in_step(indexer, "POL-L")


def test_every_chunk_records_its_profile_hash_and_embedding_model(env) -> None:
    """Covers: R-7.2"""
    _, indexer, _ = env
    for doc_id, ref in (("POL-L", "firm_policy@1:400/50"), ("FAQ-1", "faq@1:400/50")):
        rows = _rows(indexer, doc_id).values()
        assert rows and {c.chunking_profile for c in rows} == {ref}
        assert all(c.content_hash and len(c.content_hash) == 64 for c in rows)
        assert {c.embedding_ref for c in rows} == {indexer.embedding_ref}
    faq_ids = set(_rows(indexer, "FAQ-1"))
    assert any(cid.startswith("FAQ-1:q-") for cid in faq_ids)


def test_changing_a_profile_rechunks_only_its_source_type(env, monkeypatch) -> None:
    """Covers: R-7.2 (a profile change is detected per source type; text is unchanged,
    so nothing needs embedding again)."""
    _, indexer, embedder = env
    monkeypatch.setitem(chunking_profiles._RULES, "faq", (2, 3))
    embedder.texts.clear()
    report = indexer.sync(actor="test")

    assert report.indexed == ["FAQ-1"] and report.unchanged == ["POL-L"]
    assert embedder.texts == []  # same chunk texts: vectors kept
    assert {c.chunking_profile for c in _rows(indexer, "FAQ-1").values()} == {"faq@2:400/50"}
    assert {c.chunking_profile for c in _rows(indexer, "POL-L").values()} == {"firm_policy@1:400/50"}


def test_a_new_embedding_model_re_embeds_everything(env) -> None:
    _, indexer, embedder = env
    indexer.embedding_ref = "another-model"
    embedder.texts.clear()
    report = indexer.sync(actor="test")
    total = len(_rows(indexer, "POL-L")) + len(_rows(indexer, "FAQ-1"))
    assert report.chunks == {"embedded": total} and len(embedder.texts) == total
    assert {c.embedding_ref for c in _rows(indexer, "POL-L").values()} == {"another-model"}


def test_forced_reindex_re_embeds_every_chunk_of_that_document_only(env) -> None:
    _, indexer, embedder = env
    embedder.texts.clear()
    report = indexer.reindex_document("POL-L", actor="test")
    assert report.chunks == {"embedded": 20} and len(embedder.texts) == 20
    assert _in_step(indexer, "POL-L") and len(_rows(indexer, "FAQ-1")) == 3  # group + 2 questions


def _make_phase1_index(indexer: Indexer, doc_id: str) -> dict[str, str]:
    """Rewrite one document's chunks as Phase 1 left them: position ids, no hash, no
    profile, no embedding_ref. Returns {old id: text}."""
    rows = sorted(_rows(indexer, doc_id).values(), key=lambda c: c.chunk_id)
    order = {c.chunk_id: n for n, c in enumerate(rows, start=1)}
    vectors = indexer.store.vectors(list(order))
    old: dict[str, str] = {}
    with indexer.sessions() as s:
        for c in rows:
            s.delete(s.get(Chunk, c.chunk_id))
        s.flush()
        for c in rows:
            oid = f"{doc_id}:{order[c.chunk_id]:04d}"
            old[oid] = c.text
            s.add(Chunk(chunk_id=oid, doc_id=doc_id, parent_id=None, text=c.text,
                        token_count=c.token_count, section=c.section))
        s.commit()
    indexer.store.delete_document(doc_id)
    payload = {"doc_id": doc_id, "source_type": "firm_policy", "access_level": "staff",
               "engagement_id": "", "superseded": False}
    indexer.store.upsert([payload | {"chunk_id": f"{doc_id}:{order[c]:04d}"} for c in order],
                         [vectors[c] for c in order])
    indexer.rebuild_bm25()
    return old


def test_phase1_ids_are_converted_reusing_vectors_and_kept_as_legacy_ids(env) -> None:
    """The one-time conversion (scripts/migrate_chunk_ids.py runs a sync): no re-embedding,
    and every chunk remembers the Phase 1 id that had the same text."""
    _, indexer, embedder = env
    old = _make_phase1_index(indexer, "POL-L")
    embedder.texts.clear()
    report = indexer.sync(actor="test")

    assert embedder.texts == []
    assert report.chunks == {"reused": 20, "deleted": 20}
    rows = _rows(indexer, "POL-L")
    assert all(":" in cid.split(":", 1)[1] for cid in rows)  # stable ids now
    assert {c.legacy_chunk_id: c.text for c in rows.values()} == old
    assert _in_step(indexer, "POL-L") == set(rows)  # old points gone from Qdrant and BM25


def test_changed_text_drops_the_legacy_id(env) -> None:
    """An old citation must never silently point at different text."""
    root, indexer, _ = env
    _make_phase1_index(indexer, "POL-L")
    indexer.sync(actor="test")
    assert _rows(indexer, "POL-L")["POL-L:7:1"].legacy_chunk_id is not None
    write_doc(root, "firm_policy/long.md", _sections(20, changed={7: "Rule 7 is new."}))
    indexer.sync(actor="test")
    rows = _rows(indexer, "POL-L")
    assert rows["POL-L:7:1"].legacy_chunk_id is None
    assert rows["POL-L:8:1"].legacy_chunk_id is not None  # unchanged chunks keep theirs
