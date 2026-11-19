"""PolicyEngine in code (lite profile; M13).

The same rule as policy/document_access.rego, written with `can_see` from
app/retrieval/acl.py: role level, then engagement team (partners see every engagement).
tests/test_policy.py checks that this, the SQL pre-filter and the Rego policy agree.
"""

from collections.abc import Sequence
from typing import Any

from app.retrieval.acl import UserContext, can_see


class InCodePolicy:
    name = "code"

    def allowed(self, user: UserContext, documents: Sequence[dict[str, Any]]) -> list[bool]:
        return [can_see(user, str(d.get("access_level") or ""), d.get("engagement_id") or None)
                for d in documents]
