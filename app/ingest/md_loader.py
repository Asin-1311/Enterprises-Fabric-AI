"""Load the synthetic Markdown documents.

- Checks that the file starts with the required SYNTHETIC banner line.
- Collects `<!-- source: ... -->` comments (pointers to the ICAI chapter and
  section a policy restates) for the author's review, and removes all HTML
  comments from the indexed text.
- Splits the body into sections by Markdown heading, keeping the heading path
  (e.g. "Materiality Policy > 3. Benchmarks") so chunks can be cited by section.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

SYNTHETIC_BANNER = (
    "SYNTHETIC — created for an internship demonstration. Not real firm or client data."
)

_COMMENT = re.compile(r"<!--(.*?)-->", flags=re.DOTALL)
_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")


@dataclass
class MdSection:
    """A Markdown section: its heading, level (1 = '#'), heading path and body."""

    heading: str
    level: int
    heading_path: str
    text: str


@dataclass
class MdDocument:
    """A loaded Markdown document."""

    path: Path
    has_banner: bool
    text: str  # full text with comments removed
    source_notes: list[str] = field(default_factory=list)
    sections: list[MdSection] = field(default_factory=list)


def split_sections(text: str) -> list[MdSection]:
    """Split Markdown text into sections at each heading line."""
    sections: list[MdSection] = []
    stack: list[tuple[int, str]] = []  # (level, heading) of enclosing headings
    heading, level, body = "", 0, []

    def flush() -> None:
        content = "\n".join(body).strip()
        if heading or content:
            path = " > ".join(h for _, h in stack) if stack else heading
            sections.append(MdSection(heading, level, path, content))

    for line in text.splitlines():
        match = _HEADING.match(line)
        if match:
            flush()
            level, heading, body = len(match.group(1)), match.group(2), []
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, heading))
        else:
            body.append(line)
    flush()
    return sections


def load_markdown(path: Path) -> MdDocument:
    """Read a Markdown file, strip comments and split it into sections."""
    raw = path.read_text(encoding="utf-8")
    first_line = raw.splitlines()[0].strip() if raw.strip() else ""
    has_banner = first_line == SYNTHETIC_BANNER
    notes = [m.strip().removeprefix("source:").strip() for m in _COMMENT.findall(raw)]
    # The banner is recorded as a flag (and as `synthetic` in the manifest); it is not
    # indexed, otherwise every document would get an identical one-line chunk.
    body = raw.split("\n", 1)[1] if has_banner and "\n" in raw else ("" if has_banner else raw)
    text = _COMMENT.sub("", body)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return MdDocument(
        path=path,
        has_banner=has_banner,
        text=text,
        source_notes=[n for n in notes if n],
        sections=split_sections(text),
    )
