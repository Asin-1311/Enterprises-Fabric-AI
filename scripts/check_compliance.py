"""Check docs/report/COMPLIANCE.md against the tests (CLAUDE.md section 16).

    .venv\\Scripts\\python.exe -m scripts.check_compliance          # static checks
    .venv\\Scripts\\python.exe -m scripts.check_compliance --run    # also run the Met tests

Fails (exit code 1) when:
- a row has an unknown status, or an ID appears twice;
- the summary line does not match the rows;
- a row marked Met does not name its acceptance test as `tests/<file>.py::<test>`, the
  test does not exist, or its docstring does not say `Covers: <ID>`;
- for the UI (M16b), the acceptance test may be a Vitest test, named
  `frontend/src/<file>.test.tsx::<test title>`; its body must have a `// Covers: <ID>` line;
- the Met test is marked to be skipped (statically);
- with --run: a Met test fails or is skipped when pytest (or Vitest) runs it.
"""

import argparse
import ast
import json
import re
import shutil
import tempfile
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from app.config import PROJECT_ROOT

COMPLIANCE = PROJECT_ROOT / "docs" / "report" / "COMPLIANCE.md"
STATUSES = ("Met", "Partial", "Not met", "Deviation")
_TEST_ID = re.compile(r"`?(tests/[\w/]+\.py)::(\w+)`?")
_VITEST_ID = re.compile(r"`?(frontend/src/[\w/.-]+\.test\.tsx?)::([^`|]+?)`?")
_SUMMARY = re.compile(r"\*\*Summary: (\d+) requirements: (\d+) Met, (\d+) Partial, "
                      r"(\d+) Not met, (\d+) Deviation\.\*\*")


@dataclass
class Row:
    id: str
    status: str
    acceptance_test: str


def read_rows(text: str) -> list[Row]:
    """Rows of the requirement table (the one whose header starts with `| ID |`)."""
    rows, in_table = [], False
    for line in text.splitlines():
        if line.startswith("| ID |"):
            in_table = True
            continue
        if in_table:
            if not line.startswith("|"):
                break
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if set(cells[0]) <= {"-"}:  # the |---| line
                continue
            rows.append(Row(id=cells[0], status=cells[3], acceptance_test=cells[5]))
    return rows


def _covers(docstring: str | None, req_id: str) -> bool:
    """Does a `Covers:` line in the docstring name this requirement exactly?"""
    for line in (docstring or "").splitlines():
        if "Covers:" in line and re.search(rf"(?<![\w.-]){re.escape(req_id)}(?![\w-])", line):
            return True
    return False


def _is_skip_marker(node: ast.expr) -> bool:
    text = ast.unparse(node)
    return "mark.skip" in text  # skip and skipif


def _vitest_block(source: str, title: str) -> str | None:
    """The source of the Vitest test called `title`, up to the next top-level test."""
    start = source.find(f'test("{title}"')
    if start < 0:
        return None
    following = re.search(r"^test(\.\w+)?\(", source[start + 1:], re.M)
    return source[start:start + 1 + following.start()] if following else source[start:]


def check_vitest(root: Path, test_ref: str, req_id: str) -> list[str]:
    """Problems with a Vitest acceptance test (empty if fine)."""
    m = _VITEST_ID.fullmatch(test_ref.strip())
    path, title = root / m.group(1), m.group(2).strip()
    if not path.is_file():
        return [f"{req_id}: test file {m.group(1)} not found"]
    source = path.read_text(encoding="utf-8")
    if any(f'test.{k}("{title}"' in source for k in ("skip", "todo")):
        return [f"{req_id}: {test_ref} is marked to be skipped"]
    block = _vitest_block(source, title)
    if block is None:
        return [f"{req_id}: test '{title}' not found in {m.group(1)}"]
    if not _covers(block, req_id):
        return [f"{req_id}: {test_ref} does not say '// Covers: {req_id}'"]
    return []


def check_test(root: Path, test_ref: str, req_id: str) -> list[str]:
    """Problems with the named acceptance test of a Met row (empty if fine)."""
    if _VITEST_ID.fullmatch(test_ref.strip()):
        return check_vitest(root, test_ref, req_id)
    m = _TEST_ID.fullmatch(test_ref.strip())
    if not m:
        return [f"{req_id}: Met, but the acceptance test is not given as tests/<file>.py::<test>"]
    path, name = root / m.group(1), m.group(2)
    if not path.is_file():
        return [f"{req_id}: test file {m.group(1)} not found"]
    tree = ast.parse(path.read_text(encoding="utf-8"))
    module_skip = any(isinstance(n, ast.Assign) and any(
        isinstance(t, ast.Name) and t.id == "pytestmark" for t in n.targets)
        and _is_skip_marker(n.value) for n in tree.body)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            problems = []
            if not _covers(ast.get_docstring(node), req_id):
                problems.append(f"{req_id}: {test_ref} does not say 'Covers: {req_id}'")
            if module_skip or any(_is_skip_marker(d) for d in node.decorator_list):
                problems.append(f"{req_id}: {test_ref} is marked to be skipped")
            return problems
    return [f"{req_id}: test {name} not found in {m.group(1)}"]


