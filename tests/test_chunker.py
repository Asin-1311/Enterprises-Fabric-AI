"""Tests for app/ingest/chunker.py (CLAUDE.md section 13: chunker)."""

import pytest

from app.config import PROJECT_ROOT
from app.ingest.chunker import chunk_icai_chapter, chunk_markdown, extract_sa_refs
from app.ingest.md_loader import split_sections


def words(text: str) -> int:
    """Simple token counter for tests: one token per word."""
    return len(text.split())


# A miniature chapter that copies the real layout patterns.
PAGES = [
    (1, "LEARNING OUTCOMES\nUnderstand audit evidence."),
    (4, "1.\nAUDIT EVIDENCE\nIntro to audit evidence under SA 500.\n"
        "1.1 Introduction\nAudit evidence is information used by the auditor.\n"
        "1.2 Sufficient appropriate audit evidence\nSufficiency is the measure of quantity."),
    (5, "Appropriateness is the measure of quality.\n"
        "1.\nSales, transfers and charges between segments.\n"   # numbered list item, not a heading
        "ILLUSTRATION 7\nThe auditor of ABC Ltd sent a request.\nSOLUTION\nThis is a positive request.\n"
        "2.\nEXTERNAL CONFIRMATIONS (SA 505)\n"
        "2.1 Definition of External Confirmation\nA direct written response from a third party."),
    (6, "TEST YOUR KNOWLEDGE\nMCQs\n1. Which is correct?"),
]


@pytest.fixture
def chapter():
    return chunk_icai_chapter(doc_id="STD-CH04", doc_title="Audit Evidence", chapter=4,
                              pages=PAGES, count_tokens=words, max_tokens=60, overlap_tokens=8)


def test_parent_child_structure(chapter) -> None:
    by_section = {c.section: c for c in chapter.chunks}
    parent1, parent2 = by_section["1"], by_section["2"]
    assert parent1.parent_id is None and parent2.parent_id is None
    assert parent1.section_title == "AUDIT EVIDENCE"
    assert parent2.section_title == "EXTERNAL CONFIRMATIONS (SA 505)"
    assert by_section["1.1"].parent_id == parent1.chunk_id
    assert by_section["1.2"].parent_id == parent1.chunk_id
    assert by_section["2.1"].parent_id == parent2.chunk_id
    assert by_section["1.2"].section_title == "Sufficient appropriate audit evidence"


def test_numbered_list_item_is_not_a_heading(chapter) -> None:
    sections = {c.section for c in chapter.chunks}
    assert sections == {"0", "1", "1.1", "1.2", "2", "2.1"}


def test_test_your_knowledge_excluded_and_set_aside(chapter) -> None:
    assert all("TEST YOUR KNOWLEDGE" not in c.text for c in chapter.chunks)
    assert all("Which is correct" not in c.text for c in chapter.chunks)
    assert "[p.6] TEST YOUR KNOWLEDGE" in chapter.excluded_test_your_knowledge
    assert "Which is correct" in chapter.excluded_test_your_knowledge


def test_illustration_kept_whole(chapter) -> None:
    holders = [c for c in chapter.chunks if "ILLUSTRATION 7" in c.text]
    assert len(holders) == 1
    assert "This is a positive request." in holders[0].text


def test_metadata_pages_sa_refs_and_header(chapter) -> None:
    c12 = next(c for c in chapter.chunks if c.section == "1.2")
    assert (c12.page_start, c12.page_end) == (4, 5)
    assert c12.chapter == 4
    assert c12.text.startswith("Audit Evidence > 1 AUDIT EVIDENCE > 1.2 Sufficient")
    c21 = next(c for c in chapter.chunks if c.section == "2.1")
    assert c21.sa_refs == ["SA 505"]  # picked up from the heading path


def test_token_cap_and_overlap() -> None:
    body = "\n".join(f"Line {i} about inventory counting procedures here." for i in range(40))
    result = chunk_icai_chapter(doc_id="D", doc_title="T", chapter=4,
                                pages=[(3, f"1.\nAUDIT EVIDENCE\n1.1 Long section\n{body}")],
                                count_tokens=words, max_tokens=60, overlap_tokens=8)
    kids = [c for c in result.chunks if c.section == "1.1"]
    assert len(kids) > 3
    assert all(c.token_count <= 60 for c in result.chunks)
    for a, b in zip(kids, kids[1:]):  # last body line of a window is repeated in the next
        assert a.text.splitlines()[-1] in b.text


def test_extract_sa_refs() -> None:
    text = "SA 500, SA500 and SA-230; also SA 705 (Revised). Not ISA."
    assert extract_sa_refs(text) == ["SA 230", "SA 500", "SA 705"]


def test_chunk_ids_are_stable_section_ids(chapter) -> None:
    """M12b: ids are <doc>:<section>:<window>, unique within the document."""
    ids = [c.chunk_id for c in chapter.chunks]
    assert len(set(ids)) == len(ids)
    assert ids[:4] == ["STD-CH04:0:1", "STD-CH04:1:1", "STD-CH04:1.1:1", "STD-CH04:1.2:1"]


def test_editing_one_section_changes_only_that_sections_chunks() -> None:
    """Covers: R-7.1-b (the chunker half: ids and hashes elsewhere stay the same)."""
    def run(text_11: str):
        pages = [(4, "1.\nAUDIT EVIDENCE\nIntro.\n1.1 Introduction\n" + text_11 +
                  "\n1.2 Sufficiency\nSufficiency is the measure of quantity.")]
        r = chunk_icai_chapter(doc_id="D", doc_title="T", chapter=4, pages=pages,
                               count_tokens=words, max_tokens=60, overlap_tokens=8)
        return {c.chunk_id: c.content_hash for c in r.chunks}

    before = run("Audit evidence is information used by the auditor.")
    long_text = "\n".join(f"Inserted line {i} about evidence." for i in range(30))
    after = run(long_text)
    assert set(after) > set(before)  # 1.1 grew into several windows; nothing renumbered
    changed = {cid for cid in after if before.get(cid) != after[cid]}
    assert changed and all(cid.startswith("D:1.1:") for cid in changed)


