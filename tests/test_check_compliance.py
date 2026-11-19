"""scripts/check_compliance.py: a requirement is Met only with a named, covering, unskipped test."""

from pathlib import Path

from app.config import PROJECT_ROOT
from scripts.check_compliance import COMPLIANCE, check

TESTS = '''import pytest


def test_good() -> None:
    """Covers: R-1-a."""


def test_wrong_id() -> None:
    """Covers: R-1-ab, R-1-a-extra."""


@pytest.mark.skipif(True, reason="not here")
def test_skipped() -> None:
    """Covers: R-1-a."""
'''


def _doc(rows: list[tuple[str, str, str]], summary: str | None = None) -> str:
    met = sum(s == "Met" for _, s, _ in rows)
    partial = sum(s == "Partial" for _, s, _ in rows)
    not_met = sum(s == "Not met" for _, s, _ in rows)
    summary = summary or (f"**Summary: {len(rows)} requirements: {met} Met, {partial} Partial, "
                          f"{not_met} Not met, 0 Deviation.**")
    table = ["| ID | Spec § | Requirement | Status | Evidence | Acceptance test | Milestone | Notes |",
             "|---|---|---|---|---|---|---|---|"]
    table += [f"| {rid} | 1 | r | {status} | e | {test} | M9 | |" for rid, status, test in rows]
    return summary + "\n\n" + "\n".join(table) + "\n"


def _root(tmp_path: Path) -> Path:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text(TESTS, encoding="utf-8")
    return tmp_path


def test_the_real_compliance_file_passes() -> None:
    assert check(COMPLIANCE.read_text(encoding="utf-8"), PROJECT_ROOT) == []


def test_a_met_row_with_a_covering_test_passes(tmp_path) -> None:
    doc = _doc([("R-1-a", "Met", "`tests/test_x.py::test_good`"), ("R-1-b", "Partial", "later")])
    assert check(doc, _root(tmp_path)) == []


def test_met_needs_an_existing_test_that_names_the_exact_id(tmp_path) -> None:
    root = _root(tmp_path)
    cases = {
        "`tests/test_x.py::test_wrong_id`": "does not say 'Covers: R-1-a'",  # R-1-ab is not R-1-a
        "`tests/test_x.py::test_missing`": "not found",
        "`tests/test_y.py::test_good`": "not found",
        "the gate fails on a regression": "not given as tests/",
        "`tests/test_x.py::test_skipped`": "marked to be skipped",
    }
    for test_ref, message in cases.items():
        problems = check(_doc([("R-1-a", "Met", test_ref)]), root)
        assert any(message in p for p in problems), (test_ref, problems)


def test_unknown_status_duplicates_and_a_stale_summary_are_caught(tmp_path) -> None:
    root = _root(tmp_path)
    problems = check(_doc([("R-1-a", "Done", "x"), ("R-1-a", "Partial", "x")]), root)
    assert any("unknown status 'Done'" in p for p in problems)
    assert any("listed twice" in p for p in problems)
    stale = _doc([("R-1-a", "Partial", "x")], summary="**Summary: 1 requirements: 1 Met, 0 Partial, "
                                                      "0 Not met, 0 Deviation.**")
    assert any("summary line" in p for p in check(stale, root))
