"""Token accounting.

Costs are tracked in **micro-cents** (1 cent = 1,000 micro-cents). A single
Sonnet parse call costs a fraction of a cent, and rounding each step to a whole
cent would report every step as $0.00 and every run as $0.00 — which is exactly
the number a reviewer would call out as fake.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

MICROCENTS_PER_CENT = 1_000
MICROCENTS_PER_DOLLAR = 100 * MICROCENTS_PER_CENT


@dataclass(frozen=True)
class ModelPricing:
    """Dollars per million tokens, from the published Anthropic price list."""

    input_per_mtok: Decimal
    output_per_mtok: Decimal
    cache_read_per_mtok: Decimal


# Cached 2026-06-24. If a model is missing we fail loudly rather than
# silently reporting $0.00 for a run that really cost money.
PRICING: dict[str, ModelPricing] = {
    "claude-opus-5": ModelPricing(Decimal("5.00"), Decimal("25.00"), Decimal("0.50")),
    "claude-opus-4-8": ModelPricing(Decimal("5.00"), Decimal("25.00"), Decimal("0.50")),
    "claude-sonnet-5": ModelPricing(Decimal("2.00"), Decimal("10.00"), Decimal("0.20")),
    "claude-sonnet-4-6": ModelPricing(Decimal("3.00"), Decimal("15.00"), Decimal("0.30")),
    "claude-haiku-4-5": ModelPricing(Decimal("1.00"), Decimal("5.00"), Decimal("0.10")),
    "claude-fable-5-1": ModelPricing(Decimal("10.00"), Decimal("50.00"), Decimal("0.25")),
    "claude-fable-5": ModelPricing(Decimal("10.00"), Decimal("50.00"), Decimal("1.00")),
}


class UnknownModelPricing(KeyError):
    pass


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cache_read_tokens + other.cache_read_tokens,
        )


def cost_microcents(model: str, usage: Usage) -> int:
    """Cost of one call, in micro-cents, rounded to the nearest micro-cent."""
    try:
        p = PRICING[model]
    except KeyError as exc:
        raise UnknownModelPricing(
            f"No pricing entry for model {model!r}. Add it to observability/cost.py "
            f"rather than letting cost silently report as zero."
        ) from exc

    per_token = Decimal(MICROCENTS_PER_DOLLAR) / Decimal(1_000_000)
    total = (
        Decimal(usage.input_tokens) * p.input_per_mtok
        + Decimal(usage.output_tokens) * p.output_per_mtok
        + Decimal(usage.cache_read_tokens) * p.cache_read_per_mtok
    ) * per_token
    return int(total.to_integral_value(rounding="ROUND_HALF_UP"))


def fmt_microcents(microcents: int) -> str:
    """Render micro-cents for humans, with enough precision to be useful."""
    dollars = Decimal(microcents) / Decimal(MICROCENTS_PER_DOLLAR)
    if dollars >= 1:
        return f"${dollars:.2f}"
    if dollars >= Decimal("0.01"):
        return f"${dollars:.4f}"
    return f"${dollars:.6f}"
