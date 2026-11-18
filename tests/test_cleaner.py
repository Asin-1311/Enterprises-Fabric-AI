"""Tests for app/ingest/cleaner.py and the PDF loader.

The sample pages below copy the patterns observed in the real ICAI PDFs (M1).
"""

from pathlib import Path

import pytest

from app.config import PROJECT_ROOT
from app.ingest.cleaner import clean_page, normalize_text
from app.ingest.pdf_loader import chapter_number_from_path, load_icai_chapter

EVEN_PAGE = """AUDITING AND ETHICS
6.8
1.7 Timely Preparation of Audit Documentation
The auditor shall prepare audit documentation on a timely basis.
© The Institute of Chartered Accountants of India
"""

ODD_PAGE = """AUDIT DOCUMENTATION
6.9
1.9 Assembly of the Final Audit File
♦
An appropriate time limit is ordinarily not more than 60 days.
© The Institute of Chartered Accountants of India
"""

CH9_ODD_PAGE = """9.3
SPECIAL FEATURES OF AUDIT OF DIFFERENT
TYPES OF ENTITIES
Audit of Different Entities
"""

PAGE_ONE = """CHAPTER
4
LEARNING OUTCOMES
AUDIT EVIDENCE
After studying this chapter, you would be able to understand-
"""


def test_even_page_header_and_footer_removed() -> None:
    out = clean_page(EVEN_PAGE, chapter=6, page_number=8)
    assert "AUDITING AND ETHICS" not in out
    assert "6.8" not in out.splitlines()
    assert "©" not in out
    assert out.splitlines()[0] == "1.7 Timely Preparation of Audit Documentation"


def test_odd_page_title_header_removed() -> None:
    out = clean_page(ODD_PAGE, chapter=6, page_number=9)
    assert out.splitlines()[0] == "1.9 Assembly of the Final Audit File"
    assert "AUDIT DOCUMENTATION" not in out


def test_two_line_chapter_title_removed() -> None:
    out = clean_page(CH9_ODD_PAGE, chapter=9, page_number=3)
    assert out == "Audit of Different Entities"


def test_page_one_banner_removed_but_content_kept() -> None:
    out = clean_page(PAGE_ONE, chapter=4, page_number=1)
    lines = out.splitlines()
    assert lines[0] == "LEARNING OUTCOMES"
    assert "CHAPTER" not in lines and "4" not in lines


def test_title_words_below_header_zone_are_kept() -> None:
    raw = "AUDITING AND ETHICS\n4.2\nIntro line\nmore text\nAUDIT EVIDENCE\n"
    assert "AUDIT EVIDENCE" in clean_page(raw, chapter=4, page_number=2).splitlines()


def test_bullet_glyphs_become_list_items() -> None:
    raw = "Intro\n♦\nFirst item\n Second item\n• Third item\nlast.\n"
    lines = clean_page(raw, chapter=10, page_number=5).splitlines()
    assert lines == ["Intro", "- First item", "- Second item", "- Third item", "last."]


def test_hyphenated_line_break_joined_and_hyphen_kept() -> None:
    raw = "This is a case of non-\ncompliance with the SA.\n"
    assert clean_page(raw, chapter=4, page_number=5) == "This is a case of non-compliance\nwith the SA."


def test_ligatures_and_rupee_glyph() -> None:
    assert normalize_text("ﬁnancial ﬂow eﬀect ` 1,23,000") == "financial flow effect ₹1,23,000"


@pytest.mark.parametrize(
    ("name", "expected"),
    [("ch04_audit_evidence.pdf", 4), ("93341bos-aps5884-ch10.pdf", 10)],
)
def test_chapter_number_from_path(name: str, expected: int) -> None:
    assert chapter_number_from_path(Path(name)) == expected


CH6 = PROJECT_ROOT / "data" / "corpus" / "standards" / "ch06_audit_documentation.pdf"


@pytest.mark.skipif(not CH6.exists(), reason="ICAI PDFs are local only (not in git)")
def test_real_pdf_keeps_page_numbers_as_metadata() -> None:
    chapter = load_icai_chapter(CH6)
    assert chapter.chapter == 6
    assert [p.page_number for p in chapter.pages] == list(range(1, len(chapter.pages) + 1))
    page9 = chapter.pages[8]
    assert page9.page_number == 9
    assert page9.text.startswith("1.9 Assembly of the Final Audit File")
    assert "60 days" in page9.text
    for page in chapter.pages:
        assert "©" not in page.text
        assert "AUDITING AND ETHICS" not in page.text
