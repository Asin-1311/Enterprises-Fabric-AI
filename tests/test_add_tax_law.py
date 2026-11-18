"""Tests for scripts/add_tax_law.py (file-name parsing, ids, titles)."""

from pathlib import Path

import pytest

from scripts.add_tax_law import chapter_title, parse_name, smart_title, title_for


@pytest.mark.parametrize(
    ("name", "series", "chapter", "unit", "doc_id"),
    [("GST_ch10", "GST", 10, None, "TAX-GST-10"),
     ("Incometax_Actbook_ch1", "IT", 1, None, "TAX-IT-01"),
     ("Incometax_ch3u4", "IT", 3, 4, "TAX-IT-03-U4"),
     ("Incometax_ch9", "IT", 9, None, "TAX-IT-09")],
)
def test_parse_name(name, series, chapter, unit, doc_id) -> None:
    f = parse_name(Path(f"{name}.pdf"))
    assert (f.series, f.chapter, f.unit, f.doc_id) == (series, chapter, unit, doc_id)


def test_unrecognised_name() -> None:
    assert parse_name(Path("notes.pdf")) is None


def test_smart_title_keeps_acronyms_and_small_words() -> None:
    assert smart_title("TAX INVOICE, CREDIT AND DEBIT NOTES") == "Tax Invoice, Credit and Debit Notes"
    assert smart_title("TDS, TCS AND ADVANCE TAX") == "TDS, TCS and Advance Tax"
    assert smart_title("EXEMPTIONS FROM GST") == "Exemptions from GST"
    assert smart_title("E-WAY BILL") == "E-Way Bill"
    assert smart_title("SUPPLY UNDER GST") == "Supply under GST"


def test_two_line_title_kept_in_printed_order() -> None:
    page = ("GOODS AND SERVICES TAX\n14.{n}\nbody {n}" , "TAX DEDUCTION AT SOURCE AND COLLECTION\n"
            "OF TAX AT SOURCE\n14.{n}\nbody {n}")
    pages = [page[i % 2].format(n=i) for i in range(12)]
    title = chapter_title(pages, {"GOODS AND SERVICES TAX"})
    assert title == "Tax Deduction at Source and Collection of Tax at Source"


def test_title_for_unit() -> None:
    f = parse_name(Path("Incometax_ch3u1.pdf"))
    assert title_for(f, "Salaries") == "ICAI Taxation study material, Income Tax Law, Chapter 3, Unit 1: Salaries"
