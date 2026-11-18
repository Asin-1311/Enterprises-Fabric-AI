"""Tests for manifest validation (app/ingest/manifest.py)."""

from dataclasses import replace
from pathlib import Path

import pytest

from app.ingest.manifest import ManifestRow, validate_manifest
from app.ingest.md_loader import SYNTHETIC_BANNER

ENGAGEMENTS = {"ENG-A"}


def _row(**kw: str) -> ManifestRow:
    base = dict(
        doc_id="POL-1", path="data/corpus/firm_policy/p1.md", source_type="firm_policy",
        title="Policy", version="1.0", effective_date="2024-04-01", supersedes="",
        access_level="staff", engagement_id="", synthetic="true",
    )
    base.update(kw)
    return ManifestRow(**base)


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    """A tiny valid corpus: two policies (v2 supersedes v1) and one engagement file."""
    for rel in ("firm_policy/p1.md", "firm_policy/p2.md", "engagement/a/plan.md"):
        f = tmp_path / "data" / "corpus" / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(f"{SYNTHETIC_BANNER}\n\n# Doc\n", encoding="utf-8")
    return tmp_path


def _valid_rows() -> list[ManifestRow]:
    return [
        _row(),
        _row(doc_id="POL-2", path="data/corpus/firm_policy/p2.md", version="2.0",
             effective_date="2025-04-01", supersedes="POL-1"),
        _row(doc_id="ENG-1", path="data/corpus/engagement/a/plan.md",
             source_type="engagement", engagement_id="ENG-A"),
    ]


def _validate(rows: list[ManifestRow], root: Path) -> list[str]:
    return validate_manifest(rows, project_root=root, corpus_dir=root / "data" / "corpus",
                             engagement_ids=ENGAGEMENTS)


def test_valid_manifest_has_no_errors(corpus: Path) -> None:
    assert _validate(_valid_rows(), corpus) == []


@pytest.mark.parametrize(
    ("index", "changes", "expected"),
    [
        (0, {"title": ""}, "missing title"),
        (0, {"effective_date": "2024-13-01"}, "bad effective_date"),
        (0, {"effective_date": "01/04/2024"}, "bad effective_date"),
        (0, {"access_level": "intern"}, "unknown access_level"),
        (0, {"source_type": "memos"}, "unknown source_type"),
        (0, {"synthetic": "yes"}, "synthetic must be"),
        (0, {"synthetic": ""}, "missing synthetic"),
        (1, {"supersedes": "POL-404"}, "supersedes unknown doc_id"),
        (1, {"supersedes": "POL-2"}, "supersedes itself"),
        (1, {"effective_date": "2023-01-01"}, "not later than superseded"),
        (2, {"engagement_id": "ENG-ZZZ"}, "unknown engagement_id"),
        (2, {"engagement_id": ""}, "engagement document without engagement_id"),
        (0, {"engagement_id": "ENG-A"}, "only engagement documents"),
        (0, {"path": "data/corpus/firm_policy/missing.md"}, "file not found"),
        (1, {"doc_id": "POL-1"}, "duplicate doc_id"),
    ],
)
def test_each_problem_is_reported(
    corpus: Path, index: int, changes: dict[str, str], expected: str
) -> None:
    rows = _valid_rows()
    rows[index] = replace(rows[index], **changes)
    errors = _validate(rows, corpus)
    assert any(expected in e for e in errors), errors


def test_missing_banner_reported(corpus: Path) -> None:
    (corpus / "data/corpus/firm_policy/p1.md").write_text("# no banner\n", encoding="utf-8")
    assert any("banner" in e for e in _validate(_valid_rows(), corpus))


def test_wrong_folder_reported(corpus: Path) -> None:
    rows = _valid_rows()
    rows[0] = replace(rows[0], source_type="circulars")
    assert any("not under data/corpus/circulars" in e for e in _validate(rows, corpus))


def test_orphan_file_reported(corpus: Path) -> None:
    (corpus / "data/corpus/firm_policy/extra.md").write_text("x", encoding="utf-8")
    assert any("file not in manifest" in e for e in _validate(_valid_rows(), corpus))


def test_missing_column_reported(corpus: Path) -> None:
    errors = validate_manifest(
        _valid_rows(), project_root=corpus, corpus_dir=corpus / "data" / "corpus",
        engagement_ids=ENGAGEMENTS, header=["doc_id", "path"],
    )
    assert any("missing column" in e for e in errors)
