"""Generate the documents that must never be hand-written (M19).

    python -m scripts.make_docs          # write both
    python -m scripts.make_docs --check  # fail if either is out of date (tests, CI)

- docs/api/REFERENCE.md from docs/api/openapi.json (scripts/export_openapi.py): every
  endpoint with its method, purpose, request body and responses.
- docs/report/SECTION15.md from docs/report/COMPLIANCE.md: the replacement for the
  project document's Section 15 table, with the real status of every requirement,
  grouped by specification section. The PDF's table is to be replaced by this one; it
  is never written by hand (CLAUDE.md sections 0 and 16).
"""

import json
import re
import sys
from collections import Counter, defaultdict

from app.config import PROJECT_ROOT

OPENAPI = PROJECT_ROOT / "docs" / "api" / "openapi.json"
REFERENCE = PROJECT_ROOT / "docs" / "api" / "REFERENCE.md"
COMPLIANCE = PROJECT_ROOT / "docs" / "report" / "COMPLIANCE.md"
SECTION15 = PROJECT_ROOT / "docs" / "report" / "SECTION15.md"


def _ref_name(schema: dict | None) -> str:
    if not schema:
        return ""
    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[-1]
    if schema.get("type") == "array" and "items" in schema:
        return f"list of {_ref_name(schema['items'])}"
    return schema.get("type", "")


def api_reference(spec: dict) -> str:
    lines = [f"# API reference: {spec['info']['title']} {spec['info'].get('version', '')}".rstrip(), "",
             "Generated from `docs/api/openapi.json` by `python -m scripts.make_docs`; do not edit.",
             "The contract tests check every endpoint against that schema. All endpoints need a bearer",
             "token except `/health`, `/metrics` and the lite sign-in routes.", "",
             "| Method | Path | Purpose | Request body | Responses |", "|---|---|---|---|---|"]
    for path, ops in sorted(spec["paths"].items()):
        for method, op in sorted(ops.items()):
            if method not in {"get", "post", "put", "delete", "patch"}:
                continue
            purpose = (op.get("summary") or op.get("description") or "").strip().splitlines()[0:1]
            body = op.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema")
            codes = ", ".join(sorted(op.get("responses", {})))
            lines.append(f"| {method.upper()} | `{path}` | {(purpose[0] if purpose else '').replace('|', '/')} | "
                         f"{_ref_name(body)} | {codes} |")
    return "\n".join(lines) + "\n"


def compliance_rows() -> list[list[str]]:
    rows, in_table = [], False
    for line in COMPLIANCE.read_text(encoding="utf-8").splitlines():
        if line.startswith("| ID |"):
            in_table = True
            continue
        if in_table:
            if not line.startswith("|"):
                break
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if set(cells[0]) <= {"-"}:
                continue
            rows.append(cells)
    return rows


# How a note in COMPLIANCE.md introduces what is still open.
OPEN_MARKERS = ("Still missing", "Remaining for Met", "Not yet:")


def sentences(text: str) -> list[str]:
    """The text's sentences. A full stop inside brackets does not end one."""
    parts, start = [], 0
    for gap in re.finditer(r"(?<=[.!?])\s+(?=[\"'`(]?[A-Z0-9])", text):
        piece = text[start:gap.start()]
        if piece.count("(") <= piece.count(")"):
            parts.append(piece.strip())
            start = gap.end()
    parts.append(text[start:].strip())
    return [p for p in parts if p]


def summarise_note(note: str, tail: int = 400, limit: int = 600) -> str:
    """Why a requirement is open, in whole sentences: from the last sentence that names what
    is open or, if none does, as many closing sentences as fit in `tail` characters."""
    parts = sentences(note)
    if not parts:
        return ""
    marked = [i for i, p in enumerate(parts) if p.startswith(OPEN_MARKERS)]
    if marked:
        first = marked[-1]
    else:
        first = len(parts) - 1
        while first > 0 and len(" ".join(parts[first - 1:])) <= tail:
            first -= 1
    summary = parts[first]
    for part in parts[first + 1:]:
        if len(summary) + 1 + len(part) > limit:
            break
        summary += " " + part
    return summary


def section15(rows: list[list[str]]) -> str:
    counts = Counter(r[3] for r in rows)
    by_section: dict[str, list[list[str]]] = defaultdict(list)
    for r in rows:
        by_section[r[1].split(",")[0].strip()].append(r)

    def order(section: str):
        head = section.split()[0]
        try:
            return (0, [int(x) for x in head.split(".")])
        except ValueError:
            return (1, [section])

    lines = ["# Section 15 replacement: real status of every requirement", "",
             "Generated from `docs/report/COMPLIANCE.md` by `python -m scripts.make_docs`; do not edit.",
             "This table replaces the project document's Section 15 \"Work Completed\" table, which",
             "marked every deliverable as Completed. A requirement is **Met** only when its code exists",
             "and its acceptance test passes; the evidence and tests are in COMPLIANCE.md.", "",
             f"**{len(rows)} requirements: " + ", ".join(f"{counts.get(s, 0)} {s}" for s in
                                                       ("Met", "Partial", "Not met", "Deviation")) + ".**", ""]
    for section in sorted(by_section, key=order):
        lines += [f"## Specification section {section}", "", "| ID | Requirement | Status | Milestone |",
                  "|---|---|---|---|"]
        lines += [f"| {r[0]} | {r[2]} | {r[3]} | {r[6]} |" for r in by_section[section]]
        lines.append("")
    not_met = [r for r in rows if r[3] != "Met"]
    lines += ["## Not met in full, and why", "",
              "Every requirement that is not Met, with the reason from COMPLIANCE.md (shortened).", ""]
    for r in not_met:
        lines.append(f"- **{r[0]} ({r[3]})**: {summarise_note(r[7])}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    check = "--check" in (argv if argv is not None else sys.argv[1:])
    outputs = {REFERENCE: api_reference(json.loads(OPENAPI.read_text(encoding="utf-8"))),
               SECTION15: section15(compliance_rows())}
    stale = []
    for path, text in outputs.items():
        if check:
            if not path.exists() or path.read_text(encoding="utf-8") != text:
                stale.append(path.name)
        else:
            path.write_text(text, encoding="utf-8")
            print(f"wrote {path.relative_to(PROJECT_ROOT)}")
    if stale:
        print(f"out of date: {stale}; run python -m scripts.make_docs")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
