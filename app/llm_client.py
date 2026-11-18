"""The single place where the Claude client is created.

This project reaches Claude through OpenRouter using the official `anthropic` SDK with
a custom base URL and a bearer token (LLM_BASE_URL + LLM_AUTH_TOKEN). With
ANTHROPIC_API_KEY set instead, the same code talks to the Anthropic API directly.

Note on sampling: the current models (Haiku / Sonnet / Opus 5.5) do not accept
`temperature`, `top_p` or `top_k`, so no call sends them. Behaviour is steered with
`output_config={"effort": ...}` (see Settings.effort_*), fixed prompts and structured
output instead.
"""

import anthropic

from app.config import Settings


def make_client(settings: Settings) -> anthropic.Anthropic:
    """Return an `anthropic.Anthropic` client configured from settings."""
    if settings.llm_auth_token:
        return anthropic.Anthropic(
            base_url=settings.llm_base_url,
            auth_token=settings.llm_auth_token,
            api_key=None,
        )
    return anthropic.Anthropic(api_key=settings.anthropic_api_key)
