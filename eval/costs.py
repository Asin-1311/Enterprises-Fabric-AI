"""Token prices and spend measurement.

PRICES are the per-million-token prices listed on OpenRouter on 2026-11-07, used only for
ESTIMATES. The evaluation also MEASURES real spend as the drop in the OpenRouter key balance
between the start and end of a run; reports should quote the measured figure.
"""

import httpx

PRICES_USD_PER_MTOK: dict[str, tuple[float, float]] = {  # (input, output)
    "anthropic/claude-haiku-5.5": (0.10, 0.50),
    "anthropic/claude-sonnet-5.5": (2.00, 10.00),
    "anthropic/claude-opus-5.5": (4.00, 20.00),
}


def price_key(model: str) -> str:
    """The price-list name of a model id: "claude-haiku-5-5" (direct API) and
    "anthropic/claude-haiku-5.5" (OpenRouter) are the same model."""
    if model.startswith("anthropic/"):
        return model
    name, _, minor = model.rpartition("-")
    return f"anthropic/{name}.{minor}" if name else model


def estimate_usd(model: str, tokens_in: int, tokens_out: int) -> float:
    """Estimated cost of one call (0 for an unknown model)."""
    p_in, p_out = PRICES_USD_PER_MTOK.get(price_key(model), (0.0, 0.0))
    return p_in * tokens_in / 1e6 + p_out * tokens_out / 1e6


def openrouter_balance(base_url: str | None, token: str | None) -> float | None:
    """Remaining credit on the OpenRouter key, or None if not using OpenRouter."""
    if not token or not base_url or "openrouter.ai" not in base_url:
        return None
    try:
        r = httpx.get("https://openrouter.ai/api/v1/key",
                      headers={"Authorization": f"Bearer {token}"}, timeout=30)
        return float(r.json()["data"]["limit_remaining"])
    except Exception:  # noqa: BLE001 - spend measurement is best-effort
        return None
