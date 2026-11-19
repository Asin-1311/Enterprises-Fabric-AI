"""Structure-aware chunking (CLAUDE.md section 6).

Standards (ICAI PDFs)
- Top-level numbered sections ("2." + an ALL-CAPS title, or "1. SUBSEQUENT EVENTS")
  become PARENT chunks. Their numbered sub-sections ("1.5", "6.9.3") become CHILD chunks
  whose parent_id points to the parent. Section numbers must increase, which filters out
  numbered list items that happen to look like headings.
- Text before the first heading becomes section "0" (chapter introduction).
- Everything from the "TEST YOUR KNOWLEDGE" line onwards is NOT indexed; it is returned
  separately as candidate evaluation material.
- ILLUSTRATION / Example / Test Your Understanding blocks are kept in one chunk when they fit.

Synthetic Markdown
- `##` sections are parents and `###` sections are children.
- Markdown tables are kept whole; each checklist `##` group and each FAQ `###` Q&A is
  kept whole.

All documents
- Windows are capped at `max_tokens` (about 400) with about `overlap_tokens` (50) of
  overlap between consecutive windows of the same section.
- Each chunk's text starts with a one-line heading path (document > section > sub-section)
  so both BM25 and the embedding model see the context.
- `sa_refs` lists every "SA nnn" mentioned in the chunk text.

Chunk ids (M12b) are stable: `<doc_id>:<section key>:<window>`, e.g. "STD-CH06:1.1:1".
- The section key is the section number ("1.1", "2", "0" for a chapter introduction).
  A section without a number (a document lead, an FAQ question "Q:") gets "h-" or "q-"
  plus six hex digits of its heading, e.g. "FAQ-01:q-3f9a2c:1".
- A key that repeats within one document gets "-2", "-3", ... in order of appearance.
- Windows are numbered from 1 within their section.
So editing or inserting one section leaves the ids of every other section unchanged, and
the indexer can re-embed only the chunks whose text changed (CLAUDE.md section 6).
Each chunk also carries a SHA-256 of its text and the chunking profile that made it
(app/ingest/chunking_profiles.py).
"""

import bisect
import hashlib
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from app.ingest.chunking_profiles import ChunkingProfile, profile_for
from app.ingest.md_loader import MdSection

TokenCounter = Callable[[str], int]

TYK_MARKER = "TEST YOUR KNOWLEDGE"
_SA_REF = re.compile(r"\bSA\s?-?(\d{3})\b")
_TOP_NUMBER = re.compile(r"^(\d{1,2})\.?\s*(.*)$")
_SUB_HEADING = re.compile(r"^(\d{1,2})\.(\d{1,2})(?:\.(\d{1,2}))?\.?\s+([A-Z(].{1,110})$")
_BLOCK_START = re.compile(r"^(ILLUSTRATION\s*\d*|Examples?\b|Test Your Understanding\s*\d*|CASE SCENARIO)",
                          re.IGNORECASE)
_MD_NUMBER = re.compile(r"^((?:\d+(?:\.\d+)*)|[A-Z])[.)]?\s+(.*)$")
# Page-1 banners that look like numbered ALL-CAPS headings but are not sections.
_BANNER_TITLES = ("LEARNING OUTCOMES", "CHAPTER OVERVIEW", "UNIT OVERVIEW")
# Rough words-per-token ratio used only to size the overlap tail of an over-long line.
_WORDS_PER_TOKEN = 0.75


@dataclass(frozen=True)
class Line:
    """One line of text and the PDF page it came from (None for Markdown)."""

    text: str
    page: int | None = None


@dataclass
class Chunk:
    """A retrievable chunk with its metadata."""

    chunk_id: str
    doc_id: str
    parent_id: str | None
    text: str
    token_count: int
    section: str
    section_title: str
    chapter: int | None
    page_start: int | None
    page_end: int | None
    sa_refs: list[str] = field(default_factory=list)
    content_hash: str = ""       # SHA-256 of `text`: an unchanged hash needs no re-embedding
    chunking_profile: str = ""   # e.g. "faq@1:400/50"


