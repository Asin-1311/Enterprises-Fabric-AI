"""Two-layer access control, applied BEFORE any scoring (CLAUDE.md sections 5 and 12).

Layer 1, role level: staff < manager < partner. A user sees documents at or below
their own level.

Layer 2, engagement scope: a document with an engagement_id is visible only to that
engagement's team (members and the engagement partner). Partners see every engagement.
A manager who is not on the team sees nothing from it.

The same rule is expressed three ways, and tests check they agree:
- `qdrant_filter()`   : a Qdrant payload filter, so dense search never scores a
                         forbidden vector;
- `allowed_chunk_ids()`: the set of chunk ids BM25 may score;
- `can_see()`          : a plain Python check, used as a final defence-in-depth pass on
                         results.
"""

from dataclasses import dataclass

from qdrant_client import models
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db.models import Chunk, Document, Engagement, EngagementMember, User

ROLE_LEVELS: dict[str, int] = {"staff": 0, "manager": 1, "partner": 2}


class UnknownUserError(LookupError):
    """The user_id is not in the users table."""


@dataclass(frozen=True)
class UserContext:
    """Who is asking: role plus the engagements they are assigned to."""

    user_id: str
    role: str
    engagement_ids: frozenset[str]

    @property
    def sees_all_engagements(self) -> bool:
        return self.role == "partner"

    @property
    def allowed_levels(self) -> list[str]:
        """Access levels at or below this user's role."""
        mine = ROLE_LEVELS[self.role]
        return [lvl for lvl, rank in ROLE_LEVELS.items() if rank <= mine]


def load_user(session: Session, user_id: str) -> UserContext:
    """Look up role and engagement assignments from SQLite."""
    user = session.get(User, user_id)
    if user is None or user.role not in ROLE_LEVELS:
        raise UnknownUserError(user_id)
    member_of = set(session.scalars(
        select(EngagementMember.engagement_id).where(EngagementMember.user_id == user_id)))
    partner_of = set(session.scalars(
        select(Engagement.engagement_id).where(Engagement.partner_id == user_id)))
    return UserContext(user_id, user.role, frozenset(member_of | partner_of))


def can_see(user: UserContext, access_level: str, engagement_id: str | None) -> bool:
    """True if `user` may see a document with these metadata."""
    if access_level not in user.allowed_levels:
        return False
    if engagement_id and not user.sees_all_engagements:
        return engagement_id in user.engagement_ids
    return True


def qdrant_filter(user: UserContext, source_types: list[str] | None = None) -> models.Filter:
    """Payload filter enforcing both layers (plus optional source routing) in Qdrant."""
    must: list[models.Condition] = [
        models.FieldCondition(key="access_level", match=models.MatchAny(any=user.allowed_levels)),
    ]
    if not user.sees_all_engagements:
        # "" = firm-wide document; otherwise only the user's own engagements.
        must.append(models.FieldCondition(
            key="engagement_id", match=models.MatchAny(any=["", *sorted(user.engagement_ids)])))
    if source_types:
        must.append(models.FieldCondition(key="source_type",
                                          match=models.MatchAny(any=list(source_types))))
    return models.Filter(must=must)


def allowed_chunk_ids(session: Session, user: UserContext,
                      source_types: list[str] | None = None) -> set[str]:
    """Chunk ids the user may see (the BM25 candidate set)."""
    stmt = (select(Chunk.chunk_id).join(Document, Document.doc_id == Chunk.doc_id)
            .where(Document.access_level.in_(user.allowed_levels)))
    if not user.sees_all_engagements:
        stmt = stmt.where(or_(Document.engagement_id.is_(None),
                              Document.engagement_id.in_(sorted(user.engagement_ids))))
    if source_types:
        stmt = stmt.where(Document.source_type.in_(list(source_types)))
    return set(session.scalars(stmt))
