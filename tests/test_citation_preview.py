"""M16b: each citation carries its cited passage, and the preview always contains it, so
the UI can highlight the supporting text (CLAUDE.md section 11)."""

from app.api.main import PREVIEW_CHARS, cited_text, preview


def _chunk(words: int, passage: str, at_word: int) -> str:
    body = [f"w{i}" for i in range(words)]
    body.insert(at_word, passage)
    return "Heading > Path\n" + " ".join(body)


def test_cited_text_is_the_span_with_spaces_normalised() -> None:
    text = "Heading\nThe final audit file is assembled\n   within 60 days. Other."
    start = text.index("The final")
    end = text.index("days.") + len("days.")
    assert cited_text(text, start, end) == "The final audit file is assembled within 60 days."
    assert cited_text(text, None, None) is None
    assert cited_text(text, 5, 5) is None and cited_text(text, 0, len(text) + 1) is None


def test_a_passage_late_in_a_long_chunk_is_inside_the_preview() -> None:
    passage = "Assembly is completed within sixty days of the report date."
    text = _chunk(600, passage, 450)  # far beyond the first PREVIEW_CHARS characters
    shown = preview(text, passage)
    assert passage in shown and shown.startswith("… ") and shown.endswith(" …")
    assert len(shown) <= PREVIEW_CHARS + len(passage) + 10
    assert not shown.startswith("… w") or shown.split()[1].startswith("w")  # starts at a word


def test_an_early_or_missing_passage_keeps_the_ordinary_preview() -> None:
    passage = "Assembly is completed within sixty days."
    text = _chunk(600, passage, 3)
    assert preview(text, passage) == preview(text) and passage in preview(text)
    assert preview(text, "not in the chunk") == preview(text)
    short = "Heading\nShort body."
    assert preview(short, "Short body.") == "Short body."
