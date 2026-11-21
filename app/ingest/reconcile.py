"""Nightly reconciliation: find and repair drift between the metadata store and the two
indexes (CLAUDE.md section 6, "compares every source with the indexes and repairs drift";
M12d). Scheduled by flows/serve.py; also runnable by hand:

    .venv\\Scripts\\python.exe -m app.ingest.reconcile [--check-only]

1. Sources: a manifest sync brings the corpus folders in line (added, changed and removed
   files). Polling connectors are compared with their sources every 30 s by the
   connector runner, and the DMS streams its changes, so they need no extra step here.
2. Indexes: for every document, the chunk ids in the metadata store, the vector index and
   the keyword index must be the same three sets.
   - A document whose sets differ is rebuilt (`Indexer.repair_document`).
   - Index entries of a document the metadata store does not know are deleted.
3. The result is printed as JSON and written to audit_log (`reconcile`).

Drift should never happen, because one worker applies every change to all three stores
from the same plan. This job is the check that it did not (a crash between two writes, a
restored backup, a manual edit), and the repair if it did.

In lite, embedded Qdrant allows one program at a time: run this while the API is stopped
(it says so if the index is in use). In the full profile it runs alongside everything.
"""

import argparse
import json
import logging
import sys
from dataclasses import asdict, dataclass, field

from sqlalchemy import select

from app.db.models import Chunk, Document
from app.db.session import write_audit_log
from app.ingest.indexer import Indexer, ManifestError

log = logging.getLogger(__name__)


@dataclass
class ReconcileReport:
    documents_checked: int = 0
    drifted: list[str] = field(default_factory=list)       # repaired (or, check-only, found)
    orphans_removed: list[str] = field(default_factory=list)  # in an index, not in the store
    repair_failed: dict[str, str] = field(default_factory=dict)
    sync: dict[str, int] = field(default_factory=dict)


def find_drift(indexer: Indexer) -> tuple[list[str], list[str], int]:
    """(documents whose chunk ids differ between the stores, documents only in an index,
    number of documents in the metadata store)."""
    stored: dict[str, set[str]] = {}
    with indexer.sessions() as s:
        for chunk_id, doc_id in s.execute(select(Chunk.chunk_id, Chunk.doc_id)):
            stored.setdefault(doc_id, set()).add(chunk_id)
        docs = set(s.scalars(select(Document.doc_id)))
    vector, keyword = indexer.store.index_chunk_ids(), indexer.bm25.index_chunk_ids()
    drifted = sorted(d for d in docs
                     if not (stored.get(d, set()) == vector.get(d, set()) == keyword.get(d, set())))
    orphans = sorted((vector.keys() | keyword.keys()) - docs)
    return drifted, orphans, len(docs)


def reconcile(indexer: Indexer, repair: bool = True, actor: str = "reconcile") -> ReconcileReport:
    report = ReconcileReport()
    if repair:
        try:
            result = indexer.sync(actor=actor)
            report.sync = {"indexed": len(result.indexed), "metadata_updated": len(result.metadata_updated),
                           "unchanged": len(result.unchanged), "removed": len(result.removed)}
        except ManifestError as exc:  # an invalid manifest is refused, not half-applied
            report.repair_failed["manifest"] = str(exc)[:500]
    drifted, orphans, report.documents_checked = find_drift(indexer)
    report.drifted = drifted
    if repair:
        for doc_id in orphans:
            indexer.store.delete_document(doc_id)
            indexer.bm25.delete_document(doc_id)
            report.orphans_removed.append(doc_id)
        for doc_id in drifted:
            try:
                indexer.repair_document(doc_id, actor=actor)
            except Exception as exc:  # noqa: BLE001 - reported, the others are still repaired
                report.repair_failed[doc_id] = f"{type(exc).__name__}: {exc}"[:300]
        indexer.rebuild_bm25()
    else:
        report.orphans_removed = []
    with indexer.sessions() as s:
        write_audit_log(s, actor, "reconcile",
                        f"{report.documents_checked} checked, {len(report.drifted)} drifted, "
                        f"{len(orphans)} orphaned, {len(report.repair_failed)} failed"
                        + ("" if repair else " (check only)"))
    return report


def main(argv: list[str] | None = None) -> int:
    from app.config import get_settings
    from app.ingest.indexer import build_indexer
    from app.retrieval.dense import QdrantInUseError

    parser = argparse.ArgumentParser(description="Find and repair index drift.")
    parser.add_argument("--check-only", action="store_true", help="report, do not repair")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        indexer = build_indexer(get_settings())
    except QdrantInUseError as exc:
        print(exc)
        return 2
    report = reconcile(indexer, repair=not args.check_only)
    print(json.dumps(asdict(report), indent=2))
    return 1 if report.repair_failed else 0


if __name__ == "__main__":
    sys.exit(main())
