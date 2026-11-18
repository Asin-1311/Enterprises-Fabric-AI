"""Shared helpers for scripts that import a folder of PDFs into the corpus
(scripts/add_ca_final.py, scripts/add_tax_law.py)."""

import csv
import shutil
from pathlib import Path

from app.config import PROJECT_ROOT
from app.ingest.manifest import MANIFEST_COLUMNS

MANIFEST = PROJECT_ROOT / "data" / "manifest.csv"


def copy_if_needed(src: Path, dest: Path) -> bool:
    """Copy `src` to `dest` unless an identical-size copy is already there."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size == src.stat().st_size:
        return False
    shutil.copy2(src, dest)
    return True


def replace_manifest_rows(prefix: str | tuple[str, ...], rows: list[dict[str, str]],
                          manifest: Path = MANIFEST) -> int:
    """Replace every manifest row whose doc_id starts with `prefix` (or any of several
    prefixes) by `rows`; all other rows are kept unchanged and in order. Returns the new
    total number of rows."""
    with manifest.open(newline="", encoding="utf-8") as fh:
        kept = [r for r in csv.DictReader(fh) if not r["doc_id"].startswith(prefix)]
    with manifest.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(MANIFEST_COLUMNS))
        w.writeheader()
        w.writerows([{c: r.get(c, "") for c in MANIFEST_COLUMNS} for r in kept])
        w.writerows(rows)
    return len(kept) + len(rows)
