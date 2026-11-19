"""The private model: a local Ollama server (PRIVATE_MODEL, OLLAMA_URL; M13a).

Used only for requests that contain restricted documents (app/agents/model_router.py).
It answers the same calls as the external model (app/agents/llm.py), through Ollama's
HTTP API, with the project's existing httpx dependency:

- POST /api/chat with the system and user messages;
- a JSON schema, when one is given, is passed as `format` (structured output);
- with `on_text`, the reply is streamed line by line (one JSON object per line).

Effort settings are not Ollama options; the role only labels the call. One model serves
every role: on a laptop a second local model would not fit in memory.
"""

import json
import time
from collections.abc import Callable
from typing import Any

import httpx

from app.agents.llm import LLMResult

TIMEOUT_SECONDS = 300.0


class PrivateModelError(RuntimeError):
    """The private model did not answer (the request is not sent anywhere else)."""


class OllamaLLM:
    def __init__(self, model: str, url: str = "http://127.0.0.1:11434",
                 transport: httpx.BaseTransport | None = None) -> None:
        self.model = model
        self.http = httpx.Client(base_url=url.rstrip("/"), timeout=TIMEOUT_SECONDS,
                                 transport=transport)

    def call(self, *, purpose: str, role: str, system: str, user: str, max_tokens: int,
             schema: dict | None = None,
             on_text: Callable[[str], None] | None = None) -> LLMResult:
        body: dict[str, Any] = {
            "model": self.model, "stream": on_text is not None,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "options": {"num_predict": max_tokens}}
        if schema is not None:
            body["format"] = schema
        start = time.perf_counter()
        text, final = "", {}
        try:
            if on_text is None:
                response = self.http.post("/api/chat", json=body)
                response.raise_for_status()
                final = response.json()
                text = final.get("message", {}).get("content", "")
            else:
                with self.http.stream("POST", "/api/chat", json=body) as response:
                    response.raise_for_status()
                    for line in response.iter_lines():
                        if not line:
                            continue
                        part = json.loads(line)
                        piece = part.get("message", {}).get("content", "")
                        if piece:
                            text += piece
                            on_text(piece)
                        if part.get("done"):
                            final = part
        except (httpx.HTTPError, ValueError) as exc:
            raise PrivateModelError(f"private model ({self.model}) failed for {purpose}: {exc}") from None
        data = None
        if schema is not None:
            try:
                data = json.loads(text)
            except ValueError:
                raise PrivateModelError(f"private model gave no JSON for {purpose}") from None
        return LLMResult(text=text, data=data, model=f"ollama:{self.model}", effort="n/a",
                         tokens_in=int(final.get("prompt_eval_count", 0)),
                         tokens_out=int(final.get("eval_count", 0)),
                         latency_ms=(time.perf_counter() - start) * 1000)