def test_unnumbered_and_repeated_sections_get_stable_keys() -> None:
    r = chunk_markdown(doc_id="FAQ-01", doc_title="FAQ", source_type="faq",
                       sections=split_sections(FAQ_MD + "\n### Q: How long are files kept?\n"
                                               "A: See policy.\n"),
                       count_tokens=words, max_tokens=400, overlap_tokens=50)
    ids = [c.chunk_id for c in r.chunks]
    assert len(set(ids)) == len(ids)
    lead = ids[0]
    assert lead.startswith("FAQ-01:h-") and lead.endswith(":1")
    questions = [c.chunk_id for c in r.chunks if c.section == "Q"]
    assert all(q.startswith("FAQ-01:q-") for q in questions)
    # The same question twice: the second copy gets "-2" ("FAQ-01:q-abc123-2:1").
    assert questions[2] == questions[1].removesuffix(":1") + "-2:1"


def test_every_chunk_records_its_chunking_profile() -> None:
    """Covers: R-7.2"""
    faq = chunk_markdown(doc_id="FAQ-01", doc_title="FAQ", source_type="faq",
                         sections=split_sections(FAQ_MD), count_tokens=words)
    chk = chunk_markdown(doc_id="CHK", doc_title="Checklist", source_type="checklists",
                         sections=split_sections(CHECKLIST_MD), count_tokens=words)
    std = chunk_icai_chapter(doc_id="STD-CH04", doc_title="Audit Evidence", chapter=4,
                             pages=PAGES, count_tokens=words)
    assert {c.chunking_profile for c in faq.chunks} == {"faq@1:400/50"}
    assert {c.chunking_profile for c in chk.chunks} == {"checklists@1:400/50"}
    assert {c.chunking_profile for c in std.chunks} == {"standards@1:400/50"}


def test_profiles_exist_for_every_source_type() -> None:
    """Covers: R-7.2"""
    from app.ingest.chunking_profiles import all_profiles
    from app.ingest.manifest import SOURCE_TYPES

    profiles = all_profiles(300, 30)
    assert set(profiles) == set(SOURCE_TYPES)
    assert profiles["faq"].whole_level == 3 and profiles["checklists"].whole_level == 2
    assert profiles["templates"].ref == "templates@1:300/30"


# ------------------------------------------------------------------- Markdown

FAQ_MD = """# Helpdesk FAQ

Version 4.0.

## Documentation

### Q: Within how many days must the file be assembled?
A: Within 45 days under firm policy.
SA 230 sets an outer limit of 60 days.

### Q: How long are files kept?
A: Seven years.
"""

CHECKLIST_MD = """# Checklist

## B. NPA classification checks
- [ ] Term loans overdue more than 90 days are NPA.
- [ ] Cash credit accounts out of order are NPA.
- [ ] Classification is borrower-wise.

## C. Provisioning
| Category | Provision |
|---|---|
| Substandard | 15% |
"""


def test_faq_question_and_answer_kept_whole() -> None:
    r = chunk_markdown(doc_id="FAQ-01", doc_title="FAQ", source_type="faq",
                       sections=split_sections(FAQ_MD), count_tokens=words,
                       max_tokens=400, overlap_tokens=50)
    q = next(c for c in r.chunks if "Within how many days" in c.section_title)
    assert "Within 45 days" in q.text and "outer limit of 60 days" in q.text
    group = next(c for c in r.chunks if c.section_title == "Documentation")
    assert q.parent_id == group.chunk_id
    assert q.page_start is None  # Markdown has no pages


def test_checklist_group_and_table_kept_whole() -> None:
    r = chunk_markdown(doc_id="CHK", doc_title="Checklist", source_type="checklists",
                       sections=split_sections(CHECKLIST_MD), count_tokens=words,
                       max_tokens=400, overlap_tokens=50)
    group = next(c for c in r.chunks if c.section == "B")
    assert "90 days" in group.text and "borrower-wise" in group.text
    table = next(c for c in r.chunks if c.section == "C")
    assert "| Substandard | 15% |" in table.text


# --------------------------------------------------------------- real chapter

CH6 = PROJECT_ROOT / "data" / "corpus" / "standards" / "ch06_audit_documentation.pdf"


@pytest.mark.skipif(not CH6.exists(), reason="ICAI PDFs are local only (not in git)")
def test_real_chapter_6_structure() -> None:
    from app.ingest.pdf_loader import load_icai_chapter

    ch = load_icai_chapter(CH6)
    r = chunk_icai_chapter(doc_id="STD-CH06", doc_title="Audit Documentation", chapter=6,
                           pages=[(p.page_number, p.text) for p in ch.pages],
                           count_tokens=words, max_tokens=400, overlap_tokens=50)
    assembly = [c for c in r.chunks if c.section == "1.9"]
    assert assembly and assembly[0].page_start == 9
    assert any("60 days" in c.text for c in assembly)
    parent = next(c for c in r.chunks if c.chunk_id == assembly[0].parent_id)
    assert parent.section == "1" and parent.parent_id is None
    assert all("TEST YOUR KNOWLEDGE" not in c.text for c in r.chunks)
    assert r.excluded_test_your_knowledge.startswith("[p.17]")
