"""Which model may read a request (spec section 12, "model data handling"; CLAUDE.md
section 12 item 5; Gap Register R-12-model; M13a).

Documents classified `restricted` may be read only by the firm's private model (Ollama,
PRIVATE_MODEL), never by an external one (Claude through the Anthropic API or OpenRouter).

How it is enforced: every place that gives document text to a model wraps it in a
<document> block (app/security/injection.py), and a restricted chunk's block carries
`classification="restricted"`. `ModelRouter` sits in front of the models and looks at
every request before it is sent:
- no restricted block  -> the external model, as before;
- a restricted block   -> the private model; if there is none, `PrivateModelUnavailable`
  is raised and nothing is sent anywhere (fail closed).
Document text cannot forge or hide the marker: `wrap_document` escapes any "<document" in
the text, and attribute values are escaped too. A request is routed by what it contains,
not by who built it, so a new agent step cannot forget to ask.
"""

import re
from collections.abc import Callable

from app.agents.llm import LLMCaller, LLMResult

_RESTRICTED_BLOCK = re.compile(r'<document\b[^>]*\bclassification="restricted"', re.IGNORECASE)

RESTRICTED_UNAVAILABLE_MESSAGE = (
    "Some of the documents this answer needs are classified restricted. Restricted material "
    "may be read only by the firm's private model, which is not available right now, so no "
    "answer was produced. No restricted text was sent to any external service. Please try "
    "again later or ask your engagement manager."
)


class PrivateModelUnavailable(RuntimeError):
    """A request contains restricted text and there is no private model to read it."""


def contains_restricted(text: str) -> bool:
    """True if `text` holds a <document> block of a restricted chunk."""
    return bool(_RESTRICTED_BLOCK.search(text or ""))


class ModelRouter:
    """An LLMCaller that sends each request to the external or the private model."""

    def __init__(self, external: LLMCaller, private: LLMCaller | None) -> None:
        self.external, self.private = external, private
        self.private_calls = 0

    def call(self, *, purpose: str, role: str, system: str, user: str, max_tokens: int,
             schema: dict | None = None,
             on_text: Callable[[str], None] | None = None) -> LLMResult:
        kwargs = dict(purpose=purpose, role=role, system=system, user=user,
                      max_tokens=max_tokens, schema=schema, on_text=on_text)
        if contains_restricted(system) or contains_restricted(user):
            if self.private is None:
                raise PrivateModelUnavailable(
                    f"{purpose}: restricted content and no private model configured")
            self.private_calls += 1
            return self.private.call(**kwargs)
        return self.external.call(**kwargs)
