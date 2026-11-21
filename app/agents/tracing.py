"""Write one `traces` row per agent step, masked (CLAUDE.md sections 7 and 12).

Since M17a each step also feeds the Prometheus metrics (step time, tokens, estimated cost)
and, when tracing is on, becomes an OpenTelemetry span under the question's root span
(app/observability/otel.py)."""

from collections.abc import Sequence
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app.agents.budgets import BudgetTracker, StepBudget
from app.agents.llm import LLMResult
from app.agents.versions import ActiveVersions
from app.db.models import Trace
from app.observability import metrics
from app.security.masking import mask_json


class Tracer:
    """Records agent steps for one query."""

    def __init__(self, session_factory: sessionmaker[Session], query_id: int,
                 versions: ActiveVersions | None = None, budget: StepBudget | None = None) -> None:
        self.sessions, self.query_id = session_factory, query_id
        self.versions = versions or ActiveVersions(None)
        # M14a: the question's step budget, charged with every LLM result logged here, and
        # the latest graph state, so a run stopped by its budget can still return the best
        # verified answer it reached.
        self.budget = BudgetTracker(budget or StepBudget())
        self.snapshot: dict[str, Any] = {}
        # M17a: the question's root span (None when tracing is off) and what spans may carry.
        self.span = None
        self.include_content, self.content_max_chars = False, 4000

    def log(self, agent: str, input: Any, output: Any, llm: Sequence[LLMResult] = (),
            latency_ms: float | None = None) -> None:
        """Write a row. If the step made LLM calls, their models/tokens/latency are summed
        and charged to the question's budget."""
        self.budget.charge(llm)
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
        if row.latency_ms is not None:
            metrics.AGENT_SECONDS.labels(agent).observe(row.latency_ms / 1000)
        for r in llm:
            metrics.record_llm(agent, r.model, r.tokens_in, r.tokens_out)
        if self.span is not None:
            self.span.step(agent, latency_ms=row.latency_ms, llm=llm,
                           versions={"prompt_version_id": row.prompt_version_id,
                                     "policy_version_id": row.policy_version_id},
                           step_input=row.input, step_output=row.output,
                           include_content=self.include_content,
                           content_max_chars=self.content_max_chars)