def text_hash(text: str) -> str:
    """SHA-256 of a chunk's text (what the embedding is computed from)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class ChunkingResult:
    """Chunks of one document, plus excluded end-of-chapter questions (PDF only)."""

    chunks: list[Chunk]
    excluded_test_your_knowledge: str = ""


@dataclass
class _Section:
    number: str
    title: str
    page: int | None = None  # page of the heading line
    lines: list[Line] = field(default_factory=list)
    children: list["_Section"] = field(default_factory=list)


@dataclass
class _Unit:
    """A run of lines packed together; atomic units are kept whole if they fit."""

    lines: list[Line]
    atomic: bool = False


# --------------------------------------------------------------------------- helpers


def extract_sa_refs(text: str) -> list[str]:
    """All distinct 'SA nnn' references in `text`, sorted, normalised to 'SA 500'."""
    return sorted({f"SA {m}" for m in _SA_REF.findall(text)})


def _is_all_caps(text: str) -> bool:
    """Heading-style capitals. 80% (not 100%) so that e.g. '(NGOs)' still counts."""
    letters = [c for c in text if c.isalpha()]
    return len(letters) >= 2 and sum(c.isupper() for c in letters) / len(letters) >= 0.8


def _pages(lines: Sequence[Line]) -> tuple[int | None, int | None]:
    pages = [ln.page for ln in lines if ln.page is not None]
    return (min(pages), max(pages)) if pages else (None, None)


class _ChunkBuilder:
    """Packs units into token-capped windows and emits Chunk objects."""

    def __init__(self, doc_id: str, chapter: int | None, count: TokenCounter,
                 profile: ChunkingProfile) -> None:
        self.doc_id, self.chapter, self.count, self.profile = doc_id, chapter, count, profile
        self.max_tokens, self.overlap_tokens = profile.max_tokens, profile.overlap_tokens
        self.chunks: list[Chunk] = []
        self.by_id: dict[str, Chunk] = {}
        self._key_uses: dict[str, int] = {}

    def section_key(self, section: str, heading: str) -> str:
        """The stable key of one section (see the module docstring)."""
        if section and section != "Q":
            base = section
        else:
            digest = hashlib.sha256(" ".join(heading.split()).encode("utf-8")).hexdigest()[:6]
            base = f"{'q' if section == 'Q' else 'h'}-{digest}"
        uses = self._key_uses.get(base, 0) + 1
        self._key_uses[base] = uses
        return base if uses == 1 else f"{base}-{uses}"

    def _tokens(self, lines: Sequence[Line]) -> int:
        return self.count("\n".join(ln.text for ln in lines)) if lines else 0

    def _overlap_tail(self, window: list[Line]) -> list[Line]:
        """Last lines of `window` worth about `overlap_tokens` tokens."""
        tail: list[Line] = []
        for ln in reversed(window):
            if self._tokens([ln, *tail]) > self.overlap_tokens:
                break
            tail.insert(0, ln)
        if not tail and window:  # last line alone is longer than the overlap
            words = window[-1].text.split()
            keep = max(1, int(self.overlap_tokens * _WORDS_PER_TOKEN))
            tail = [Line(" ".join(words[-keep:]), window[-1].page)]
        return tail

    def _split_long_line(self, line: Line, budget: int) -> list[list[Line]]:
        words, out, cur = line.text.split(), [], []
        for w in words:
            if cur and self.count(" ".join([*cur, w])) > budget:
                out.append([Line(" ".join(cur), line.page)])
                cur = []
            cur.append(w)
        if cur:
            out.append([Line(" ".join(cur), line.page)])
        return out

    def windows(self, units: list[_Unit], budget: int) -> list[list[Line]]:
        """Greedy packing of units into windows of at most `budget` tokens."""
        result: list[list[Line]] = []
        cur: list[Line] = []

        def flush() -> None:
            nonlocal cur
            if cur:
                result.append(cur)
                cur = self._overlap_tail(cur)

        for unit in units:
            size = self._tokens(unit.lines)
            if size > budget:  # cannot fit even alone: split line by line
                for ln in unit.lines:
                    pieces = (self._split_long_line(ln, budget - self.overlap_tokens)
                              if self._tokens([ln]) > budget else [[ln]])
                    for piece in pieces:
                        if self._tokens([*cur, *piece]) > budget:
                            flush()
                            if self._tokens([*cur, *piece]) > budget:
                                cur = []
                        cur.extend(piece)
                continue
            if cur and self._tokens([*cur, *unit.lines]) > budget:
                flush()
                if self._tokens([*cur, *unit.lines]) > budget:
                    cur = []  # overlap would push a whole unit over the cap: drop it
            cur.extend(unit.lines)
        if cur and (not result or cur != self._overlap_tail(result[-1])):
            result.append(cur)
        return result

    def emit(self, *, chunk_id: str, header: str, body_lines: list[Line],
             parent_id: str | None, section: str, section_title: str,
             page_hint: int | None = None) -> str:
        """Create one chunk; return its id. `page_hint` is used when the body is empty."""
        text = "\n".join([header, *(ln.text for ln in body_lines)]).strip()
        start, end = _pages(body_lines)
        if start is None:
            start = end = page_hint
        chunk = Chunk(
            chunk_id=chunk_id, doc_id=self.doc_id, parent_id=parent_id, text=text,
            token_count=self.count(text), section=section, section_title=section_title,
            chapter=self.chapter, page_start=start, page_end=end, sa_refs=extract_sa_refs(text),
            content_hash=text_hash(text), chunking_profile=self.profile.ref,
        )
        self.chunks.append(chunk)
        self.by_id[chunk_id] = chunk
        return chunk_id

    def emit_section(self, *, header: str, units: list[_Unit], parent_id: str | None,
                     section: str, section_title: str, page_hint: int | None = None) -> list[str]:
        """Window a section's units and emit one chunk per window."""
        key = self.section_key(section, section_title or header)
        budget = max(self.max_tokens - self.count(header), self.overlap_tokens * 2)
        ids = []
        for n, window in enumerate(self.windows(units, budget) or [[]], start=1):
            ids.append(self.emit(chunk_id=f"{self.doc_id}:{key}:{n}", header=header,
                                 body_lines=window, parent_id=parent_id, section=section,
                                 section_title=section_title, page_hint=page_hint))
        return ids


