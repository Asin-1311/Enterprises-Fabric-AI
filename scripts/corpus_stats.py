"""Print corpus statistics and check the CLAUDE.md section 5 corpus minimums.

Run from the project root:
    .venv\\Scripts\\python.exe -m scripts.corpus_stats

Chunk counts are read from the SQLite `chunks` table; until M2 (chunking and indexing)
has run, they are reported as "n/a".
"""

import csv
from collections import Counter
from pathlib import Path

from sqlalchemy import func, inspect, select

from app.config import PROJECT_ROOT
from app.db.models import Chunk, Document
from app.db.session import make_engine, make_session_factory
from app.ingest.manifest import ACCESS_LEVELS, SOURCE_TYPES, ManifestRow, read_manifest
from app.ingest.pdf_loader import count_pdf_pages

DATA = PROJECT_ROOT / "data"
DB_PATH = DATA / "fabric.db"


def chunk_counts(db_path: Path) -> dict[str, int] | None:
    """Chunks per source type from SQLite, or None if nothing is indexed yet."""
    if not db_path.exists():
        return None
    engine = make_engine(f"sqlite:///{db_path.as_posix()}")
    if "chunks" not in inspect(engine).get_table_names():
        return None
    with make_session_factory(engine)() as s:
        stmt = (
            select(Document.source_type, func.count(Chunk.chunk_id))
            .join(Chunk, Chunk.doc_id == Document.doc_id)
            .group_by(Document.source_type)
        )
        counts = dict(s.execute(stmt).all())
    return counts or None


def minimum_checks(rows: list[ManifestRow]) -> list[tuple[str, bool, str]]:
    """(requirement, passed, detail) for each corpus minimum in CLAUDE.md section 5."""
    pairs = [r for r in rows if r.supersedes and r.source_type in ("firm_policy", "circulars")]
    restricted = [r for r in rows if r.access_level in ("manager", "partner")]
    per_client = Counter(r.engagement_id for r in rows if r.engagement_id)
    with (DATA / "users.csv").open(newline="", encoding="utf-8") as f:
        roles = Counter(u["role"] for u in csv.DictReader(f))
    with (DATA / "engagements.csv").open(newline="", encoding="utf-8") as f:
        engagements = [e["engagement_id"] for e in csv.DictReader(f)]
    return [
        (">= 5 superseded pairs in firm_policy/circulars", len(pairs) >= 5, str(len(pairs))),
        (">= 10 manager- or partner-only documents", len(restricted) >= 10, str(len(restricted))),
        (">= 3 engagement documents per client",
         all(per_client[e] >= 3 for e in engagements),
         ", ".join(f"{e}={per_client[e]}" for e in engagements)),
        (">= 2 users per role", all(roles[r] >= 2 for r in ACCESS_LEVELS),
         ", ".join(f"{r}={roles[r]}" for r in ACCESS_LEVELS)),
        (">= 3 fictional clients", len(engagements) >= 3, str(len(engagements))),
    ]


def main() -> int:
    """Print the statistics tables and the minimum checks."""
    _, rows = read_manifest(DATA / "manifest.csv")
    chunks = chunk_counts(DB_PATH)

    docs = Counter(r.source_type for r in rows)
    by_access = Counter((r.source_type, r.access_level) for r in rows)
    pages: Counter[str] = Counter()
    for r in rows:
        if r.path.endswith(".pdf"):
            pages[r.source_type] += count_pdf_pages(PROJECT_ROOT / r.path)

    head = f"{'source_type':<12} {'docs':>5} {'pages':>6} {'chunks':>7}  " + "  ".join(
        f"{a:>7}" for a in ACCESS_LEVELS
    )
    print(head)
    print("-" * len(head))
    for st in SOURCE_TYPES:
        n_chunks = "n/a" if chunks is None else str(chunks.get(st, 0))
        n_pages = str(pages[st]) if pages[st] else "-"
        access = "  ".join(f"{by_access[(st, a)]:>7}" for a in ACCESS_LEVELS)
        print(f"{st:<12} {docs[st]:>5} {n_pages:>6} {n_chunks:>7}  {access}")
    total_chunks = "n/a" if chunks is None else str(sum(chunks.values()))
    totals = "  ".join(f"{sum(1 for r in rows if r.access_level == a):>7}" for a in ACCESS_LEVELS)
    print("-" * len(head))
    print(f"{'TOTAL':<12} {len(rows):>5} {sum(pages.values()):>6} {total_chunks:>7}  {totals}")
    note = "chunks: n/a = not indexed yet (run python -m app.ingest.indexer)" if chunks is None \
        else "chunks: from the SQLite index"
    print(f"(pages: PDF pages only; Markdown has no pages. {note}.)")

    print("\nCorpus minimums (CLAUDE.md section 5):")
    ok = True
    for name, passed, detail in minimum_checks(rows):
        ok &= passed
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}: {detail}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
