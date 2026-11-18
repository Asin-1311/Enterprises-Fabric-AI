"""Generate the SYNTHETIC corpus, users, engagements and the manifest.

Run from the project root:
    .venv\\Scripts\\python.exe -m scripts.make_synthetic_docs          # refuses to overwrite
    .venv\\Scripts\\python.exe -m scripts.make_synthetic_docs --force  # regenerate everything

Outputs:
    data/corpus/<source_type>/...md   synthetic documents (each starts with the banner)
    data/users.csv                    synthetic users
    data/engagements.csv              synthetic engagements and team members
    data/manifest.csv                 one row per document, ICAI chapters included
    data/manifest.synthetic.csv       the synthetic rows only (indexable from a clean clone,
                                      where the licensed ICAI PDFs are absent)

The script is deterministic: the same code always writes the same files. Manifest rows for
documents the author added by hand (doc_ids this script does not generate) are kept.
"""

import argparse
import csv
import sys
from pathlib import Path

from app.config import PROJECT_ROOT
from app.ingest.md_loader import SYNTHETIC_BANNER
from scripts.synthetic import SynthDoc
from scripts.synthetic import calendar_docs, checklists, circulars, engagement, faq, firm_policy, templates
from scripts.synthetic.people import ENGAGEMENTS, USERS, demo_email

DATA = PROJECT_ROOT / "data"
CORPUS = DATA / "corpus"

MANIFEST_COLUMNS = [
    "doc_id",
    "path",
    "source_type",
    "title",
    "version",
    "effective_date",
    "supersedes",
    "access_level",
    "engagement_id",
    "synthetic",
]

# ICAI chapters (real, not synthetic). effective_date is a PLACEHOLDER: the author
# should replace it with the edition date printed on the study material.
ICAI_EDITION_DATE_PLACEHOLDER = "2025-04-01"
STANDARDS: list[tuple[str, str, str]] = [
    ("STD-CH04", "ch04_audit_evidence.pdf", "Chapter 4: Audit Evidence"),
    ("STD-CH05", "ch05_audit_of_items_of_financial_statements.pdf",
     "Chapter 5: Audit of Items of Financial Statements"),
    ("STD-CH06", "ch06_audit_documentation.pdf", "Chapter 6: Audit Documentation"),
    ("STD-CH07", "ch07_completion_and_review.pdf", "Chapter 7: Completion and Review"),
    ("STD-CH08", "ch08_audit_report.pdf", "Chapter 8: Audit Report"),
    ("STD-CH09", "ch09_special_features_of_audit_of_different_entities.pdf",
     "Chapter 9: Special Features of Audit of Different Types of Entities"),
    ("STD-CH10", "ch10_audit_of_banks.pdf", "Chapter 10: Audit of Banks"),
    ("STD-CH11", "ch11_ethics_and_terms_of_audit_engagements.pdf",
     "Chapter 11: Ethics and Terms of Audit Engagements"),
]


def all_synthetic_docs() -> list[SynthDoc]:
    """Every synthetic document, in a fixed order."""
    return [
        *firm_policy.DOCS,
        *circulars.DOCS,
        *checklists.DOCS,
        *templates.DOCS,
        *calendar_docs.DOCS,
        *faq.DOCS,
        *engagement.DOCS,
    ]


def doc_path(doc: SynthDoc) -> Path:
    """Absolute path where a synthetic document is written."""
    return CORPUS / doc.source_type / doc.filename


def relative(path: Path) -> str:
    """Project-relative POSIX path, as stored in the manifest."""
    return path.relative_to(PROJECT_ROOT).as_posix()


def manifest_rows(docs: list[SynthDoc]) -> list[dict[str, str]]:
    """Build manifest rows for the ICAI chapters and all synthetic documents."""
    rows = [
        {
            "doc_id": doc_id,
            "path": relative(CORPUS / "standards" / filename),
            "source_type": "standards",
            "title": f"ICAI Auditing and Ethics, {title}",
            "version": "ICAI study material",
            "effective_date": ICAI_EDITION_DATE_PLACEHOLDER,
            "supersedes": "",
            "access_level": "staff",
            "engagement_id": "",
            "synthetic": "false",
        }
        for doc_id, filename, title in STANDARDS
    ]
    rows += [
        {
            "doc_id": d.doc_id,
            "path": relative(doc_path(d)),
            "source_type": d.source_type,
            "title": d.title,
            "version": d.version,
            "effective_date": d.effective_date,
            "supersedes": d.supersedes,
            "access_level": d.access_level,
            "engagement_id": d.engagement_id,
            "synthetic": "true",
        }
        for d in docs
    ]
    return rows


def author_rows(generated: list[dict[str, str]]) -> list[dict[str, str]]:
    """Rows in the existing manifest that this script did not generate (documents the author
    added by hand). They are kept, unchanged, when the manifest is rewritten."""
    path = DATA / "manifest.csv"
    if not path.exists():
        return []
    ours = {r["doc_id"] for r in generated}
    with path.open(newline="", encoding="utf-8") as f:
        return [{c: (row.get(c) or "") for c in MANIFEST_COLUMNS}
                for row in csv.DictReader(f) if row.get("doc_id") and row["doc_id"] not in ours]


def write_csv(path: Path, header: list[str], rows: list[list[str]] | list[dict[str, str]]) -> None:
    """Write a UTF-8 CSV file with a header row."""
    with path.open("w", newline="", encoding="utf-8") as f:
        if rows and isinstance(rows[0], dict):
            writer = csv.DictWriter(f, fieldnames=header)
            writer.writeheader()
            writer.writerows(rows)  # type: ignore[arg-type]
        else:
            writer = csv.writer(f)
            writer.writerow(header)
            writer.writerows(rows)  # type: ignore[arg-type]


def main(argv: list[str] | None = None) -> int:
    """Generate all outputs. Returns a process exit code."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--force", action="store_true", help="overwrite existing files")
    args = parser.parse_args(argv)

    docs = all_synthetic_docs()
    targets = [doc_path(d) for d in docs] + [
        DATA / "users.csv", DATA / "engagements.csv", DATA / "manifest.csv",
        DATA / "manifest.synthetic.csv",
    ]
    existing = [p for p in targets if p.exists()]
    if existing and not args.force:
        print(f"{len(existing)} output file(s) already exist, e.g. {relative(existing[0])}.")
        print("Nothing written. Re-run with --force to overwrite (this discards manual edits).")
        return 1

    for d in docs:
        path = doc_path(d)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{SYNTHETIC_BANNER}\n\n{d.body.strip()}\n", encoding="utf-8")

    write_csv(DATA / "users.csv", ["user_id", "name", "role", "synthetic", "email"],
              [[uid, name, role, "true", demo_email(name)] for uid, name, role in USERS])
    write_csv(DATA / "engagements.csv",
              ["engagement_id", "client_name", "industry", "fy", "partner_id", "member_ids"],
              [[e, c, i, fy, p, ";".join(m)] for e, c, i, fy, p, m in ENGAGEMENTS])
    generated = manifest_rows(docs)
    every_row = generated + author_rows(generated)
    write_csv(DATA / "manifest.csv", MANIFEST_COLUMNS, every_row)
    write_csv(DATA / "manifest.synthetic.csv", MANIFEST_COLUMNS,
              [r for r in every_row if r["synthetic"] == "true"])

    print(f"Wrote {len(docs)} synthetic documents, {len(USERS)} users, "
          f"{len(ENGAGEMENTS)} engagements and {len(docs) + len(STANDARDS)} manifest rows.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
