"""Load ICAI chapter PDFs page by page with pymupdf, keeping page numbers."""

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from app.ingest.cleaner import CHAPTER_TITLES, clean_page, detect_running_headers


@dataclass
class PdfPage:
    """One PDF page. `page_number` is 1-based, as shown by a PDF viewer."""

    page_number: int
    text: str


@dataclass
class ChapterDocument:
    """A cleaned ICAI chapter."""

    path: Path
    chapter: int | None
    title: str
    pages: list[PdfPage] = field(default_factory=list)


def chapter_number_from_path(path: Path) -> int:
    """Read the chapter number from a file name such as `ch04_audit_evidence.pdf`."""
    match = re.search(r"ch0?(\d{1,2})", path.stem, flags=re.IGNORECASE)
    if not match:
        raise ValueError(f"Cannot find a chapter number in file name: {path.name}")
    return int(match.group(1))


def load_pdf_pages(path: Path) -> list[PdfPage]:
    """Return the raw text of every page of a PDF."""
    with pymupdf.open(path) as doc:
        return [PdfPage(page_number=i + 1, text=page.get_text()) for i, page in enumerate(doc)]


def count_pdf_pages(path: Path) -> int:
    """Number of pages in a PDF."""
    with pymupdf.open(path) as doc:
        return doc.page_count


def load_icai_chapter(path: Path) -> ChapterDocument:
    """Load and clean an ICAI chapter PDF. Page numbers are kept per page."""
    chapter = chapter_number_from_path(path)
    pages = [
        PdfPage(p.page_number, clean_page(p.text, chapter, p.page_number))
        for p in load_pdf_pages(path)
    ]
    return ChapterDocument(
        path=path,
        chapter=chapter,
        title=CHAPTER_TITLES.get(chapter, f"Chapter {chapter}").title(),
        pages=pages,
    )


_INTER_NAME = re.compile(r"^ch\d{2}_", re.IGNORECASE)  # ch04_audit_evidence.pdf
# 'Chapter_3' (CA Final) or '_ch10' / '_ch3u1' (Taxation study material file names)
_CHAPTER_IN_NAME = re.compile(r"(?:Chapter[_-]?|_ch)(\d{1,2})", re.IGNORECASE)
_PAGE_NUMBER = re.compile(r"(\d{1,2})\.\d{1,3}")


def _dominant_page_prefix(raw_pages: list[str]) -> int | None:
    """Most common chapter prefix of printed page numbers ('4.47' -> 4), if any."""
    prefixes: Counter[int] = Counter()
    for raw in raw_pages:
        for line in [ln.strip() for ln in raw.splitlines() if ln.strip()][:4]:
            m = _PAGE_NUMBER.fullmatch(line)
            if m:
                prefixes[int(m.group(1))] += 1
    return prefixes.most_common(1)[0][0] if prefixes else None


def load_icai_pdf(path: Path, title: str | None = None) -> ChapterDocument:
    """Load any ICAI study-material PDF.

    - Intermediate Auditing and Ethics chapters (`chNN_*.pdf`): the original cleaner.
    - Others (e.g. CA Final papers): running headers are detected per file; the chapter
      number comes from 'Chapter_N' in the file name, else from the printed page numbers.
    """
    if _INTER_NAME.match(path.name):
        return load_icai_chapter(path)
    raw = load_pdf_pages(path)
    headers = detect_running_headers([p.text for p in raw])
    if not headers:  # e.g. Accounting Standards: Title-Case headers ("AS 22 (issued 2001)")
        headers = detect_running_headers([p.text for p in raw], min_share=0.3,
                                         require_capitals=False)
    m = _CHAPTER_IN_NAME.search(path.stem)
    chapter = int(m.group(1)) if m else _dominant_page_prefix([p.text for p in raw])
    pages = [PdfPage(p.page_number, clean_page(p.text, chapter, p.page_number, header_lines=headers))
             for p in raw]
    return ChapterDocument(path=path, chapter=chapter, title=title or path.stem, pages=pages)
