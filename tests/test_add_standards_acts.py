"""Tests for scripts/add_standards_acts.py and the generic-cleaner additions it relies on."""

from pathlib import Path

from app.ingest.cleaner import clean_page, detect_running_headers, is_contents_page
from scripts.add_standards_acts import parse_date, read_readme, standard_row

README = """ICAI Accounting Standards as on 1 February 2022
Source page: https://www.icai.org/post/accounting-standards-as-on-1stfeb2022

Total files downloaded: 2

01. Appendix 1
    File: 01_Appendix_1.pdf
    URL: https://example/appendix.pdf

06. AS 5 Net Profit or Loss for the Period, Prior Period Items and Changes in Accounting Policies
    File: 06_AS_5_Net_Profit_or_Loss_for_the_Period_Prior_Period_Items.pdf
    URL: https://example/as5.pdf
"""


def test_parse_date_forms() -> None:
    assert parse_date("29th August, 2013") == "2013-08-29"
    assert parse_date("1 February 2022") == "2022-02-01"


def test_readme_gives_date_and_official_titles(tmp_path: Path) -> None:
    (tmp_path / "README.txt").write_text(README, encoding="utf-8")
    date, titles = read_readme(tmp_path / "README.txt")
    assert date == "2022-02-01"
    assert titles["06_AS_5_Net_Profit_or_Loss_for_the_Period_Prior_Period_Items.pdf"].startswith(
        "AS 5 Net Profit or Loss for the Period, Prior Period Items")


def test_standard_row_ids_and_titles(tmp_path: Path, monkeypatch) -> None:
    import scripts.add_standards_acts as mod

    monkeypatch.setattr(mod, "PROJECT_ROOT", tmp_path)
    pdf = Path("06_AS_5_Net_Profit.pdf")
    row = standard_row(pdf, tmp_path / "x" / pdf.name, "2022-02-01",
                       "AS 5 Net Profit or Loss for the Period")
    assert row["doc_id"] == "ASC-AS05" and row["effective_date"] == "2022-02-01"
    assert row["title"] == "ICAI Accounting Standards (as on 1 February 2022), AS 5: Net Profit or Loss for the Period"
    app = standard_row(Path("01_Appendix_1.pdf"), tmp_path / "x" / "a.pdf", "2022-02-01", "Appendix 1")
    assert app["doc_id"] == "ASC-APP1"


def test_title_case_headers_found_only_in_relaxed_mode() -> None:
    pages = [f"{376 + i}\nAS 22 (issued 2001)\nbody {i}" if i % 2 == 0 else
             f"Accounting for Taxes on Income\n{376 + i}\nbody {i}" for i in range(10)]
    assert detect_running_headers(pages) == set()
    relaxed = detect_running_headers(pages, min_share=0.3, require_capitals=False)
    assert relaxed == {"AS 22 (ISSUED 2001)", "ACCOUNTING FOR TAXES ON INCOME"}
    assert clean_page(pages[0], None, 1, header_lines=relaxed) == "body 0"  # header + '376' gone
    assert clean_page(pages[1], None, 2, header_lines=relaxed) == "body 1"


def test_contents_pages_are_dropped_in_generic_mode_only() -> None:
    toc = "2\nSECTIONS\n36. Punishment for fraudulently inducing persons to invest money."
    first = "1\nTHE COMPANIES ACT, 2013\n____\nARRANGEMENT OF SECTIONS\nCHAPTER I"
    body = "14\nTHE COMPANIES ACT, 2013\nACT NO. 18 OF 2013\n1. Short title, extent."
    assert is_contents_page(toc) and is_contents_page(first) and not is_contents_page(body)
    assert clean_page(toc, None, 2, header_lines=set()) == ""
    assert clean_page(body, None, 14, header_lines=set()).startswith("THE COMPANIES ACT, 2013")