# ------------------------------------------------------------------ ICAI chapters (PDF)


def _line_units(lines: list[Line]) -> list[_Unit]:
    """Group lines into units: illustration/example blocks are atomic, others per line."""
    units: list[_Unit] = []
    block: list[Line] | None = None
    for ln in lines:
        if _BLOCK_START.match(ln.text):
            if block:
                units.append(_Unit(block, atomic=True))
            block = [ln]
        elif block is not None:
            block.append(ln)
        else:
            units.append(_Unit([ln]))
    if block:
        units.append(_Unit(block, atomic=True))
    return units


def _parse_chapter(lines: list[Line]) -> tuple[list[_Section], list[Line]]:
    """Split chapter lines into top-level sections (with children) and the TYK tail."""
    sections = [_Section("0", "Chapter introduction")]
    current_top, last_sub = 0, (0, 0, 0)
    # Index of every sub-heading line and its major number, for the look-ahead rule:
    # a candidate top-level heading N is rejected if the next sub-heading after it still
    # belongs to an earlier section (e.g. ch9 has an all-caps numbered list "2. TENURE"
    # inside section 1.2, followed later by sub-heading 1.4).
    sub_positions = [
        (idx, int(m.group(1)))
        for idx, ln in enumerate(lines)
        if (m := _SUB_HEADING.match(ln.text.strip()))
    ]

    sub_index = [p for p, _ in sub_positions]

    def next_sub_major(after: int) -> int | None:
        pos = bisect.bisect_right(sub_index, after)
        return sub_positions[pos][1] if pos < len(sub_positions) else None

    i = 0
    while i < len(lines):
        text = lines[i].text.strip()
        if text.upper() == TYK_MARKER:
            return sections, lines[i:]

        top = _TOP_NUMBER.match(text)
        if top and not _SUB_HEADING.match(text):
            number, rest = int(top.group(1)), top.group(2).strip()
            nxt = lines[i + 1].text.strip() if i + 1 < len(lines) else ""
            title_starts = (rest and _is_all_caps(rest)) or (not rest and _is_all_caps(nxt))
            banner = (rest or nxt).upper().startswith(_BANNER_TITLES)
            upcoming = next_sub_major(i)
            consistent = upcoming is None or upcoming >= number
            if title_starts and not banner and consistent and current_top < number <= current_top + 3:
                title_parts, j = ([rest] if rest else []), i + 1
                while j < len(lines) and len(title_parts) < 4 and _is_all_caps(lines[j].text) \
                        and not _TOP_NUMBER.match(lines[j].text.strip()):
                    title_parts.append(lines[j].text.strip())
                    j += 1
                sections.append(_Section(str(number), " ".join(title_parts), lines[i].page))
                current_top, last_sub = number, (number, 0, 0)
                i = j
                continue

        sub = _SUB_HEADING.match(text)
        if sub:
            major, minor, third = int(sub.group(1)), int(sub.group(2)), int(sub.group(3) or 0)
            key = (major, minor, third)
            if (major == current_top + 1 or (current_top == 0 and major <= 40)) \
                    and minor == 1 and third == 0:
                # Top-level heading was not detected (or the document, like a CA Final
                # Financial Reporting unit, numbers sub-sections "2.1, 2.2" under an
                # unnumbered unit title): open an untitled parent.
                sections.append(_Section(str(major), f"Section {major}", lines[i].page))
                current_top, last_sub = major, (major, 0, 0)
            if major == current_top and key > last_sub:
                number = ".".join(str(n) for n in key if n) if third else f"{major}.{minor}"
                sections[-1].children.append(_Section(number, sub.group(4).strip(), lines[i].page))
                last_sub = key
                i += 1
                continue

        target = sections[-1].children[-1] if sections[-1].children else sections[-1]
        target.lines.append(lines[i])
        i += 1
    return sections, []


