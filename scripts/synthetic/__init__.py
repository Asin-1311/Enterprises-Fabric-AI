"""Synthetic corpus definitions for the Enterprise Fabric AI demo.

Every document here is SYNTHETIC: a fictional firm ("Example & Associates,
Chartered Accountants"), fictional clients and fictional people. Where a document
restates ICAI guidance, an HTML comment `<!-- source: ICAI chN §x.y (p.n) -->`
points to the chapter, section and PDF page so the author can check it.
"""

from dataclasses import dataclass

FIRM = "Example & Associates, Chartered Accountants"


@dataclass(frozen=True)
class SynthDoc:
    """One synthetic document plus its manifest metadata."""

    doc_id: str
    filename: str  # file name inside data/corpus/<source_type>/
    source_type: str
    title: str
    version: str
    effective_date: str  # YYYY-MM-DD
    access_level: str  # staff | manager | partner
    body: str  # Markdown, WITHOUT the banner line (the writer adds it)
    supersedes: str = ""
    engagement_id: str = ""
