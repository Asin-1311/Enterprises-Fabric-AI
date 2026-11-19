"""Create the synthetic "firm methodology" Git repository for the git connector (M12c).

    .venv\\Scripts\\python.exe -m scripts.make_synthetic_git_repo

Creates data/sources/firm-methodology-repo/ (git-ignored) with a few SYNTHETIC Markdown
documents about internal firm procedures, in two commits, so the connector has history to
read. Does nothing if the repository already exists. Register it as a source with
scripts/register_demo_sources.py.

Content rule (CLAUDE.md section 5): fictional firm procedures only; no statement about
auditing standards.
"""

import subprocess
import sys
from pathlib import Path

from app.config import PROJECT_ROOT
from app.ingest.md_loader import SYNTHETIC_BANNER

REPO = PROJECT_ROOT / "data" / "sources" / "firm-methodology-repo"
AUTHOR = ["-c", "user.name=Synthetic Author", "-c", "user.email=synthetic@example-associates.test"]

DOCS_V1 = {
    "methodology/working-paper-referencing.md": """# Working paper referencing

## 1. Reference format
Each working paper carries the area code and a running number, for example INV-003, and
cross-references the lead schedule it supports.

## 2. Tick marks
Use the firm's standard tick mark legend, printed on the inside cover of every file.
""",
    "methodology/review-notes.md": """# Clearing review notes

## 1. Raising a note
Reviewers raise notes in the practice management system, never on the working paper itself.

## 2. Clearing a note
The preparer answers each note and the reviewer closes it. Open notes block the file from
being locked.
""",
    "README.txt": "Synthetic repository for the git connector demonstration.\n",
}
DOCS_V2 = {  # the second commit edits one section of one document
    "methodology/review-notes.md": DOCS_V1["methodology/review-notes.md"].replace(
        "Open notes block the file from\nbeing locked.",
        "Open notes block the file from\nbeing locked, and the manager is reminded weekly."),
}


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *AUTHOR, *args], cwd=repo, check=True, capture_output=True)


def write_docs(repo: Path, docs: dict[str, str]) -> None:
    for rel, body in docs.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        text = body if rel.endswith(".txt") else f"{SYNTHETIC_BANNER}\n\n{body}"
        path.write_text(text, encoding="utf-8", newline="\n")


def make_repo(repo: Path, commits: list[dict[str, str]], branch: str = "main") -> None:
    """A Git repository at `repo` with one commit per dict of {path: text}."""
    repo.mkdir(parents=True, exist_ok=True)
    git(repo, "init", "--quiet", f"--initial-branch={branch}")
    for n, docs in enumerate(commits, start=1):
        write_docs(repo, docs)
        git(repo, "add", "--all")
        git(repo, "commit", "--quiet", "-m", f"Synthetic commit {n}")


def main() -> int:
    if (REPO / ".git").exists():
        print(f"{REPO} already exists; nothing to do.")
        return 0
    make_repo(REPO, [DOCS_V1, DOCS_V2])
    print(f"Created {REPO} (2 commits, {len([d for d in DOCS_V1 if d.endswith('.md')])} Markdown documents).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
