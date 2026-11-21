"""Turn the formats SaaS systems store text in into Markdown for the chunker (M12c).

- `html_to_markdown`: Confluence "storage" format and SharePoint pages are XHTML. Headings
  become `#` lines (the chunker splits on them), paragraphs and list items become lines,
  tables become Markdown tables (kept whole by the chunker). Everything else keeps only its
  text. Uses the standard library's HTML parser; no new dependency.
- `adf_to_markdown`: Jira descriptions are Atlassian Document Format (a JSON tree). Only the
  node types needed for text are handled: paragraph, heading, bulletList/orderedList,
  listItem, table/tableRow/tableCell/tableHeader, text, hardBreak.

Text is copied, never rewritten: retrieved content stays untrusted data downstream.
"""

from html.parser import HTMLParser
from typing import Any

_BLOCKS = {"p", "div", "li", "tr", "br", "h1", "h2", "h3", "h4", "h5", "h6", "table", "ul", "ol"}


class _Html(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lines: list[str] = []
        self.current: list[str] = []
        self.prefix = ""
        self.row: list[str] | None = None
        self.cell: list[str] | None = None
        self.rows_in_table = 0
        self.skip = 0  # inside <script>/<style>

    def _flush(self) -> None:
        text = " ".join("".join(self.current).split())
        if text:
            self.lines.append(self.prefix + text)
        self.current, self.prefix = [], ""

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in ("script", "style"):
            self.skip += 1
        elif tag in ("td", "th"):
            self.cell = []
        elif tag == "tr":
            self.row = []
        elif tag == "table":
            self._flush()
            self.lines.append("")
            self.rows_in_table = 0
        elif tag in _BLOCKS:
            self._flush()
            if tag[0] == "h" and tag[1:].isdigit():
                self.lines.append("")
                self.prefix = "#" * int(tag[1]) + " "
            elif tag == "li":
                self.prefix = "- "

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)
        elif tag in ("td", "th") and self.cell is not None and self.row is not None:
            self.row.append(" ".join("".join(self.cell).split()))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.lines.append("| " + " | ".join(self.row) + " |")
            if self.rows_in_table == 0:
                self.lines.append("|" + "---|" * len(self.row))
            self.rows_in_table += 1
            self.row = None
        elif tag == "table":
            self.lines.append("")
        elif tag in _BLOCKS:
            self._flush()
            if tag[0] == "h" and tag[1:].isdigit():
                self.lines.append("")

    def handle_data(self, data: str) -> None:
        if self.skip:
            return
        (self.cell if self.cell is not None else self.current).append(data)


def _tidy(lines: list[str]) -> str:
    out: list[str] = []
    for line in lines:
        if line == "" and (not out or out[-1] == ""):
            continue
        out.append(line)
    return "\n".join(out).strip() + "\n"


def html_to_markdown(html: str) -> str:
    """XHTML (Confluence storage format, SharePoint pages) as Markdown."""
    parser = _Html()
    parser.feed(html)
    parser.close()
    parser._flush()
    # A paragraph followed by a heading needs a blank line between them for the splitter.
    lines: list[str] = []
    for line in parser.lines:
        if line.startswith("#") and lines and lines[-1] != "":
            lines.append("")
        lines.append(line)
    return _tidy(lines)


def _adf_text(node: dict[str, Any]) -> str:
    if node.get("type") == "text":
        return str(node.get("text", ""))
    if node.get("type") == "hardBreak":
        return "\n"
    return "".join(_adf_text(c) for c in node.get("content") or [])


def _adf_block(node: dict[str, Any], lines: list[str]) -> None:
    kind = node.get("type")
    children = node.get("content") or []
    if kind == "heading":
        level = int((node.get("attrs") or {}).get("level", 2))
        lines += ["", "#" * max(1, min(level, 6)) + " " + _adf_text(node).strip(), ""]
    elif kind == "paragraph":
        text = _adf_text(node).strip()
        if text:
            lines += [text, ""]
    elif kind in ("bulletList", "orderedList"):
        for item in children:
            lines.append("- " + " ".join(_adf_text(item).split()))
        lines.append("")
    elif kind == "table":
        for n, row in enumerate(children):
            cells = [" ".join(_adf_text(c).split()) for c in row.get("content") or []]
            lines.append("| " + " | ".join(cells) + " |")
            if n == 0:
                lines.append("|" + "---|" * len(cells))
        lines.append("")
    else:  # doc, panels, quotes, ...: walk into the children
        for child in children:
            _adf_block(child, lines)


def adf_to_markdown(doc: dict[str, Any] | None) -> str:
    """An Atlassian Document Format tree (a Jira description) as Markdown."""
    lines: list[str] = []
    if isinstance(doc, dict):
        _adf_block(doc, lines)
    return _tidy(lines)
