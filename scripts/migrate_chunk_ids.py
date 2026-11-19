"""Convert the lite index from Phase 1 chunk ids to stable ids, reusing the stored vectors.

    .venv\\Scripts\\python.exe -m scripts.migrate_chunk_ids

M12b changes chunk ids from a running number ("STD-CH06:0004") to
`<doc_id>:<section>:<window>` ("STD-CH06:1.1:1"). This script converts an existing index:

1. Backs up data/fabric.db and data/qdrant to data/backups/<timestamp>-chunk-ids/
   (restore = stop everything, copy both back).
2. Runs a normal sync. Every document whose chunks predate M12b (no chunking profile) is
   re-chunked; each new chunk with the same text as an old one gets the old chunk's stored
   vector (no new embedding) and keeps the old id as `legacy_chunk_id`, so answers written
   before M12b still open their sources. Old ids are deleted from SQLite, Qdrant and BM25.
3. Checks the result: no Phase 1 ids left, every citation stored in the database still
   resolves, and SQLite and Qdrant hold the same number of chunks. Writes a JSON report
   next to the backup.

Resumable: the sync commits document by document, and a converted document is skipped the
next time. Lite profile only: for the full profile, erase its volumes and run
scripts/copy_lite_to_full.py after converting the lite index. Stop the API first:
embedded Qdrant allows one program at a time.
"""

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

from sqlalchemy import func, select

from app.config import get_settings
from app.db.models import Chunk, Citation
from app.ingest.indexer import ManifestError, _is_phase1_id, build_indexer
from app.retrieval.dense import QdrantInUseError, open_qdrant


def backup(sqlite_path: Path, qdrant_path: Path, root: Path) -> Path:
    """Copy the SQLite file and the Qdrant folder into a new timestamped folder."""
    target = root / f"{datetime.now():%Y%m%d-%H%M%S}-chunk-ids"
    target.mkdir(parents=True)
    shutil.copy2(sqlite_path, target / sqlite_path.name)
    shutil.copytree(qdrant_path, target / qdrant_path.name)
    return target


def check(indexer) -> dict:
    """Facts about the converted index (all counted, nothing estimated)."""
    with indexer.sessions() as s:
        ids = list(s.scalars(select(Chunk.chunk_id)))
        legacy = s.scalar(select(func.count()).select_from(Chunk)
                          .where(Chunk.legacy_chunk_id.is_not(None)))
        cited = set(s.scalars(select(Citation.chunk_id)))
        resolved = set(s.scalars(select(Chunk.chunk_id).where(Chunk.chunk_id.in_(cited)))) | \
            set(s.scalars(select(Chunk.legacy_chunk_id).where(Chunk.legacy_chunk_id.in_(cited))))
    return {
        "chunks_sqlite": len(ids),
        "points_qdrant": indexer.store.count(),
        "phase1_ids_left": sum(_is_phase1_id(c) for c in ids),
        "chunks_with_legacy_id": legacy,
        "distinct_cited_ids": len(cited),
        "cited_ids_unresolved": sorted(cited - resolved),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--no-backup", action="store_true",
                        help="skip the backup (only when re-running after a completed backup)")
    args = parser.parse_args(argv)
    settings = get_settings()
    if settings.profile != "lite":
        print("This converts the lite index only. For the full profile, erase its volumes and "
              "run scripts/copy_lite_to_full.py after converting the lite index.")
        return 1
    try:  # fail early, before copying anything, if the API or another script holds Qdrant
        open_qdrant(settings.qdrant_path).close()
    except QdrantInUseError as exc:
        print(exc)
        return 1

    report: dict = {"started_at": datetime.now().isoformat(timespec="seconds")}
    if not args.no_backup:
        folder = backup(settings.sqlite_path, settings.qdrant_path, settings.data_dir / "backups")
        report["backup"] = str(folder)
        print(f"Backup written to {folder}")
    else:
        folder = settings.data_dir / "backups"
        folder.mkdir(parents=True, exist_ok=True)

    indexer = build_indexer(settings)
    report["before"] = check(indexer)
    try:
        sync = indexer.sync(actor="migrate_chunk_ids")
    except ManifestError as exc:
        for e in exc.errors:
            print(f"MANIFEST ERROR: {e}")
        return 1
    report["sync"] = {"indexed": len(sync.indexed), "metadata_updated": len(sync.metadata_updated),
                      "unchanged": len(sync.unchanged), "removed": len(sync.removed),
                      "chunks": dict(sync.chunks)}
    report["after"] = after = check(indexer)
    # A citation that did not resolve before (its document changed in Phase 1) cannot be
    # repaired here; the conversion must not add any.
    newly_broken = set(after["cited_ids_unresolved"]) - set(report["before"]["cited_ids_unresolved"])
    report["cited_ids_newly_unresolved"] = sorted(newly_broken)
    ok = (after["phase1_ids_left"] == 0 and not newly_broken
          and after["chunks_sqlite"] == after["points_qdrant"])
    report["finished_at"] = datetime.now().isoformat(timespec="seconds")
    out = folder / "migration_report.json" if not args.no_backup else \
        folder / f"{datetime.now():%Y%m%d-%H%M%S}-chunk-ids-report.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("before", "sync", "after")}, indent=2))
    print(f"Report: {out}")

    if ok:
        print("Conversion complete.")
    else:
        print("Conversion finished WITH FINDINGS (see the report):")
        if after["phase1_ids_left"]:
            print(f"- {after['phase1_ids_left']} chunks still have Phase 1 ids: documents outside "
                  "the manifest (e.g. pushed by the webhook) convert when they next change.")
        if newly_broken:
            print(f"- {len(newly_broken)} cited ids no longer resolve: the stored text of those "
                  "chunks differs from what the chunker now produces, so they get no legacy id.")
        if after["chunks_sqlite"] != after["points_qdrant"]:
            print("- SQLite and Qdrant chunk counts differ.")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
