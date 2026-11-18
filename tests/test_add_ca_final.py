"""Tests for scripts/add_ca_final.py naming and skip rules, and the generic PDF cleaning."""

from pathlib import Path

import pytest

from app.ingest.cleaner import clean_page, detect_running_headers
from scripts.add_ca_final import classify, paper_number, title_for


@pytest.mark.parametrize(
    ("folder", "name", "doc_id", "title_end", "skip"),
    [
        ("Paper 3 - Advanced Auditing", "04_Chapter_3_Audit_Planning_Strategy_and_Execution",
         "CAF-P3-04", "Chapter 3: Audit Planning Strategy and Execution", ""),
        ("Paper 3 - Advanced Auditing", "23_Chapter_19_Professional_Ethics__Liabilities_of_Auditors",
         "CAF-P3-23", "Chapter 19: Professional Ethics & Liabilities of Auditors", ""),
        ("Paper 1 - Financial Reporting", "08_Unit_2_Ind_AS_10_Events_after_the_Reporting_Period",
         "CAF-P1-08", "Unit 2: Ind AS 10 Events after the Reporting Period", ""),
        ("Paper 5 - Indirect Tax Laws", "02_Chapter-1_Supply_under_GST",
         "CAF-P5-02", "Chapter 1: Supply under GST", ""),
        ("Paper 2 - AFM", "01_Initial_Pages", "CAF-P2-01", "Initial Pages", "front matter"),
        ("Paper 1 - Financial Reporting", "70_Crossword_Answer_Key", "CAF-P1-70",
         "Crossword Answer Key", "crossword"),
        ("Paper 6 - IBS", "01_Model_Test_Papers_Group_I", "CAF-P6-01",
         "Model Test Papers Group I", "model test papers"),
    ],
)
def test_ids_titles_and_skips(folder, name, doc_id, title_end, skip) -> None:
    f = classify(Path(f"{name}.pdf"), paper_number(folder))
    assert f.doc_id == doc_id
    assert title_for(f).startswith(f"ICAI CA Final Paper {f.paper} (")
    assert title_for(f).endswith(title_end)
    assert (skip in f.skip_reason) if skip else f.skip_reason == ""


def test_generic_header_detection_and_cleaning() -> None:
    pages = [f"ADVANCED AUDITING, ASSURANCE AND PROFESSIONAL ETHICS\n3.{i}\nBody text {i}.\n"
             f"© The Institute of Chartered Accountants of India" if i % 2 == 0 else
             f"AUDIT PLANNING, STRATEGY & EXECUTION\n3.{i}\nBody text {i}.\n"
             f"© The Institute of Chartered Accountants of India" for i in range(2, 22)]
    headers = detect_running_headers(pages)
    assert headers == {"ADVANCED AUDITING, ASSURANCE AND PROFESSIONAL ETHICS",
                       "AUDIT PLANNING, STRATEGY & EXECUTION"}
    assert clean_page(pages[0], 3, 3, header_lines=headers) == "Body text 2."
    assert clean_page(pages[1], 3, 4, header_lines=headers) == "Body text 3."


def test_body_lines_in_capitals_are_not_mistaken_for_headers() -> None:
    pages = [f"PAPER NAME\n{i}.1 SECTION {i}\nUNIQUE CAPITAL LINE {i}\ntext" for i in range(10)]
    assert detect_running_headers(pages) == {"PAPER NAME"}