def chunk_icai_chapter(
    *,
    doc_id: str,
    doc_title: str,
    chapter: int | None,
    pages: Sequence[tuple[int, str]],
    count_tokens: TokenCounter,
    max_tokens: int = 400,
    overlap_tokens: int = 50,
    profile: ChunkingProfile | None = None,
) -> ChunkingResult:
    """Chunk a cleaned ICAI chapter given as (page_number, text) pairs. `profile` (default:
    the standards profile with these sizes) sets the window sizes and is recorded."""
    lines = [Line(t, page) for page, text in pages for t in text.splitlines() if t.strip()]
    sections, tyk = _parse_chapter(lines)
    b = _ChunkBuilder(doc_id, chapter, count_tokens,
                      profile or profile_for("standards", max_tokens, overlap_tokens))

    for top in sections:
        if not top.lines and not top.children:
            continue
        top_label = f"{top.number} {top.title}".strip()
        header = f"{doc_title} > {top_label}"
        lead = top.lines or [Line("Sub-sections: " + "; ".join(
            f"{c.number} {c.title}" for c in top.children), None)]
        lead_ids = b.emit_section(header=header, units=_line_units(lead), parent_id=None,
                                  section=top.number, section_title=top.title,
                                  page_hint=top.page)
        parent_id = lead_ids[0]
        for extra in lead_ids[1:]:  # long lead text: later windows are children
            b.by_id[extra].parent_id = parent_id
        first_child = len(b.chunks)
        for child in top.children:
            b.emit_section(header=f"{header} > {child.number} {child.title}",
                           units=_line_units(child.lines), parent_id=parent_id,
                           section=child.number, section_title=child.title,
                           page_hint=child.page)
        parent = b.by_id[parent_id]
        if parent.page_start is None:  # outline-only parent: span its sub-sections
            child_pages = [p for c in b.chunks[first_child:] for p in (c.page_start, c.page_end)
                           if p is not None]
            if child_pages:
                parent.page_start, parent.page_end = min(child_pages), max(child_pages)

    tyk_text = "\n".join(f"[p.{ln.page}] {ln.text}" for ln in tyk)
    return ChunkingResult(b.chunks, tyk_text)


