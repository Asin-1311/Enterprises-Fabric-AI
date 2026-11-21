"""Chunking profiles: how each source type is cut into chunks (spec 7.2; CLAUDE.md
section 6; Gap Register R-7.2).

A profile fixes the window size, the overlap and the "keep whole" rule for one source
type. Every chunk records the profile that made it (`chunks.chunking_profile`, e.g.
"faq@1:400/50"), so chunk granularity is stored per source type and can be inspected.

The reference changes when the profile changes: bump `version` when a rule changes, and
the sizes come from `.env` (CHUNK_MAX_TOKENS, CHUNK_OVERLAP_TOKENS). The indexer compares
each document's stored reference with the current one, so changing a profile re-chunks
only the documents of that source type.

Keep-whole rules (from Phase 1, now per profile):
- standards   ICAI chapters: numbered sections; ILLUSTRATION / Example blocks whole.
- checklists  each `##` item group whole.
- faq         each `###` question and answer whole.
- all others  paragraphs; Markdown tables whole.
"""

from dataclasses import dataclass

from app.ingest.manifest import SOURCE_TYPES


@dataclass(frozen=True)
class ChunkingProfile:
    """How one source type is chunked."""

    name: str                # the source type it applies to
    version: int             # bumped by hand whenever a rule below changes
    max_tokens: int
    overlap_tokens: int
    whole_level: int | None  # Markdown heading level kept whole; None = paragraph windows

    @property
    def ref(self) -> str:
        """What a chunk records, e.g. 'faq@1:400/50'."""
        return f"{self.name}@{self.version}:{self.max_tokens}/{self.overlap_tokens}"


# Per source type: (version, Markdown heading level kept whole).
_RULES: dict[str, tuple[int, int | None]] = {
    "standards": (1, None),
    "checklists": (1, 2),
    "faq": (1, 3),
}


def profile_for(source_type: str, max_tokens: int = 400, overlap_tokens: int = 50) -> ChunkingProfile:
    """The chunking profile of one source type, with the configured window sizes."""
    if source_type not in SOURCE_TYPES:
        raise ValueError(f"unknown source_type '{source_type}'")
    version, whole_level = _RULES.get(source_type, (1, None))
    return ChunkingProfile(source_type, version, max_tokens, overlap_tokens, whole_level)


def all_profiles(max_tokens: int = 400, overlap_tokens: int = 50) -> dict[str, ChunkingProfile]:
    """Every source type's profile (for the admin view and the tests)."""
    return {t: profile_for(t, max_tokens, overlap_tokens) for t in SOURCE_TYPES}
