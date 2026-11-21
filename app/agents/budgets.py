"""Step budgets per question (spec section 5, agent plane; CLAUDE.md section 7; M14a).

Every question gets limits on: LLM calls, tokens (in + out), wall-clock seconds and
retrieval rounds (the first search, the follow-up, the retry after verification). All
four are in `.env` (BUDGET_MAX_*).

How they are enforced:
- every LLM result is charged when its step is traced (app/agents/tracing.py);
- before an LLM call or a new retrieval round, the step asks `check()`; if the call or
  round would pass a limit, `BudgetExceeded` is raised;
- the assistant then ends the run with the best VERIFIED answer it has so far (only
  supported claims, with their confidence), or with an abstention if nothing was verified
  yet. The breach is written to `traces` (`budget_exceeded`) and the log.
A budget stops runaway loops and cost; it never lets an unverified draft through.
"""

import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass

from app.agents.llm import LLMResult


@dataclass(frozen=True)
class StepBudget:
    max_llm_calls: int = 12
    max_tokens: int = 60_000
    max_seconds: float = 90.0
    max_retrieval_rounds: int = 3


class BudgetExceeded(RuntimeError):
    """A step would pass one of the question's limits."""

    def __init__(self, limit: str, usage: dict) -> None:
        super().__init__(f"{limit} budget exceeded: {usage}")
        self.limit, self.usage = limit, usage


class BudgetTracker:
    """Usage of one question so far (thread-safe: retrieval runs in parallel)."""

    def __init__(self, budget: StepBudget) -> None:
        self.budget = budget
        self.started = time.monotonic()
        self.llm_calls = self.tokens = self.retrieval_rounds = 0
        self._lock = threading.Lock()

    def usage(self) -> dict:
        return {"llm_calls": self.llm_calls, "tokens": self.tokens,
                "seconds": round(time.monotonic() - self.started, 1),
                "retrieval_rounds": self.retrieval_rounds}

    def charge(self, results: Sequence[LLMResult]) -> None:
        with self._lock:
            self.llm_calls += len(results)
            self.tokens += sum(r.tokens_in + r.tokens_out for r in results)

    def allows(self, *, llm_calls: int = 0, retrieval_round: bool = False) -> bool:
        """Whether `check` would pass, without counting anything (used to choose a path)."""
        b = self.budget
        with self._lock:
            return (time.monotonic() - self.started <= b.max_seconds
                    and not (llm_calls and self.tokens >= b.max_tokens)
                    and self.llm_calls + llm_calls <= b.max_llm_calls
                    and not (retrieval_round and self.retrieval_rounds + 1 > b.max_retrieval_rounds))

    def check(self, *, llm_calls: int = 0, retrieval_round: bool = False) -> None:
        """Raise BudgetExceeded if `llm_calls` more calls (or one more retrieval round)
        would pass a limit; otherwise count the round."""
        b = self.budget
        with self._lock:
            if time.monotonic() - self.started > b.max_seconds:
                raise BudgetExceeded("time", self.usage())
            if self.tokens >= b.max_tokens and llm_calls:
                raise BudgetExceeded("tokens", self.usage())
            if self.llm_calls + llm_calls > b.max_llm_calls:
                raise BudgetExceeded("llm_calls", self.usage())
            if retrieval_round:
                if self.retrieval_rounds + 1 > b.max_retrieval_rounds:
                    raise BudgetExceeded("retrieval_rounds", self.usage())
                self.retrieval_rounds += 1