def check(text: str, root: Path = PROJECT_ROOT) -> list[str]:
    """All static problems in a COMPLIANCE.md text (empty list = fine)."""
    rows = read_rows(text)
    problems = []
    if not rows:
        return ["no requirement table found"]
    seen = set()
    for row in rows:
        if row.id in seen:
            problems.append(f"{row.id}: listed twice")
        seen.add(row.id)
        if row.status not in STATUSES:
            problems.append(f"{row.id}: unknown status '{row.status}'")
        if row.status == "Met":
            problems += check_test(root, row.acceptance_test, row.id)
    counts = [sum(r.status == s for r in rows) for s in STATUSES]
    expected = f"**Summary: {len(rows)} requirements: {counts[0]} Met, {counts[1]} Partial, " \
               f"{counts[2]} Not met, {counts[3]} Deviation.**"
    found = _SUMMARY.search(text)
    if not found or found.group(0) != expected:
        problems.append(f"summary line is wrong or missing; it should read: {expected}")
    return problems


def is_integration(test_ref: str) -> bool:
    """Integration tests (tests/integration/) need the full-profile servers; pytest.ini
    deselects them unless `-m integration` is given."""
    return test_ref.strip("`").startswith("tests/integration/")


def _run(refs: list[str], marker: str, kind: str) -> list[str]:
    result = subprocess.run([sys.executable, "-m", "pytest", "-q", "-rs", "-p", "no:cacheprovider",
                             "-m", marker, *refs], cwd=PROJECT_ROOT, capture_output=True, text=True)
    problems = []
    if result.returncode != 0:
        problems.append(f"a Met {kind} acceptance test failed:\n" + result.stdout[-2000:])
    if re.search(r"\bskipped\b", result.stdout):
        problems.append(f"a Met {kind} acceptance test was skipped:\n" + result.stdout[-2000:])
    return problems


def _run_vitest(refs: list[str]) -> list[str]:
    """Run the Vitest files of these refs (JSON report); each named test must pass."""
    npx = shutil.which("npx")
    if npx is None:
        return ["a Met test is a Vitest test, but npx (Node.js) is not installed"]
    named = [_VITEST_ID.fullmatch(r) for r in refs]
    files = sorted({m.group(1).removeprefix("frontend/") for m in named})
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "vitest.json"
        subprocess.run([npx, "vitest", "run", *files, "--reporter=json", f"--outputFile={report}"],
                       cwd=PROJECT_ROOT / "frontend", capture_output=True, text=True)
        if not report.is_file():
            return ["Vitest did not run (no report written)"]
        results = json.loads(report.read_text(encoding="utf-8"))
    status = {a["title"]: a["status"] for f in results.get("testResults", [])
              for a in f.get("assertionResults", [])}
    return [f"a Met Vitest acceptance test did not pass ({status.get(m.group(2).strip(), 'not run')}): "
            f"{m.group(0)}" for m in named if status.get(m.group(2).strip()) != "passed"]


def run_met_tests(text: str, include_integration: bool = False) -> list[str]:
    """Run every Met row's acceptance test with pytest; fail on failures or skips.
    Integration acceptance tests run only with `include_integration` (they need the
    servers); otherwise they are listed, so a Met row never passes unseen."""
    refs = [r.acceptance_test.strip("`") for r in read_rows(text) if r.status == "Met"]
    vitest = [r for r in refs if _VITEST_ID.fullmatch(r)]
    refs = [r for r in refs if r not in vitest]
    lite = [r for r in refs if not is_integration(r)]
    integration = [r for r in refs if is_integration(r)]
    problems = _run(lite, "not integration", "lite") if lite else []
    problems += _run_vitest(vitest) if vitest else []
    if integration and include_integration:
        problems += _run(integration, "integration", "integration")
    elif integration:
        print(f"NOT RUN (need the servers; use --run-integration): {', '.join(integration)}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="Check COMPLIANCE.md against the tests.")
    parser.add_argument("--run", action="store_true", help="also run the Met acceptance tests")
    parser.add_argument("--run-integration", action="store_true",
                        help="with --run: also run the integration acceptance tests (servers up)")
    args = parser.parse_args()
    text = COMPLIANCE.read_text(encoding="utf-8")
    problems = check(text) + (run_met_tests(text, args.run_integration) if args.run else [])
    for p in problems:
        print("PROBLEM:", p)
    rows = read_rows(text)
    print(f"{len(rows)} requirements: " + ", ".join(
        f"{sum(r.status == s for r in rows)} {s}" for s in STATUSES))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
