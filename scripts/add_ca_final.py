"""Add the ICAI CA Final study material to the corpus (author's request, 2026-11-08).

Run from the project root:
    .venv\\Scripts\\python.exe -m scripts.add_ca_final
    .venv\\Scripts\\python.exe -m scripts.add_ca_final --source "D:\\other\\folder"

What it does (safe to re-run):
1. Copies every CHAPTER PDF of Papers 1-5 into data/corpus/standards/ca_final/<paper>/.
   The originals are left where they are.
2. Skips files that are not study chapters (author's choice): 'Initial Pages' (front
   matter), 'Crossword' answer keys, and Paper 6's model test papers (exam questions with
   answers, kept out of the index for the same reason as 'Test Your Knowledge'). They are
   copied to data/ca_final_not_indexed/ and listed with the reason in skipped_files.csv.
3. Rewrites the CA Final rows of data/manifest.csv (doc_id prefix CAF-), keeping every
   other row unchanged. Then run `python -m app.ingest.manifest` and the indexer.

The ICAI material is copyright; all copies are git-ignored and used locally only.
"""

import argparse
import csv
import re
from dataclasses import dataclass
from pathlib import Path

from app.config import PROJECT_ROOT
from scripts.corpus_import import copy_if_needed, replace_manifest_rows

DEFAULT_SOURCE = PROJECT_ROOT / "ICAI_CA_Final_All_Papers_Chapter_Wise_Latest"
TARGET = PROJECT_ROOT / "data" / "corpus" / "standards" / "ca_final"
NOT_INDEXED = PROJECT_ROOT / "data" / "ca_final_not_indexed"
DOC_PREFIX = "CAF-"
# Placeholder like the Intermediate chapters: replace with the edition date (PLACEHOLDERS.md).
EDITION_DATE_PLACEHOLDER = "2025-04-01"

# Paper number -> (folder slug, short name used in titles)
PAPERS: dict[int, tuple[str, str]] = {
    1: ("p1_financial_reporting", "Financial Reporting"),
    2: ("p2_advanced_financial_management", "Advanced Financial Management"),
    3: ("p3_advanced_auditing", "Advanced Auditing, Assurance and Professional Ethics"),
    4: ("p4_direct_tax_laws", "Direct Tax Laws and International Taxation"),
    5: ("p5_indirect_tax_laws", "Indirect Tax Laws"),
    6: ("p6_integrated_business_solutions", "Integrated Business Solutions"),
}
SKIP_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"initial[_ ]pages?", re.I), "front matter (title pages, contents)"),
    (re.compile(r"crossword", re.I), "crossword puzzle or answer key"),
]


@dataclass(frozen=True)
class SourceFile:
    path: Path
    paper: int
    index: int
    skip_reason: str  # "" = index it

    @property
    def doc_id(self) -> str:
        return f"{DOC_PREFIX}P{self.paper}-{self.index:02d}"


def paper_number(folder_name: str) -> int | None:
    m = re.match(r"Paper\s*(\d)", folder_name, re.I)
    return int(m.group(1)) if m else None


def classify(path: Path, paper: int) -> SourceFile:
    """Decide whether a PDF is indexed, and give it its position number."""
    m = re.match(r"(\d{1,3})", path.stem)
    index = int(m.group(1)) if m else 0
    reason = ""
    if paper == 6:
        reason = "model test papers (exam questions with answers)"
    for pattern, why in SKIP_RULES:
        if pattern.search(path.stem):
            reason = why
    return SourceFile(path, paper, index, reason)


def title_for(f: SourceFile) -> str:
    """'04_Chapter_3_Audit_Planning_Strategy_and_Execution' ->
    'ICAI CA Final Paper 3 (Advanced Auditing, ...), Chapter 3: Audit Planning Strategy and Execution'."""
    label = re.sub(r"^\d{1,3}_", "", f.path.stem).replace("__", " & ").replace("_", " ")
    label = re.sub(r"^(Chapter|Unit)[\s-]+(\d+)\s+", r"\1 \2: ", label)
    label = re.sub(r"\s{2,}", " ", label).strip()
    return f"ICAI CA Final Paper {f.paper} ({PAPERS[f.paper][1]}), {label}"


def discover(source: Path) -> list[SourceFile]:
    files = []
    for folder in sorted(p for p in source.iterdir() if p.is_dir()):
        paper = paper_number(folder.name)
        if paper is None or paper not in PAPERS:
            continue
        files += [classify(pdf, paper) for pdf in sorted(folder.glob("*.pdf"))]
    return files


def manifest_row(f: SourceFile, dest: Path) -> dict[str, str]:
    return {"doc_id": f.doc_id, "path": dest.relative_to(PROJECT_ROOT).as_posix(),
            "source_type": "standards", "title": title_for(f),
            "version": "ICAI study material (CA Final)", "effective_date": EDITION_DATE_PLACEHOLDER,
            "supersedes": "", "access_level": "staff", "engagement_id": "", "synthetic": "false"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Add ICAI CA Final chapters to the corpus.")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    args = parser.parse_args(argv)
    if not args.source.is_dir():
        raise SystemExit(f"Source folder not found: {args.source}")

    files = discover(args.source)
    ids = [f.doc_id for f in files if not f.skip_reason]
    if len(ids) != len(set(ids)):
        raise SystemExit("Two chapter files map to the same doc_id; check the file numbering.")

    rows, skipped, copied = [], [], 0
    for f in files:
        slug = PAPERS[f.paper][0]
        if f.skip_reason:
            copied += copy_if_needed(f.path, NOT_INDEXED / slug / f.path.name)
            skipped.append({"paper": f.paper, "file": f.path.name, "reason": f.skip_reason})
            continue
        dest = TARGET / slug / f.path.name
        copied += copy_if_needed(f.path, dest)
        rows.append(manifest_row(f, dest))

    NOT_INDEXED.mkdir(parents=True, exist_ok=True)
    with (NOT_INDEXED / "skipped_files.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["paper", "file", "reason"])
        w.writeheader()
        w.writerows(skipped)

    total = replace_manifest_rows(DOC_PREFIX, rows)

    print(f"Chapters added to manifest: {len(rows)}  |  not indexed: {len(skipped)}  |  "
          f"files copied this run: {copied}")
    print(f"Manifest now has {total} rows. Next: python -m app.ingest.manifest, "
          "then python -m app.ingest.indexer")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
