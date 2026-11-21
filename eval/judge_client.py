"""The answer-evaluation judge's model client (M18b; CLAUDE.md section 3 "MODEL_JUDGE").

The judge must come from a different model family than the verifier, so it does not share
the verifier's blind spots. The system's models are Claude (through the Anthropic SDK), so
the judge is called through an OpenAI-compatible chat-completions API instead: OpenRouter's
`/v1/chat/completions`, with the same key as the rest of the project.

- Structured output: `response_format` with a strict JSON schema (the judge's SCHEMA).
- No sampling parameters (CLAUDE.md section 3): the judge is steered by a fixed prompt, the
  schema and a reasoning-effort setting (EFFORT_JUDGE).
- Restricted documents never reach it: in eval/run_answer_eval.py the client sits behind
  `ModelRouter`, which refuses any request holding a restricted <document> block.
- Every call returns model, effort, tokens and latency like the system's own LLM client.
"""

import json
import time
from collections.abc import Callable

import httpx

from app.agents.llm import LLMResult
from app.config import Settings, model_family  # noqa: F401  (re-exported for the eval)


class JudgeError(RuntimeError):
    """The judge's API returned no usable answer."""


class OpenAICompatibleJudge:
    """An LLMCaller for the judge, over an OpenAI-compatible chat-completions endpoint."""

    def __init__(self, base_url: str, api_key: str, model: str, effort: str,
                 client: httpx.Client | None = None, timeout: float = 180.0) -> None:
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model, self.effort = model, effort
        self.client = client or httpx.Client(timeout=timeout)
        self.headers = {"Authorization": f"Bearer {api_key}"}

    @classmethod
    def from_settings(cls, settings: Settings) -> "OpenAICompatibleJudge":
        if not settings.model_judge:
            raise SystemExit("Set MODEL_JUDGE in .env (a model from a different family than MODEL_VERIFY).")
        base = settings.judge_base_url or (settings.llm_base_url or "https://openrouter.ai/api").rstrip("/") + "/v1"
        key = settings.llm_auth_token or ""
        if not key:
            raise SystemExit("The judge uses the OpenRouter key (LLM_AUTH_TOKEN), which is not set.")
        return cls(base, key, settings.model_judge, settings.effort_judge)

    def call(self, *, purpose: str, role: str, system: str, user: str, max_tokens: int,
             schema: dict | None = None, on_text: Callable[[str], None] | None = None) -> LLMResult:
        body: dict = {"model": self.model, "max_tokens": max_tokens,
                      "messages": [{"role": "system", "content": system},
                                   {"role": "user", "content": user}],
                      "reasoning": {"effort": self.effort}, "usage": {"include": True}}
        if schema is not None:
            body["response_format"] = {"type": "json_schema",
                                       "json_schema": {"name": purpose, "strict": True, "schema": schema}}
        started = time.perf_counter()
        r = self.client.post(self.url, json=body, headers=self.headers)
        latency_ms = (time.perf_counter() - started) * 1000
        if r.status_code != 200:
            raise JudgeError(f"judge API returned HTTP {r.status_code}")  # no body: it may echo input
        payload = r.json()
        choices = payload.get("choices") or []
        text = ((choices[0].get("message") or {}).get("content") or "") if choices else ""
        if not text:
            raise JudgeError("judge API returned an empty answer")
        data = None
        if schema is not None:
            try:
                data = json.loads(text)
            except json.JSONDecodeError as exc:
                raise JudgeError("judge answer is not valid JSON") from exc
        usage = payload.get("usage") or {}
        # The configured id (prices and reports use it), not a dated id the API may return.
        return LLMResult(text=text, data=data, model=self.model, effort=self.effort,
                         tokens_in=int(usage.get("prompt_tokens") or 0),
                         tokens_out=int(usage.get("completion_tokens") or 0), latency_ms=latency_ms)
