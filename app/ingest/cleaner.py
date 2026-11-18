"""Clean page text extracted from the ICAI *Auditing and Ethics* chapter PDFs.

What gets removed (all observed in the actual PDFs during M1):
- the running header `AUDITING AND ETHICS` (even pages),
- the chapter title repeated at the top of odd pages (it may span two lines),
- the running page number such as `6.9` (chapter.page),
- the footer `© The Institute of Chartered Accountants of India`,
- the page-1 banner lines `CHAPTER` and the chapter number,
- the `CHAPTER OVERVIEW` label of the overview diagram.

What gets repaired:
- typographic ligatures (`ﬁ`, `ﬂ`, `ﬀ` ...) become plain letters,
- the ICAI rupee glyph, extracted as a backtick before an amount, becomes `₹`,
- a line ending in a hyphen is joined to the next line. The hyphen is KEPT,
  because in these PDFs almost every line-end hyphen is a real compound
  ("non-compliance", "co-operative"), not a word split for layout,
- decorative bullet glyphs (`♦`, `•`) are dropped, but the item they introduce
  is kept as a list item (prefixed with "- ").

Page numbers are not lost: the loader stores the PDF page number on each page,
and chunks later inherit page_start / page_end from it.
"""

import re

# Chapter titles as printed in the running headers (ICAI ch4 to ch11).
CHAPTER_TITLES: dict[int, str] = {
    4: "AUDIT EVIDENCE",
    5: "AUDIT OF ITEMS OF FINANCIAL STATEMENTS",
    6: "AUDIT DOCUMENTATION",
    7: "COMPLETION AND REVIEW",
    8: "AUDIT REPORT",
    9: "SPECIAL FEATURES OF AUDIT OF DIFFERENT TYPES OF ENTITIES",
    10: "AUDIT OF BANKS",
    11: "ETHICS AND TERMS OF AUDIT ENGAGEMENTS",
}

RUNNING_HEADER = "AUDITING AND ETHICS"
COPYRIGHT_MARK = "©"
BULLET_GLYPHS = ("♦", "•")
# The PDFs also use Wingdings bullets, which extract as Unicode private-use
# characters U+F000 to U+F0FF (e.g. U+F06C, U+F0FC, U+F0D8).
_PRIVATE_USE_BULLETS = (0xF000, 0xF0FF)
_TRAILING_GLYPHS = re.compile(r"\s*[-♦•]+\s*$")


def _is_bullet_glyph(ch: str) -> bool:
    lo, hi = _PRIVATE_USE_BULLETS
    return ch in BULLET_GLYPHS or (len(ch) == 1 and lo <= ord(ch) <= hi)

_LIGATURES = {"ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st"}
_RUPEE = re.compile(r"`\s?(?=\d)")
_SPACES = re.compile(r"[ \t ]+")
# How many lines at the top of a page may belong to the running header.
_HEADER_ZONE = 4


def normalize_text(text: str) -> str:
    """Replace ligatures and the rupee glyph; collapse runs of spaces."""
    for lig, plain in _LIGATURES.items():
        text = text.replace(lig, plain)
    text = _RUPEE.sub("₹", text)
    return _SPACES.sub(" ", text)


def _norm(line: str) -> str:
    return " ".join(line.upper().split())


def _is_page_number(line: str, chapter: int) -> bool:
    return re.fullmatch(rf"{chapter}\.\d{{1,3}}", line.strip()) is not None


def _is_title_fragment(line: str, chapter: int) -> bool:
    """True if `line` is an all-caps piece of this chapter's running title."""
    text = _norm(line)
    title = CHAPTER_TITLES.get(chapter, "")
    return bool(text) and line.strip().isupper() and text in title


def _strip_header(lines: list[str], chapter: int | None,
                  header_lines: set[str] | None = None) -> list[str]:
    """Drop running-header lines from the top of the page only.

    Without `header_lines` (the Intermediate chapters) the known ICAI header, chapter title
    and `chapter.page` number are removed. With `header_lines` (detected per PDF, see
    detect_running_headers) those lines and any `n.nn` page number are removed instead."""
    i = 0
    while i < min(len(lines), _HEADER_ZONE):
        line = lines[i]
        if header_lines is not None:
            is_header = (_norm(line) in header_lines
                         or _GENERIC_PAGE_NUMBER.fullmatch(line.strip())
                         or _BARE_PAGE_NUMBER.fullmatch(line.strip()))
        else:
            is_header = (_norm(line) == RUNNING_HEADER or _is_page_number(line, chapter)  # type: ignore[arg-type]
                         or _is_title_fragment(line, chapter))  # type: ignore[arg-type]
        if is_header:
            i += 1
            continue
        break
    return lines[i:]


_GENERIC_PAGE_NUMBER = re.compile(r"\d{1,2}\.\d{1,3}")
_BARE_PAGE_NUMBER = re.compile(r"\d{1,4}")  # compendium / statute page numbers: "376"


