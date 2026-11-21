"""M19: the requirements, architecture, API reference and Section 15 documents exist, are
linked from the README, and the generated ones are up to date.

Covers: R-15-req (use cases, every registered source, measurable targets), R-15-docs
(architecture, API reference and operations guide linked from the README),
R-ops-integrity (the Section 15 table is generated from COMPLIANCE.md, not hand-written).
"""

import csv

from app.config import PROJECT_ROOT
from scripts.make_docs import main as make_docs_main

DOCS = PROJECT_ROOT / "docs"


def test_requirements_list_use_cases_every_source_and_the_targets() -> None:
    """Covers: R-15-req."""
    text = (DOCS / "REQUIREMENTS.md").read_text(encoding="utf-8")
    assert "## 1. Users and use cases" in text and text.count("| U") >= 8
    manifest_sources = {r["source_id"] for r in csv.DictReader(
        (PROJECT_ROOT / "data" / "manifest.csv").open(encoding="utf-8"))}
    for source in manifest_sources | {"git-firm-methodology", "s3-firm-documents", "conf-aud", "jira-audq",
                                      "sp-policies"}:
        assert f"| {source} |" in text, source
    for target in ("< 1.5 s", "< 8 s", "kappa >= 0.6", "0.02", "60 s"):
        assert target in text, target


def test_architecture_api_reference_and_operations_are_linked_from_the_readme() -> None:
    """Covers: R-15-docs."""
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    for doc in ("docs/ARCHITECTURE.md", "docs/api/REFERENCE.md", "docs/OPERATIONS.md", "docs/REQUIREMENTS.md"):
        assert f"]({doc})" in readme, doc
        assert (PROJECT_ROOT / doc).is_file(), doc
    arch = (DOCS / "ARCHITECTURE.md").read_text(encoding="utf-8")
    for plane in ("Ingestion plane", "Agent plane", "Adaptive controller", "Serving plane", "Experience plane"):
        assert plane in arch, plane
    assert "```mermaid" in arch


def test_generated_documents_are_up_to_date() -> None:
    """Covers: R-ops-integrity (SECTION15.md is generated from COMPLIANCE.md; the API
    reference from the OpenAPI schema; neither may be edited by hand)."""
    assert make_docs_main(["--check"]) == 0
    section15 = (DOCS / "report" / "SECTION15.md").read_text(encoding="utf-8")
    assert "do not edit" in section15 and "| Completed |" not in section15  # real statuses only
