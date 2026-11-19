"""Write one `traces` row per agent step, masked (CLAUDE.md sections 7 and 12)."""

from collections.abc import Sequence
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app.agents.llm import LLMResult
from app.agents.versions import ActiveVersions
from app.db.models import Trace
from app.security.masking import mask_json


class Tracer:
    """Records agent steps for one query."""

    def __init__(self, session_factory: sessionmaker[Session], query_id: int,
                 versions: ActiveVersions | None = None) -> None:
        self.sessions, self.query_id = session_factory, query_id
        self.versions = versions or ActiveVersions(None)

    def log(self, agent: str, input: Any, output: Any, llm: Sequence[LLMResult] = (),
            latency_ms: float | None = None) -> None:
        """Write a row. If the step made LLM calls, their models/tokens/latency are summed."""
        models = sorted({r.model for r in llm})
        efforts = sorted({r.effort for r in llm})
        row = Trace(
            query_id=self.query_id, agent=agent,
            input=mask_json(input), output=mask_json(output),
            model=",".join(models) or None, effort=",".join(efforts) or None,
            tokens_in=sum(r.tokens_in for r in llm) if llm else None,
            tokens_out=sum(r.tokens_out for r in llm) if llm else None,
            latency_ms=latency_ms if latency_ms is not None else
            (sum(r.latency_ms for r in llm) if llm else None),
            prompt_version_id=self.versions.for_step(agent),
            policy_version_id=self.versions.policy_id,
        )
        with self.sessions() as s:
            s.add(row)
            s.commit()
