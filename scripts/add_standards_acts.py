"""Add the ICAI Accounting Standards compendium and statutes (e.g. the Companies Act, 2013)
to the corpus (author's request, 2026-11-08).

Run from the project root:
    .venv\\Scripts\\python.exe -m scripts.add_standards_acts
    .venv\\Scripts\\python.exe -m scripts.add_standards_acts --source "D:\\other\\Standards_Acts"

Expected source layout:
    <source>/<ICAI_Accounting_Standards_...>/README.txt + NN_AS_<n>_<title>.pdf, NN_Appendix_<n>.pdf
    <source>/<act>.pdf  (one PDF per statute)

What it does (safe to re-run):
1. Accounting Standards: titles are read from README.txt (the download log, which keeps the
   official titles with punctuation); the date is read from its first line ("... as on
   1 February 2022"). Files go to data/corpus/standards/accounting_standards/ with the README.
2. Acts: title, Act number and date are read from the Act's own first pages
   ("THE COMPANIES ACT, 2013" ... "ACT NO. 18 OF 2013 [29th August, 2013.]"). The PDF has no
   "as amended up to" statement, so the title says "as enacted": later amendments may not be
   reflected. Files go to data/corpus/standards/acts/.
3. Rewrites the rows with doc_id prefix ASC- (standards) and LAW- (acts) in the manifest.
If a date or title cannot be read from the files, the script stops instead of guessing.
"""

import argparse
import re
from datetime import datetime
from pathlib import Path

import pymupdf

from app.config import PROJECT_ROOT
from scripts.corpus_import import copy_if_needed, replace_manifest_rows

DEFAULT_SOURCE = PROJECT_ROOT / "Standards_Acts"
AS_TARGET = PROJECT_ROOT / "data" / "corpus" / "standards" / "accounting_standards"
ACT_TARGET = PROJECT_ROOT / "data" / "corpus" / "standards" / "acts"
PREFIXES = ("ASC-", "LAW-")

_README_ENTRY = re.compile(r"^\s*\d{1,3}\.\s+(?P<title>.+?)\s*$")
_README_FILE = re.compile(r"^\s*File:\s*(?P<file>\S+\.pdf)\s*$", re.I)
_README_DATE = re.compile(r"as on (\d{1,2}(?:st|nd|rd|th)? \w+,? \d{4})", re.I)
_AS_NAME = re.compile(r"^\d{1,3}_AS_(?P<n>\d{1,2})_", re.I)
_APPENDIX_NAME = re.compile(r"^\d{1,3}_Appendix_(?P<n>\d{1,2})", re.I)
_ACT_HEADER = re.compile(r"ACT NO\.?\s*(?P<no>\d+)\s+OF\s+(?P<year>\d{4})\s*\[(?P<date>\d{1,2}(?:st|nd|rd|th)?\s+\w+,?\s+\d{4})", re.I)
_ACT_TITLE = re.compile(r"THE\s+[A-Z][A-Z ,()&-]+ACT,\s*\d{4}")


def parse_date(text: str) -> str:
    """'29th August, 2013' / '1 February 2022' -> 'YYYY-MM-DD'."""
    clean = re.sub(r"(\d)(st|nd|rd|th)", r"\1", text).replace(",", "")
    return datetime.strptime(" ".join(clean.split()), "%d %B %Y").date().isoformat()


def read_readme(readme: Path) -> tuple[str, dict[str, str]]:
    """(date, {file name: official title}) from the Accounting Standards README."""
    lines = readme.read_text(encoding="utf-8").splitlines()
    m = _README_DATE.search(lines[0] if lines else "")
    if not m:
        raise SystemExit(f"Cannot read the 'as on <date>' line in {readme}")
    titles, pending = {}, None
    for line in lines[1:]:
        if (e := _README_ENTRY.match(line)) and not line.lstrip().lower().startswith(("file:", "url:")):
            pending = e.group("title")
        elif (f := _README_FILE.match(line)) and pending:
            titles[f.group("file")] = pending
            pending = None
    return parse_date(m.group(1)), titles


