"""Load and validate `data/manifest.csv`.

The validator returns a list of human-readable problems (empty list = valid). It checks:
- required columns and required fields present,
- unique doc_id and unique path,
- the file exists, lives under data/corpus/<source_type>/ and is in the right folder,
- known source_type and access_level values; synthetic is 'true' or 'false',
- effective_date is a real YYYY-MM-DD date,
- supersedes points to an existing, different, older document of the same source type,
  and no document is superseded twice,
- engagement_id is known; engagement documents have one, other documents do not,
- standards are not synthetic; synthetic Markdown files start with the SYNTHETIC banner,
- every document file in the corpus folder has a manifest row (no orphans),
- the optional governance columns (M12a): source_id looks like an id, owner is a known
  user, classification is public_internal | confidential | restricted.
"""

import csv
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from app.ingest.md_loader import SYNTHETIC_BANNER

SOURCE_TYPES = (
    "standards",
    "firm_policy",
    "circulars",
    "checklists",
    "templates",
    "engagement",
    "calendar",
    "faq",
)
ACCESS_LEVELS = ("staff", "manager", "partner")
# Columns every manifest must have (Phase 1).
REQUIRED_COLUMNS = (
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
)
# Governance columns added in Phase 2 (CLAUDE.md section 5). Optional, so an older manifest
# stays valid: empty means "use the source's default" (app/ingest/enrich.py).
GOVERNANCE_COLUMNS = ("source_id", "owner", "classification")
MANIFEST_COLUMNS = REQUIRED_COLUMNS + GOVERNANCE_COLUMNS
CLASSIFICATIONS = ("public_internal", "confidential", "restricted")
_SOURCE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{2,63}$")
REQUIRED_FIELDS = ("doc_id", "path", "source_type", "title", "effective_date",
                   "access_level", "synthetic")
DOCUMENT_SUFFIXES = (".pdf", ".md")


@dataclass(frozen=True)
class ManifestRow:
    """One manifest row, as strings exactly as written in the CSV."""

    doc_id: str
    path: str
    source_type: str
    title: str
    version: str
    effective_date: str
    supersedes: str
    access_level: str
    engagement_id: str
    synthetic: str
    source_id: str = ""
    owner: str = ""
    classification: str = ""

    @property
    def is_synthetic(self) -> bool:
        return self.synthetic == "true"


def read_manifest(path: Path) -> tuple[list[str], list[ManifestRow]]:
    """Read the manifest CSV. Returns (header, rows); missing cells become ''."""
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        header = list(reader.fieldnames or [])
        rows = [
            ManifestRow(**{col: (raw.get(col) or "").strip() for col in MANIFEST_COLUMNS})
            for raw in reader
        ]
    return header, rows


def load_engagement_ids(path: Path) -> set[str]:
    """Engagement ids listed in data/engagements.csv."""
    with path.open(newline="", encoding="utf-8") as f:
        return {row["engagement_id"].strip() for row in csv.DictReader(f)}


def _parse_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value) if len(value) == 10 else None
    except ValueError:
        return None


