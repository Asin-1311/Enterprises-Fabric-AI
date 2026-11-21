"""Enrichment: every change gets owner, classification, timestamp and access fields
before it is indexed (spec section 7.1; CLAUDE.md section 6; Gap Register R-7.1-a).

A change arrives with whatever metadata its source knows (a manifest row, a webhook body,
later a Git commit or an S3 object). `enrich` turns that into one complete, validated
`DocumentSpec`, filling gaps from the registered source:

- owner           the document's own owner, else the source's owner (may stay empty);
- classification  the document's own, else the source's, else "public_internal"; but a
                  default is never below "confidential" for an engagement document or one
                  above staff level (the same rule the manifest uses); then raised by any
                  matching classification policy (M16c, app/ingest/classification.py);
- timestamp       when the change happened (updated_at);
- access          access_level and engagement_id, checked against the known engagements:
                  an engagement document must name its engagement, nothing else may.

A change that cannot be enriched is refused with `EnrichmentError`; it is never indexed
with guessed access fields, because a wrong access level would leak or hide documents.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from app.db.models import Source
from app.ingest.classification import Rule, strictest
from app.ingest.manifest import CLASSIFICATIONS, SOURCE_TYPES, ManifestRow

ACCESS_LEVELS = ("staff", "manager", "partner")
_PROTECTION = {name: rank for rank, name in enumerate(CLASSIFICATIONS)}  # higher = stricter
_DOC_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")


class EnrichmentError(ValueError):
    """A change whose metadata is missing or inconsistent: it is refused, not guessed."""


@dataclass(frozen=True)
class DocumentSpec:
    """Everything the indexer stores about one document (except content and hashes)."""

    doc_id: str
    source_id: str
    uri: str
    path: str
    source_type: str
    title: str
    version: str | None
    effective_date: str
    supersedes: str | None
    access_level: str
    engagement_id: str | None
    owner: str | None
    classification: str
    synthetic: bool
    updated_at: datetime | None = None
    # M16c: the classification before classification policies (None = same as above).
    base_classification: str | None = None

    def document_fields(self) -> dict[str, Any]:
        """The `documents` columns this spec decides (compared to detect a change)."""
        return dict(path=self.path, source_type=self.source_type, title=self.title,
                    version=self.version, effective_date=self.effective_date,
                    supersedes=self.supersedes, access_level=self.access_level,
                    engagement_id=self.engagement_id, synthetic=self.synthetic,
                    source_id=self.source_id, uri=self.uri, owner=self.owner,
                    classification=self.classification,
                    base_classification=self.base_classification or self.classification)

    def payload(self) -> dict[str, Any]:
        """Fields every index entry of this document carries (CLAUDE.md section 9)."""
        return dict(doc_id=self.doc_id, source_id=self.source_id, source_type=self.source_type,
                    access_level=self.access_level, engagement_id=self.engagement_id or "",
                    classification=self.classification, effective_date=self.effective_date)


def _text(metadata: dict[str, Any], key: str) -> str:
    value = metadata.get(key)
    return "" if value is None else str(value).strip()


def enrich(metadata: dict[str, Any], *, doc_id: str, source_id: str, uri: str, path: str,
           source: Source | None, engagement_ids: set[str], user_ids: set[str],
           occurred_at: datetime | None = None, rules: Iterable[Rule] = ()) -> DocumentSpec:
    """Validate a change's metadata and complete it from its source (see module doc)."""
    problems: list[str] = []
    if not _DOC_ID.match(doc_id):
        problems.append(f"doc_id '{doc_id}' is not a valid id")
    source_type, title = _text(metadata, "source_type"), _text(metadata, "title")
    access_level, engagement_id = _text(metadata, "access_level"), _text(metadata, "engagement_id")
    effective_date = _text(metadata, "effective_date")
    if source_type not in SOURCE_TYPES:
        problems.append(f"unknown source_type '{source_type}'")
    if not title:
        problems.append("title is missing")
    if access_level not in ACCESS_LEVELS:
        problems.append(f"access_level must be one of {ACCESS_LEVELS}, got '{access_level}'")
    if engagement_id and engagement_id not in engagement_ids:
        problems.append(f"unknown engagement_id '{engagement_id}'")
    if source_type == "engagement" and not engagement_id:
        problems.append("an engagement document needs an engagement_id")
    if source_type != "engagement" and engagement_id:
        problems.append("only engagement documents may have an engagement_id")
    try:
        date.fromisoformat(effective_date)
    except ValueError:
        problems.append(f"effective_date must be YYYY-MM-DD, got '{effective_date}'")

    owner = _text(metadata, "owner") or (source.owner if source else "") or ""
    if owner and owner not in user_ids:
        problems.append(f"owner '{owner}' is not a known user")
    classification = _text(metadata, "classification")
    if not classification:  # a default: from the source, never weaker than the content needs
        classification = (source.classification if source else "") or "public_internal"
        if (source_type == "engagement" or access_level in ("manager", "partner")) \
                and _PROTECTION.get(classification, 0) < _PROTECTION["confidential"]:
            classification = "confidential"
    if classification not in CLASSIFICATIONS:
        problems.append(f"unknown classification '{classification}'")
    if problems:
        raise EnrichmentError(f"{doc_id}: " + "; ".join(problems))

    synthetic = metadata.get("synthetic")
    base = classification  # M16c: active classification policies may only raise it
    classification = strictest(base, rules, source_type, path)
    return DocumentSpec(
        doc_id=doc_id, source_id=source_id, uri=uri, path=path, source_type=source_type,
        title=title, version=_text(metadata, "version") or None, effective_date=effective_date,
        supersedes=_text(metadata, "supersedes") or None, access_level=access_level,
        engagement_id=engagement_id or None, owner=owner or None, classification=classification,
        synthetic=synthetic if isinstance(synthetic, bool) else str(synthetic).lower() == "true",
        updated_at=occurred_at, base_classification=base)


def manifest_metadata(row: ManifestRow) -> dict[str, Any]:
    """The metadata a manifest row contributes to a change event."""
    return dict(source_type=row.source_type, title=row.title, version=row.version,
                effective_date=row.effective_date, supersedes=row.supersedes,
                access_level=row.access_level, engagement_id=row.engagement_id,
                owner=row.owner, classification=row.classification,
                synthetic=row.is_synthetic)


def folder_source_id(source_type: str) -> str:
    """The registered filesystem source for one corpus folder (M11)."""
    return f"fs-{source_type}"


def manifest_source_id(row: ManifestRow) -> str:
    return row.source_id or folder_source_id(row.source_type)
