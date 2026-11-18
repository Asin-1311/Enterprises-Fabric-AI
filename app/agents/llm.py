"""One call path for every LLM request made by the agents.

- Picks the model and effort for a role: "light" (MODEL_LIGHT), "synth" (MODEL_SYNTH)
  or "verify" (MODEL_VERIFY).
- Sends no sampling parameters (removed by the API for these models); uses
  `output_config.effort`, plus `output_config.format` when a JSON schema is given.
- Streams when an `on_text` callback is given (used for the SSE answer in M6).
- Returns an `LLMResult` with the text, parsed JSON, model, effort, token counts and
  latency, which the tracer writes to `traces`.
- A `refusal` stop reason raises `LLMRefusal` instead of returning empty text.

Unit tests replace this class with a scripted fake; live calls happen only in eval/ and
the smoke script.
"""

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

import anthropic

from app.config import Settings


class LLMRefusal(RuntimeError):
    """The model declined the request (stop_reason == 'refusal')."""


@dataclass
class LLMResult:
    """Outcome of one model call."""

    text: str
    data: Any | None  # parsed JSON when a schema was requested
    model: str
    effort: str
    tokens_in: int
    tokens_out: int
    latency_ms: float


class LLMCaller(Protocol):
    """What agents need from an LLM (the real `LLM` or a test fake)."""

    def call(self, *, purpose: str, role: str, system: str, user: str, max_tokens: int,
             schema: dict | None = None, on_text: Callable[[str], None] | None = None) -> LLMResult: ...


class LLM:
    """Real Claude calls through the configured client (OpenRouter or Anthropic API)."""

    def __init__(self, client: anthropic.Anthropic, settings: Settings) -> None:
        self.client = client
        self.models = {"light": settings.model_light, "synth": settings.model_synth,
                       "verify": settings.model_verify}
        self.efforts = {"light": settings.effort_light, "synth": settings.effort_synth,
                        "verify": settings.effort_verify}

    def call(self, *, purpose: str, role: str, system: str, user: str, max_tokens: int,
             schema: dict | None = None, on_text: Callable[[str], None] | None = None) -> LLMResult:
        """Make one request. `purpose` is only a label (traces, fakes)."""
        model, effort = self.models[role], self.efforts[role]
        output_config: dict[str, Any] = {"effort": effort}
        if schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": schema}
        kwargs = dict(model=model, max_tokens=max_tokens, system=system,
                      output_config=output_config,
                      messages=[{"role": "user", "content": user}])
        start = time.perf_counter()
        if on_text is not None:
            with self.client.messages.stream(**kwargs) as stream:
                for chunk in stream.text_stream:
                    on_text(chunk)
                message = stream.get_final_message()
        else:
            message = self.client.messages.create(**kwargs)
        latency = (time.perf_counter() - start) * 1000
        if message.stop_reason == "refusal":
            raise LLMRefusal(f"{model} declined the request ({purpose})")
        # Thinking blocks may come first (e.g. Opus): take only text blocks.
        text = "".join(b.text for b in message.content if b.type == "text")
        data = json.loads(text) if schema is not None else None
        return LLMResult(text=text, data=data, model=model, effort=effort,
                         tokens_in=message.usage.input_tokens,
                         tokens_out=message.usage.output_tokens, latency_ms=latency)