# ------------------------------------------------------------- synthetic Markdown docs


def _md_units(text: str, whole: bool) -> list[_Unit]:
    """Paragraph units; tables are atomic. `whole=True` makes the entire text atomic."""
    lines = [Line(t) for t in text.splitlines() if t.strip()]
    if whole:
        return [_Unit(lines, atomic=True)] if lines else []
    units: list[_Unit] = []
    for para in re.split(r"\n\s*\n", text):
        plines = [Line(t) for t in para.splitlines() if t.strip()]
        if not plines:
            continue
        is_table = all(ln.text.lstrip().startswith("|") for ln in plines)
        units.append(_Unit(plines, atomic=is_table))
    return units


def _md_number(heading: str) -> tuple[str, str]:
    """Split '2. Overall materiality' into ('2', 'Overall materiality')."""
    if heading.startswith("Q:"):
        return "Q", heading[2:].strip()
    m = _MD_NUMBER.match(heading)
    return (m.group(1), m.group(2).strip()) if m else ("", heading)


def chunk_markdown(
    *,
    doc_id: str,
    doc_title: str,
    source_type: str,
    sections: Sequence[MdSection],
    count_tokens: TokenCounter,
    max_tokens: int = 400,
    overlap_tokens: int = 50,
    profile: ChunkingProfile | None = None,
) -> ChunkingResult:
    """Chunk a synthetic Markdown document that has been split into sections. `profile`
    (default: the source type's profile with these sizes) sets the window sizes and the
    keep-whole heading level, and is recorded on every chunk."""
    profile = profile or profile_for(source_type, max_tokens, overlap_tokens)
    b = _ChunkBuilder(doc_id, None, count_tokens, profile)
    doc_header_id: str | None = None
    parent_id: str | None = None
    parent_header = doc_title

    for i, sec in enumerate(sections):
        number, title = _md_number(sec.heading)
        if sec.level <= 1:
            if sec.text:
                doc_header_id = b.emit_section(
                    header=doc_title, units=_md_units(sec.text, whole=False), parent_id=None,
                    section="", section_title=sec.heading or doc_title)[0]
            parent_id = doc_header_id
            parent_header = doc_title
            continue

        whole = profile.whole_level == sec.level
        if sec.level == 2:
            header = f"{doc_title} > {sec.heading}"
            children = []
            for later in sections[i + 1:]:
                if later.level <= 2:
                    break
                children.append(later.heading)
            body = sec.text or ("Sub-sections: " + "; ".join(children) if children else sec.heading)
            ids = b.emit_section(header=header, units=_md_units(body, whole), parent_id=doc_header_id,
                                 section=number, section_title=title)
            parent_id, parent_header = ids[0], header
        else:
            b.emit_section(header=f"{parent_header} > {sec.heading}",
                           units=_md_units(sec.text, whole), parent_id=parent_id,
                           section=number, section_title=title)
    return ChunkingResult(b.chunks)
