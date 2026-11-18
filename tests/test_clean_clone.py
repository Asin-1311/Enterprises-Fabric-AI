"""The project works from a clean clone, where the licensed ICAI PDFs are absent.

Covers: R-ops-clone (local part; the fresh-clone CI job comes with M17).
"""

import logging
import shutil
from pathlib import Path

import pytest

from app.config import PROJECT_ROOT
from app.ingest.manifest import choose_manifest, read_manifest
from app.retrieval.acl import UserContext
from tests.helpers import make_indexer, make_searcher

DATA = PROJECT_ROOT / "data"


def _clean_clone(tmp_path: Path) -> Path:
    """Copy what git holds under data/ (everything except the licensed standards)."""
    root = tmp_path / "clone"
    (root / "data" / "corpus").mkdir(parents=True)
    for name in ("manifest.csv", "manifest.synthetic.csv", "users.csv", "engagements.csv"):
        shutil.copy2(DATA / name, root / "data" / name)
    for folder in (DATA / "corpus").iterdir():
        if folder.is_dir() and folder.name != "standards":
            shutil.copytree(folder, root / "data" / "corpus" / folder.name)
    return root


def test_clean_clone_indexes_the_synthetic_corpus_and_answers_a_search(tmp_path, caplog) -> None:
    """Covers: R-ops-clone. No ICAI PDFs: the sync must not refuse; it indexes the 40
    synthetic documents and warns clearly."""
    root = _clean_clone(tmp_path)
    assert not (root / "data" / "corpus" / "standards").exists()
    indexer = make_indexer(root, synthetic_fallback=True)

    caplog.set_level(logging.WARNING, logger="app.ingest.indexer")
    report = indexer.sync(actor="test")

    _, synthetic = read_manifest(DATA / "manifest.synthetic.csv")
    assert sorted(report.indexed) == sorted(r.doc_id for r in synthetic)
    assert "synthetic documents only" in caplog.text
    staff = UserContext("S01", "staff", frozenset({"ENG-NWT-FY26"}))
    hits = make_searcher(indexer).search("final audit file assembly days", staff, limit=5)
    assert hits and all(h.source_type != "standards" for h in hits)


def test_full_manifest_is_used_when_the_icai_pdfs_are_present(tmp_path) -> None:
    corpus = tmp_path / "data" / "corpus"
    full, synthetic = tmp_path / "data" / "manifest.csv", tmp_path / "data" / "manifest.synthetic.csv"
    (corpus / "firm_policy").mkdir(parents=True)
    full.write_text("x", encoding="utf-8")
    synthetic.write_text("x", encoding="utf-8")
    assert choose_manifest(full, synthetic, corpus) == (synthetic, True)  # no standards folder
    (corpus / "standards").mkdir()
    assert choose_manifest(full, synthetic, corpus) == (synthetic, True)  # folder but no PDFs
    (corpus / "standards" / "ch04.pdf").write_bytes(b"%PDF")
    assert choose_manifest(full, synthetic, corpus) == (full, False)
    assert choose_manifest(full, None, tmp_path / "empty") == (full, False)  # no fallback set


def test_without_a_fallback_the_full_manifest_still_refuses(tmp_path) -> None:
    """The fallback is opt-in: an indexer without it keeps the strict Phase 1 behaviour."""
    from app.ingest.indexer import ManifestError

    root = _clean_clone(tmp_path)
    with pytest.raises(ManifestError):
        make_indexer(root).sync(actor="test")
