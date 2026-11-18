"""Checks on the generated synthetic corpus (scripts/make_synthetic_docs.py)."""

import re

import pytest

from app.config import PROJECT_ROOT
from app.ingest.manifest import (
    licensed_corpus_present,
    load_engagement_ids,
    read_manifest,
    synthetic_rows,
    validate_manifest,
)
from scripts.make_synthetic_docs import all_synthetic_docs, manifest_rows
from scripts.synthetic import FIRM
from scripts.synthetic.people import ENGAGEMENTS, USERS

DATA = PROJECT_ROOT / "data"
PAN = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")


def test_committed_manifest_is_valid() -> None:
    """Covers: R-ops-clone. The synthetic manifest lists only committed files, so it is
    valid in a clean clone (the licensed ICAI PDFs are not in git)."""
    header, rows = read_manifest(DATA / "manifest.synthetic.csv")
    errors = validate_manifest(
        rows, project_root=PROJECT_ROOT, corpus_dir=DATA / "corpus",
        engagement_ids=load_engagement_ids(DATA / "engagements.csv"), header=header,
        skip_orphans_in=("standards",),
    )
    assert errors == []
    assert rows and all(r.is_synthetic and r.source_type != "standards" for r in rows)


def test_synthetic_manifest_matches_the_full_manifest() -> None:
    """manifest.synthetic.csv is exactly the synthetic rows of manifest.csv (no drift)."""
    _, full = read_manifest(DATA / "manifest.csv")
    _, synthetic = read_manifest(DATA / "manifest.synthetic.csv")
    assert synthetic == synthetic_rows(full)


@pytest.mark.skipif(not licensed_corpus_present(DATA / "corpus"),
                    reason="ICAI PDFs are licensed and not in git")
def test_full_manifest_is_valid() -> None:
    header, rows = read_manifest(DATA / "manifest.csv")
    errors = validate_manifest(
        rows, project_root=PROJECT_ROOT, corpus_dir=DATA / "corpus",
        engagement_ids=load_engagement_ids(DATA / "engagements.csv"), header=header,
    )
    assert errors == []


def test_manifest_on_disk_matches_generator() -> None:
    """Every generated row is on disk, unchanged and first (no drift). Extra rows are
    documents the author added by hand; test_committed_manifest_is_valid checks them."""
    _, rows = read_manifest(DATA / "manifest.csv")
    generated = manifest_rows(all_synthetic_docs())
    assert [r.__dict__ for r in rows[: len(generated)]] == generated


def test_generator_keeps_author_added_rows(tmp_path, monkeypatch) -> None:
    import scripts.make_synthetic_docs as gen

    data = tmp_path / "data"
    data.mkdir()
    extra = {"doc_id": "MY-DOC-01", "path": "data/corpus/faq/mine.md", "source_type": "faq",
             "title": "Mine", "version": "1", "effective_date": "2026-11-08", "supersedes": "",
             "access_level": "staff", "engagement_id": "", "synthetic": "true"}
    generated = gen.manifest_rows(all_synthetic_docs())
    gen.write_csv(data / "manifest.csv", gen.MANIFEST_COLUMNS, [*generated, extra])
    monkeypatch.setattr(gen, "DATA", data)
    assert gen.author_rows(generated) == [extra]


def test_fictional_firm_and_fake_identifiers_only() -> None:
    for doc in all_synthetic_docs():
        assert FIRM in doc.body, doc.doc_id
        for pan in PAN.findall(doc.body):
            assert pan.startswith("ZZZZ"), f"{doc.doc_id}: PAN-like value {pan} is not obviously fake"
        for email in re.findall(r"[\w.]+@[\w.-]+", doc.body):
            assert email.endswith(".example"), f"{doc.doc_id}: {email} is not a .example address"


def test_engagement_teams_reference_known_users() -> None:
    user_ids = {u[0] for u in USERS}
    for _, _, _, _, partner, members in ENGAGEMENTS:
        assert partner in user_ids
        assert set(members) <= user_ids


def test_isolation_design_for_tests() -> None:
    """S01 and M01 are only on Northwind; M02 is a manager NOT on Northwind."""
    teams = {e[0]: set(e[5]) for e in ENGAGEMENTS}
    assert {"S01", "M01"} <= teams["ENG-NWT-FY26"]
    assert not ({"S01", "M01"} & (teams["ENG-MBL-FY26"] | teams["ENG-BRT-FY26"]))
    assert "M02" not in teams["ENG-NWT-FY26"]
