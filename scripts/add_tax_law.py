"""Add the ICAI Taxation study material (Income Tax Law and GST) to the corpus
(author's request, 2026-11-08).

Run from the project root:
    .venv\\Scripts\\python.exe -m scripts.add_tax_law
    .venv\\Scripts\\python.exe -m scripts.add_tax_law --source "D:\\other\\Tax_Law"

What it does (safe to re-run):
1. Copies every PDF (GST_ch<N>.pdf, Incometax_ch<N>[u<U>].pdf) into
   data/corpus/standards/tax_law/<gst|income_tax>/. The originals are left in place.
2. Builds each title from the PDF's own running headers (the chapter title printed at the
   top of its pages), not from memory.
3. effective_date: GST chapters state "position of GST law existing as on 30.04.2026", so
   2026-04-30 is used; the Income Tax chapters state no date, so a placeholder is used
   (see docs/PLACEHOLDERS.md).
4. Rewrites the Taxation rows of data/manifest.csv (doc_id prefix TAX-), keeping all others.

The ICAI material is copyright; all copies are git-ignored and used locally only.
"""

import argparse
import re
from dataclasses import dataclass
from pathlib import Path

from app.config import PROJECT_ROOT
from app.ingest.cleaner import detect_running_headers, normalize_text
from app.ingest.pdf_loader import load_pdf_pages
from scripts.corpus_import import copy_if_needed, replace_manifest_rows

DEFAULT_SOURCE = PROJECT_ROOT / "Tax_Law"
TARGET = PROJECT_ROOT / "data" / "corpus" / "standards" / "tax_law"
DOC_PREFIX = "TAX-"
GST_STATED_DATE = "2026-04-30"  # stated on the GST chapters' first page
INCOME_TAX_DATE_PLACEHOLDER = "2025-04-01"  # not stated in the files; see PLACEHOLDERS.md

SERIES = {  # key -> (folder, series name as printed, header spellings to drop from titles)
    "GST": ("gst", "Goods and Services Tax", {"GOODS AND SERVICES TAX", "GOODS AND SERVICE TAX"}),
    "IT": ("income_tax", "Income Tax Law", {"INCOME TAX LAW"}),
}
_NAME = re.compile(r"^(?P<series>GST|Incometax)(?:_Actbook)?_ch(?P<ch>\d{1,2})(?:u(?P<unit>\d{1,2}))?$", re.I)
_KEEP_UPPER = {"GST", "IGST", "CGST", "SGST", "TDS", "TCS", "ITC"}
_SMALL = {"and", "of", "or", "in", "the", "from", "at", "to", "for", "on", "by", "a", "an", "under"}


@dataclass(frozen=True)
class TaxFile:
    path: Path
    series: str  # "GST" | "IT"
    chapter: int
    unit: int | None

    @property
    def doc_id(self) -> str:
        unit = f"-U{self.unit}" if self.unit else ""
        return f"{DOC_PREFIX}{self.series}-{self.chapter:02d}{unit}"


def parse_name(path: Path) -> TaxFile | None:
    m = _NAME.match(path.stem)
    if not m:
        return None
    series = "GST" if m.group("series").upper() == "GST" else "IT"
    unit = int(m.group("unit")) if m.group("unit") else None
    return TaxFile(path, series, int(m.group("ch")), unit)


def smart_title(text: str) -> str:
    """'TAX INVOICE, CREDIT AND DEBIT NOTES' -> 'Tax Invoice, Credit and Debit Notes'."""
    words = []
    for i, w in enumerate(text.split()):
        core = w.strip(",;:–-()")
        if core.upper() in _KEEP_UPPER:
            words.append(w.upper())
        elif i > 0 and core.lower() in _SMALL:
            words.append(w.lower())
        else:  # capitalise each part of a hyphenated word: E-WAY -> E-Way
            words.append("-".join(p[:1].upper() + p[1:].lower() for p in w.split("-")))
    return " ".join(words)


def chapter_title(pages: list[str], series_headers: set[str]) -> str | None:
    """The chapter-title header lines of a PDF, in the order printed on the page."""
    headers = detect_running_headers(pages) - series_headers
    if not headers:
        return None
    for raw in pages[1:]:
        top = [" ".join(normalize_text(ln).upper().split()) for ln in raw.splitlines() if ln.strip()][:4]
        found = [ln for ln in top if ln in headers]
        if len(found) == len(headers):
            return smart_title(" ".join(found))
    return smart_title(" ".join(sorted(headers)))


def title_for(f: TaxFile, ch_title: str | None) -> str:
    unit = f", Unit {f.unit}" if f.unit else ""
    name = SERIES[f.series][1]
    return f"ICAI Taxation study material, {name}, Chapter {f.chapter}{unit}: {ch_title or f.path.stem}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Add ICAI Taxation (Income Tax, GST) chapters.")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    args = parser.parse_args(argv)
    if not args.source.is_dir():
        raise SystemExit(f"Source folder not found: {args.source}")

    files, unknown = [], []
    for pdf in sorted(args.source.glob("*.pdf")):
        (files.append(f) if (f := parse_name(pdf)) else unknown.append(pdf.name))
    if unknown:
        raise SystemExit(f"Unrecognised file names (expected GST_chN / Incometax_chN[uU]): {unknown}")
    ids = [f.doc_id for f in files]
    if len(ids) != len(set(ids)):
        raise SystemExit("Two files map to the same doc_id; check the file names.")

    rows, copied = [], 0
    for f in sorted(files, key=lambda x: (x.series, x.chapter, x.unit or 0)):
        folder, _, series_headers = SERIES[f.series]
        dest = TARGET / folder / f.path.name
        copied += copy_if_needed(f.path, dest)
        pages = [p.text for p in load_pdf_pages(f.path)]
        rows.append({
            "doc_id": f.doc_id, "path": dest.relative_to(PROJECT_ROOT).as_posix(),
            "source_type": "standards", "title": title_for(f, chapter_title(pages, series_headers)),
            "version": "ICAI study material (Taxation)",
            "effective_date": GST_STATED_DATE if f.series == "GST" else INCOME_TAX_DATE_PLACEHOLDER,
            "supersedes": "", "access_level": "staff", "engagement_id": "", "synthetic": "false"})
    total = replace_manifest_rows(DOC_PREFIX, rows)
    print(f"Taxation chapters in manifest: {len(rows)}  |  files copied this run: {copied}  |  "
          f"manifest rows: {total}")
    for r in rows:
        print(f"  {r['doc_id']:14} {r['title']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