def detect_running_headers(raw_pages: list[str], min_share: float = 0.2,
                           min_pages: int = 3, require_capitals: bool = True) -> set[str]:
    """Lines that recur at the top of many pages of one PDF (paper name, chapter title).

    A line counts if it is in the first lines of at least `min_share` of the pages (and at
    least `min_pages` pages). By default it must also be written mostly in capitals, as ICAI
    study-material headers are. With `require_capitals=False` (used only when the strict
    pass finds nothing, e.g. the Accounting Standards, whose headers are "AS 22 (issued 2001)"
    and "Accounting for Taxes on Income") any recurring line with at least 3 letters counts.
    Used for PDFs whose headers are not hard-coded above."""

    def mostly_capitals(line: str) -> bool:  # judged on the ORIGINAL line, not the upper-cased key
        letters = [c for c in line if c.isalpha()]
        return len(letters) >= 3 and sum(c.isupper() for c in letters) / len(letters) >= 0.8

    def has_letters(line: str) -> bool:
        return sum(c.isalpha() for c in line) >= 3

    accept = mostly_capitals if require_capitals else has_letters
    counts: dict[str, int] = {}
    for raw in raw_pages:
        top = [ln.strip() for ln in normalize_text(raw).splitlines() if ln.strip()][:_HEADER_ZONE]
        for norm in {_norm(ln) for ln in top if accept(ln)}:
            counts[norm] = counts.get(norm, 0) + 1
    threshold = max(min_pages, min_share * len(raw_pages))
    return {norm for norm, n in counts.items() if n >= threshold}


# Pages of a statute's table of contents ("ARRANGEMENT OF SECTIONS", continued under a
# "SECTIONS" heading). In generic mode they are dropped: hundreds of section titles would
# otherwise crowd out real content in search results.
_TOC_MARKERS = {"ARRANGEMENT OF SECTIONS", "SECTIONS"}
_TOC_ZONE = 5


def is_contents_page(raw_text: str) -> bool:
    """True if the page is part of an 'Arrangement of Sections' (table of contents)."""
    top = [_norm(ln) for ln in normalize_text(raw_text).splitlines() if ln.strip()][:_TOC_ZONE]
    return any(t in _TOC_MARKERS for t in top)


def _strip_chapter_banner(lines: list[str], chapter: int) -> list[str]:
    """Page 1 starts with 'CHAPTER' then the bare chapter number."""
    if len(lines) >= 2 and _norm(lines[0]) == "CHAPTER" and lines[1].strip() == str(chapter):
        return lines[2:]
    return lines


def _apply_bullets(lines: list[str]) -> list[str]:
    """Turn a lone or leading bullet glyph into a '- ' list marker."""
    out: list[str] = []
    pending_bullet = False
    for line in lines:
        stripped = line.strip()
        if len(stripped) == 1 and _is_bullet_glyph(stripped):
            pending_bullet = True
            continue
        if stripped and _is_bullet_glyph(stripped[0]):
            stripped = stripped[1:].strip()
            pending_bullet = True
        # A stray glyph can also trail the previous item ("system.").
        stripped = _TRAILING_GLYPHS.sub("", stripped)
        out.append(f"- {stripped}" if pending_bullet else stripped)
        pending_bullet = False
    return out


def _join_hyphenated(lines: list[str]) -> list[str]:
    """Join 'non-' + 'compliance' into 'non-compliance' (hyphen kept)."""
    out: list[str] = []
    for line in lines:
        if (
            out
            and re.search(r"[A-Za-z]-$", out[-1])
            and line[:1].islower()
            and not out[-1].startswith("http")
        ):
            first, _, rest = line.partition(" ")
            out[-1] = out[-1] + first
            if rest:
                out.append(rest)
        else:
            out.append(line)
    return out


def clean_page(raw_text: str, chapter: int | None, page_number: int,
               header_lines: set[str] | None = None) -> str:
    """Clean one page of an ICAI PDF. Returns text with one line per line.

    `header_lines` switches to the generic mode used for PDFs other than the Intermediate
    Auditing and Ethics chapters (headers detected per file). In generic mode a
    table-of-contents page (see is_contents_page) returns an empty string."""
    if header_lines is not None and is_contents_page(raw_text):
        return ""
    text = normalize_text(raw_text)
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    lines = [ln for ln in lines if not ln.startswith(COPYRIGHT_MARK)]
    if page_number == 1 and chapter is not None and header_lines is None:
        lines = _strip_chapter_banner(lines, chapter)
    lines = _strip_header(lines, chapter, header_lines)
    lines = [ln for ln in lines if _norm(ln) != "CHAPTER OVERVIEW"]
    lines = _apply_bullets(lines)
    lines = _join_hyphenated(lines)
    return "\n".join(lines)
