from __future__ import annotations

from dataclasses import dataclass

import structlog

log = structlog.get_logger("flashback.usage")


@dataclass(frozen=True)
class ModelRate:
    input_per_mtok: float
    output_per_mtok: float
    cache_read_per_mtok: float = 0.0
    cache_write_per_mtok: float = 0.0


# Keyed by (provider, model). Rates are USD per 1,000,000 tokens.
# Anthropic rates are current (5-minute ephemeral cache: read 0.1x, write 1.25x).
# VERIFY the OpenAI and Voyage rates against the provider pricing pages before
# relying on the dashboard's dollar totals; they do not affect test correctness.
PRICING: dict[tuple[str, str], ModelRate] = {
    ("anthropic", "claude-sonnet-4-6"): ModelRate(3.0, 15.0, 0.30, 3.75),
    ("anthropic", "claude-haiku-4-5"): ModelRate(1.0, 5.0, 0.10, 1.25),
    ("openai", "gpt-5.1"): ModelRate(1.25, 10.0, 0.125, 0.0),  # VERIFY
    ("voyage", "voyage-3-large"): ModelRate(0.18, 0.0, 0.0, 0.0),  # VERIFY (input-only)
    ("voyage", "voyage-3"): ModelRate(0.06, 0.0, 0.0, 0.0),  # VERIFY (input-only)
}


def compute_cost(
    provider: str,
    model: str,
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float:
    rate = PRICING.get((provider, model))
    if rate is None:
        log.warning("usage.unknown_model", provider=provider, model=model)
        return 0.0
    return (
        (input_tokens or 0) * rate.input_per_mtok
        + (output_tokens or 0) * rate.output_per_mtok
        + (cache_read_tokens or 0) * rate.cache_read_per_mtok
        + (cache_write_tokens or 0) * rate.cache_write_per_mtok
    ) / 1_000_000.0
