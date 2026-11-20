"""Classification policies (spec 7.10; CLAUDE.md section 11; M16c).

An administrator can say "documents of source type X" or "documents whose path matches
this glob" are at least `confidential` or `restricted`. The effective classification of a
document is the STRICTEST of:
- its base classification (what enrichment decided from the document and its source), and
- every active policy that matches it.

Rules only ever make a document stricter. `restricted` matters most: restricted text is
read only by the private model (app/agents/model_router.py) and is never exported for
learning or cached.

A document keeps its base classification (`documents.base_classification`), so a rule can
be switched off and every document goes back to what it was. Changing a rule re-applies
the rules to the documents it matches (Indexer.reclassify), updating the metadata store
and the payloads of both indexes.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from fnmatch import fnmatchcase

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ClassificationPolicy
from app.ingest.manifest import CLASSIFICATIONS, SOURCE_TYPES

MATCH_KINDS = ("source_type", "path")
POLICY_LEVELS = ("confidential", "restricted")  # a rule never sets public_internal
_RANK = {name: rank for rank, name in enumerate(CLASSIFICATIONS)}  # higher = stricter


@dataclass(frozen=True)
class Rule:
    """An active policy, detached from the session."""

    id: int
    match: str
    pattern: str
    classification: str

    def matches(self, source_type: str, path: str) -> bool:
        if self.match == "source_type":
            return source_type == self.pattern
        return fnmatchcase(path.replace("\\", "/"), self.pattern)


def rule_problems(match: str, pattern: str, classification: str) -> list[str]:
    """Why a rule cannot be stored (empty list = it can)."""
    problems = []
    if match not in MATCH_KINDS:
        problems.append(f"match must be one of {MATCH_KINDS}")
    elif match == "source_type" and pattern not in SOURCE_TYPES:
        problems.append(f"unknown source type '{pattern}'")
    elif match == "path" and (not pattern.strip() or len(pattern) > 300):
        problems.append("the path pattern must be a glob of 1-300 characters, e.g. 'engagement/*'")
    if classification not in POLICY_LEVELS:
        problems.append(f"classification must be one of {POLICY_LEVELS}")
    return problems


def active_rules(s: Session) -> list[Rule]:
    return [Rule(p.id, p.match, p.pattern, p.classification) for p in s.scalars(
        select(ClassificationPolicy).where(ClassificationPolicy.active.is_(True))
        .order_by(ClassificationPolicy.id))]


def strictest(base: str, rules: Iterable[Rule], source_type: str, path: str) -> str:
    """The effective classification: `base`, raised by every matching rule."""
    out = base if base in _RANK else "public_internal"
    for rule in rules:
        if rule.matches(source_type, path) and _RANK[rule.classification] > _RANK[out]:
            out = rule.classification
    return out