def validate_manifest(
    rows: list[ManifestRow],
    *,
    project_root: Path,
    corpus_dir: Path,
    engagement_ids: set[str],
    header: list[str] | None = None,
    skip_orphans_in: tuple[str, ...] = (),
    user_ids: set[str] | None = None,
) -> list[str]:
    """Return a list of problems found in the manifest (empty if valid).

    `skip_orphans_in` lists source types whose folders may hold files that this manifest
    does not list. The synthetic-only manifest uses ("standards",): the licensed ICAI PDFs
    may be present on the author's laptop but are deliberately not in that manifest."""
    errors: list[str] = []
    if header is not None:
        missing = [c for c in REQUIRED_COLUMNS if c not in header]
        if missing:
            errors.append(f"manifest is missing column(s): {', '.join(missing)}")

    by_id: dict[str, ManifestRow] = {}
    seen_paths: set[str] = set()
    superseded_by: dict[str, str] = {}

    for n, row in enumerate(rows, start=2):  # row 1 is the header
        where = f"row {n} ({row.doc_id or 'no doc_id'})"
        for fld in REQUIRED_FIELDS:
            if not getattr(row, fld):
                errors.append(f"{where}: missing {fld}")
        if row.doc_id:
            if row.doc_id in by_id:
                errors.append(f"{where}: duplicate doc_id")
            by_id[row.doc_id] = row
        if row.path:
            if row.path in seen_paths:
                errors.append(f"{where}: duplicate path {row.path}")
            seen_paths.add(row.path)
        if row.source_type and row.source_type not in SOURCE_TYPES:
            errors.append(f"{where}: unknown source_type '{row.source_type}'")
        if row.access_level and row.access_level not in ACCESS_LEVELS:
            errors.append(f"{where}: unknown access_level '{row.access_level}'")
        if row.synthetic and row.synthetic not in ("true", "false"):
            errors.append(f"{where}: synthetic must be 'true' or 'false', got '{row.synthetic}'")
        if row.effective_date and _parse_date(row.effective_date) is None:
            errors.append(f"{where}: bad effective_date '{row.effective_date}' (need YYYY-MM-DD)")

        # Engagement scoping
        if row.engagement_id and row.engagement_id not in engagement_ids:
            errors.append(f"{where}: unknown engagement_id '{row.engagement_id}'")
        if row.source_type == "engagement" and not row.engagement_id:
            errors.append(f"{where}: engagement document without engagement_id")
        if row.source_type and row.source_type != "engagement" and row.engagement_id:
            errors.append(f"{where}: only engagement documents may have an engagement_id")

        # Governance columns (optional; checked when filled in).
        if row.source_id and not _SOURCE_ID.match(row.source_id):
            errors.append(f"{where}: source_id '{row.source_id}' is not a valid id")
        if row.classification and row.classification not in CLASSIFICATIONS:
            errors.append(f"{where}: unknown classification '{row.classification}'")
        if row.owner and user_ids is not None and row.owner not in user_ids:
            errors.append(f"{where}: owner '{row.owner}' is not a known user")

        if row.source_type == "standards" and row.synthetic == "true":
            errors.append(f"{where}: ICAI standards must have synthetic=false")

        # File checks
        if row.path:
            file = project_root / row.path
            if not file.is_file():
                errors.append(f"{where}: file not found {row.path}")
            else:
                expected = corpus_dir / row.source_type
                if expected not in file.parents:
                    errors.append(f"{where}: file is not under data/corpus/{row.source_type}/")
                if row.is_synthetic:
                    first = file.read_text(encoding="utf-8").splitlines()[:1]
                    if first != [SYNTHETIC_BANNER]:
                        errors.append(f"{where}: synthetic file does not start with the banner")

        if row.supersedes:
            if row.supersedes in superseded_by:
                errors.append(f"{where}: {row.supersedes} is already superseded by "
                              f"{superseded_by[row.supersedes]}")
            superseded_by[row.supersedes] = row.doc_id

    # supersedes links need the full id map, so check them after the first pass.
    for row in rows:
        if not row.supersedes:
            continue
        where = f"{row.doc_id}"
        old = by_id.get(row.supersedes)
        if row.supersedes == row.doc_id:
            errors.append(f"{where}: supersedes itself")
        elif old is None:
            errors.append(f"{where}: supersedes unknown doc_id '{row.supersedes}'")
        else:
            if old.source_type != row.source_type:
                errors.append(f"{where}: supersedes a document of a different source_type")
            new_d, old_d = _parse_date(row.effective_date), _parse_date(old.effective_date)
            if new_d and old_d and new_d <= old_d:
                errors.append(f"{where}: effective_date is not later than superseded "
                              f"{old.doc_id} ({old.effective_date})")

    # Orphan files: present in the corpus but not in the manifest.
    if corpus_dir.is_dir():
        for file in sorted(corpus_dir.rglob("*")):
            if file.is_file() and file.suffix.lower() in DOCUMENT_SUFFIXES:
                if file.relative_to(corpus_dir).parts[0] in skip_orphans_in:
                    continue
                rel = file.relative_to(project_root).as_posix()
                if rel not in seen_paths:
                    errors.append(f"file not in manifest: {rel}")
    return errors


def licensed_corpus_present(corpus_dir: Path) -> bool:
    """True if the ICAI study material (not in git) is on this machine."""
    standards = corpus_dir / "standards"
    return standards.is_dir() and any(standards.rglob("*.pdf"))


def choose_manifest(full: Path, synthetic: Path | None, corpus_dir: Path) -> tuple[Path, bool]:
    """The manifest to index from, and whether it is the synthetic-only fallback.

    A clean clone has no ICAI PDFs (they are licensed and kept out of git). Instead of
    refusing the whole sync, the indexer then uses data/manifest.synthetic.csv, which
    lists only the committed synthetic documents."""
    if synthetic is not None and synthetic.is_file() and not licensed_corpus_present(corpus_dir):
        return synthetic, True
    return full, False


def synthetic_rows(rows: list[ManifestRow]) -> list[ManifestRow]:
    """The rows for committed synthetic documents (what manifest.synthetic.csv holds)."""
    return [r for r in rows if r.is_synthetic]


def superseded_ids(rows: list[ManifestRow]) -> set[str]:
    """doc_ids that have been superseded by a newer document."""
    return {r.supersedes for r in rows if r.supersedes}


def main() -> int:
    """Validate data/manifest.csv. Run: python -m app.ingest.manifest"""
    from app.config import PROJECT_ROOT

    data = PROJECT_ROOT / "data"
    header, rows = read_manifest(data / "manifest.csv")
    errors = validate_manifest(
        rows,
        project_root=PROJECT_ROOT,
        corpus_dir=data / "corpus",
        engagement_ids=load_engagement_ids(data / "engagements.csv"),
        header=header,
    )
    for e in errors:
        print(f"ERROR: {e}")
    print(f"{len(rows)} rows checked, {len(errors)} problem(s).")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
