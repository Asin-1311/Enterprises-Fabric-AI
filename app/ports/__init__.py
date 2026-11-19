"""Ports: the small interfaces the rest of the system depends on (CLAUDE.md section 2).

Each backend sits behind one of these interfaces, with one adapter per profile:

| Port          | lite adapter (laptop, tests)        | full adapter (specified stack)  |
|---------------|-------------------------------------|---------------------------------|
| MetadataStore | SQLite                              | PostgreSQL 16 + Alembic          |
| VectorIndex   | Qdrant, embedded (local files)      | Qdrant server                   |
| KeywordIndex  | rank_bm25, in memory                | OpenSearch BM25                 |
| Cache         | in-process dictionary               | Redis                           |
| EventBus      | in-process queue                    | Kafka                           |

The agents, the indexer and the API use only these interfaces, so a backend can be
swapped without rewriting them (spec section 5). The interfaces are `typing.Protocol`s:
an adapter "is" a port if it has the methods, without inheriting from anything.

| Connector     | one adapter per kind of source system (app/connectors/, M12c)        |

Ports for rate limiting, policy and identity are added by the milestones that use them
(M13, M16).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from sqlalchemy.orm import Session, sessionmaker

from app.retrieval.acl import UserContext


@dataclass(frozen=True)
class AccessScope:
    """What one search may see. Built once per search from the metadata store.

    Every index must enforce `user` (role level + engagement team) and `source_types`
    inside its own query, before scoring. `allowed_chunk_ids` is the same rule evaluated
    in the metadata store; an index may use it directly (the lite keyword index does),
    and the searcher re-checks every result against it (defence in depth)."""

    user: UserContext
    source_types: tuple[str, ...] | None
    allowed_chunk_ids: frozenset[str]


@runtime_checkable
class MetadataStore(Protocol):
    """Documents, chunks, users, traces, audit log: the relational store."""

    url: str
    sessions: sessionmaker[Session]

    def initialise(self) -> None:
        """Create or migrate the schema (idempotent) and the audit-log guards."""
        ...


@runtime_checkable
class VectorIndex(Protocol):
    """Chunk embeddings with an access-control payload on every point."""

    dim: int

    def upsert(self, payloads: Sequence[dict], vectors: Sequence[Sequence[float]]) -> None: ...

    def delete_document(self, doc_id: str) -> None: ...

    def delete_chunks(self, chunk_ids: Sequence[str]) -> None:
        """Remove these chunks only (M12b: chunk-level updates). Unknown ids are ignored."""
        ...

    def vectors(self, chunk_ids: Sequence[str]) -> dict[str, list[float]]:
        """Stored vectors by chunk id, so unchanged text is never embedded twice (M12b).
        Ids not in the index are left out."""
        ...

    def set_document_payload(self, doc_id: str, payload: dict) -> None: ...

    def doc_ids_with(self, field: str, value: bool | str) -> set[str]:
        """Documents with at least one point whose payload `field` equals `value` (used to
        update only the documents whose `superseded` flag is wrong; M12c)."""
        ...

    def search_scoped(self, vector: Sequence[float], scope: AccessScope,
                      limit: int) -> list[tuple[str, float]]:
        """Top-`limit` (chunk_id, score), filtered inside the index by `scope`."""
        ...

    def count(self, doc_id: str | None = None) -> int: ...

    def chunk_ids(self, doc_id: str) -> set[str]: ...

    def payloads(self, doc_id: str) -> list[dict]: ...

    def index_chunk_ids(self) -> dict[str, set[str]]:
        """Every document's chunk ids in this index, in one pass (reconciliation, M12d)."""
        ...


@runtime_checkable
class KeywordIndex(Protocol):
    """Lexical (BM25) search over chunk text."""

    def upsert_chunks(self, chunks: Sequence[dict]) -> None:
        """Add or replace chunks; each dict has the payload fields plus `text`."""
        ...

    def delete_document(self, doc_id: str) -> None: ...

    def delete_chunks(self, chunk_ids: Sequence[str]) -> None:
        """Remove these chunks only (M12b: chunk-level updates). Unknown ids are ignored."""
        ...

    def set_document_payload(self, doc_id: str, payload: dict) -> None: ...

    def refresh(self, sessions: sessionmaker[Session]) -> None:
        """Make the changes made so far searchable (called after each batch)."""
        ...

    def search_scoped(self, query: str, scope: AccessScope,
                      limit: int) -> list[tuple[str, float]]:
        """Top-`limit` (chunk_id, score) among chunks `scope` permits."""
        ...

    def index_chunk_ids(self) -> dict[str, set[str]]:
        """Every document's chunk ids in this index, in one pass (reconciliation, M12d)."""
        ...


@runtime_checkable
class Cache(Protocol):
    """Short-lived key-value storage (the response cache in M16 uses it)."""

    def get(self, key: str) -> bytes | None: ...

    def set(self, key: str, value: bytes, ttl_seconds: float) -> None: ...

    def delete(self, key: str) -> None: ...


@runtime_checkable
class EventSubscription(Protocol):
    """One consumer group's view of a topic."""

    def poll(self, timeout_seconds: float) -> dict[str, Any] | None:
        """The next event, or None if none arrived within the timeout."""
        ...

    def commit(self) -> None:
        """Mark the events returned so far as processed (at-least-once delivery)."""
        ...

    def rewind(self) -> None:
        """Go back to the last committed position: events polled but not committed are
        delivered again by the next poll (M12d; how a failed batch is retried)."""
        ...

    def close(self) -> None: ...


@runtime_checkable
class EventBus(Protocol):
    """Publish/subscribe for change events (the ingestion worker in M12 consumes them)."""

    def publish(self, topic: str, key: str, event: dict[str, Any]) -> None: ...

    def subscribe(self, topic: str, group: str) -> EventSubscription: ...

    def close(self) -> None: ...


@runtime_checkable
class Connector(Protocol):
    """One source system (a Git repository, an S3 bucket, a Confluence space, ...): finds
    what changed since the last poll and describes each change as a `DocumentChanged`
    event (app/ingest/events.py) for the ingestion worker (M12c)."""

    source_id: str

    def poll(self, state: dict[str, Any]) -> tuple[list[Any], dict[str, Any]]:
        """(events for every change since `state`, the new state to save after the
        events are published). `state` is what the previous poll returned ({} at first)."""
        ...