def standard_row(pdf: Path, dest: Path, date: str, readme_title: str | None) -> dict[str, str]:
    if (m := _AS_NAME.match(pdf.name)):
        doc_id = f"ASC-AS{int(m.group('n')):02d}"
        n = int(m.group("n"))
        title = readme_title or f"AS {n} " + pdf.stem.split(f"AS_{m.group('n')}_", 1)[-1].replace("_", " ")
    elif (m := _APPENDIX_NAME.match(pdf.name)):
        doc_id = f"ASC-APP{int(m.group('n'))}"
        title = readme_title or f"Appendix {m.group('n')}"
    else:
        raise SystemExit(f"Unrecognised Accounting Standards file name: {pdf.name}")
    title = re.sub(r"^AS (\d+) ", r"AS \1: ", title)  # 'AS 22 Accounting ...' -> 'AS 22: Accounting ...'
    day = datetime.fromisoformat(date)
    stamp = f"{day.day} {day:%B %Y}"
    return {"doc_id": doc_id, "path": dest.relative_to(PROJECT_ROOT).as_posix(),
            "source_type": "standards",
            "title": f"ICAI Accounting Standards (as on {stamp}), {title}",
            "version": f"Compendium as on {date}", "effective_date": date, "supersedes": "",
            "access_level": "staff", "engagement_id": "", "synthetic": "false"}


def read_act(pdf: Path) -> tuple[str, str, str]:
    """(title, act number text, date) from the first pages of a statute PDF."""
    with pymupdf.open(pdf) as d:
        text = " ".join(" ".join(d[i].get_text().split()) for i in range(min(d.page_count, 30)))
    title_m, header_m = _ACT_TITLE.search(text), _ACT_HEADER.search(text)
    if not (title_m and header_m):
        raise SystemExit(f"Cannot read the Act title / 'ACT NO. .. OF ....' line in {pdf.name}")
    words = title_m.group(0).title().replace(" And ", " and ").replace(" Of ", " of ")
    return words, f"Act No. {header_m.group('no')} of {header_m.group('year')}", parse_date(header_m.group("date"))


def act_row(pdf: Path, dest: Path) -> dict[str, str]:
    title, number, date = read_act(pdf)
    slug = re.sub(r"[^A-Z0-9]+", "-", title.upper().removeprefix("THE ")).strip("-")
    return {"doc_id": f"LAW-{slug}", "path": dest.relative_to(PROJECT_ROOT).as_posix(),
            "source_type": "standards",
            "title": f"{title} ({number}, as enacted; later amendments may not be reflected)",
            "version": f"As enacted ({number})", "effective_date": date, "supersedes": "",
            "access_level": "staff", "engagement_id": "", "synthetic": "false"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Add Accounting Standards and Acts to the corpus.")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    args = parser.parse_args(argv)
    if not args.source.is_dir():
        raise SystemExit(f"Source folder not found: {args.source}")

    rows, copied = [], 0
    for folder in sorted(p for p in args.source.iterdir() if p.is_dir() and "accounting_standards" in p.name.lower()):
        readme = folder / "README.txt"
        if not readme.exists():
            raise SystemExit(f"{folder.name} has no README.txt with the 'as on' date and titles.")
        date, titles = read_readme(readme)
        copied += copy_if_needed(readme, AS_TARGET / "README.txt")
        for pdf in sorted(folder.glob("*.pdf")):
            dest = AS_TARGET / pdf.name
            copied += copy_if_needed(pdf, dest)
            rows.append(standard_row(pdf, dest, date, titles.get(pdf.name)))
    for pdf in sorted(args.source.glob("*.pdf")):
        dest = ACT_TARGET / pdf.name
        copied += copy_if_needed(pdf, dest)
        rows.append(act_row(pdf, dest))

    ids = [r["doc_id"] for r in rows]
    if len(ids) != len(set(ids)):
        raise SystemExit("Two files map to the same doc_id.")
    total = replace_manifest_rows(PREFIXES, rows)
    print(f"Documents in manifest: {len(rows)}  |  files copied this run: {copied}  |  manifest rows: {total}")
    for r in rows:
        print(f"  {r['doc_id']:24} {r['effective_date']}  {r['title']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
