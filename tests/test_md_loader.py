"""Tests for app/ingest/md_loader.py."""

from pathlib import Path

from app.ingest.md_loader import SYNTHETIC_BANNER, load_markdown, split_sections

SAMPLE = f"""{SYNTHETIC_BANNER}

# Policy Title

Intro text.

## 1. First
<!-- source: ICAI ch6 §1.9 (p.9) -->
Body one.

### 1.1 Sub
Body sub.

## 2. Second
Body two.
"""


def test_banner_detected_and_comments_collected(tmp_path: Path) -> None:
    f = tmp_path / "doc.md"
    f.write_text(SAMPLE, encoding="utf-8")
    doc = load_markdown(f)
    assert doc.has_banner
    assert doc.source_notes == ["ICAI ch6 §1.9 (p.9)"]
    assert "<!--" not in doc.text


def test_missing_banner_detected(tmp_path: Path) -> None:
    f = tmp_path / "doc.md"
    f.write_text("# No banner\ntext\n", encoding="utf-8")
    assert not load_markdown(f).has_banner


def test_sections_and_heading_paths() -> None:
    sections = split_sections(SAMPLE.split("\n", 2)[2])
    paths = [s.heading_path for s in sections]
    assert paths == [
        "Policy Title",
        "Policy Title > 1. First",
        "Policy Title > 1. First > 1.1 Sub",
        "Policy Title > 2. Second",
    ]
    assert sections[3].text == "Body two."
    assert sections[2].level == 3
