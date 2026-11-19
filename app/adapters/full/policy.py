"""PolicyEngine on an OPA server (full profile; M13).

One HTTP call per search: POST <opa>/v1/data/fabric/document_access/decisions with the
user and every candidate chunk's metadata; OPA answers one boolean per chunk from
policy/document_access.rego. If OPA cannot be reached or answers anything else, the
search fails closed (PolicyUnavailable): no chunk is shown without a decision.
"""

from collections.abc import Sequence
from typing import Any

import httpx

from app.ports import PolicyUnavailable
from app.retrieval.acl import UserContext

DECISIONS_PATH = "/v1/data/fabric/document_access/decisions"


def opa_input(user: UserContext, documents: Sequence[dict[str, Any]]) -> dict[str, Any]:
    keys = ("doc_id", "access_level", "engagement_id", "source_type", "classification")
    return {"user": {"user_id": user.user_id, "role": user.role,
                     "engagement_ids": sorted(user.engagement_ids)},
            "documents": [{k: d.get(k) for k in keys} for d in documents]}


class OpaPolicyEngine:
    name = "opa"

    def __init__(self, url: str, timeout: float = 5.0,
                 transport: httpx.BaseTransport | None = None) -> None:
        self.http = httpx.Client(base_url=url.rstrip("/"), timeout=timeout, transport=transport)

    def allowed(self, user: UserContext, documents: Sequence[dict[str, Any]]) -> list[bool]:
        if not documents:
            return []
        try:
            response = self.http.post(DECISIONS_PATH, json={"input": opa_input(user, documents)})
            response.raise_for_status()
            result = response.json().get("result")
        except (httpx.HTTPError, ValueError) as exc:
            raise PolicyUnavailable(f"OPA did not answer: {exc}") from None
        if not isinstance(result, list) or len(result) != len(documents) \
                or not all(isinstance(r, bool) for r in result):
            raise PolicyUnavailable(f"OPA gave no decision list: {str(result)[:200]}")
        return result
