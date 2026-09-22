"""The deterministic pricing engine.

This module is the reason the system can be trusted with money. It is pure:
no I/O, no database, no LLM, no clock. Given the same proposal and context it
returns the same quote, forever. Every test in `tests/test_pricing.py` pins a
behaviour here.

Rule application order is fixed and explicit:

    line level    10  base amount (quantity x unit rate)
                  11  tree-removal trunk-diameter surcharge
                  12  access-difficulty multiplier (labor-sensitive services)
                  13  per-line minimum charge
    quote level   30  urgency multiplier
                  31  seasonal multiplier
                  40  travel-zone surcharge
                  50  global job minimum
                  60  sales tax

Order matters and is not arbitrary: the per-line minimum is applied *after* the
access multiplier so that a difficult-access job never prices below the
minimum, and the global job minimum is applied *before* tax so that tax is
always charged on the amount actually billed.
"""

from __future__ import annotations

from decimal import Decimal

from pricing.catalog_data import (
    ACCESS_MULTIPLIER,
    CATALOG_BY_CODE,
    ENGINE_VERSION,
    GLOBAL_JOB_MINIMUM_CENTS,
    MAX_QUANTITY_BY_UNIT,
    SEASON_MULTIPLIER,
    TAX_RATE,
    TRAVEL_SURCHARGE_CENTS,
    TREE_REMOVAL_SURCHARGE_CENTS,
    URGENCY_MULTIPLIER,
    CatalogItem,
)
from pricing.money import apply_multiplier, fmt_money, to_cents
from pricing.types import (
    AccessDifficulty,
    AppliedRule,
    ComputedLineItem,
    ComputedQuote,
    PricingContext,
    PricingError,
    ProposedLineItem,
    TrunkDiameterBand,
    Unit,
)

TREE_REMOVAL_CODE = "TREE_REMOVAL"


def _validate_proposal(proposed: list[ProposedLineItem]) -> None:
    if not proposed:
        raise PricingError("No line items proposed; nothing to price.", code="EMPTY_PROPOSAL")

    for p in proposed:
        item = CATALOG_BY_CODE.get(p.catalog_code)
        if item is None:
            raise PricingError(
                f"Unknown service code {p.catalog_code!r}. Not in the Riverside catalog.",
                code="UNKNOWN_SERVICE",
            )

        qty = Decimal(p.quantity)
        if qty <= 0:
            raise PricingError(
                f"{p.catalog_code}: quantity must be positive, got {qty}.",
                code="NON_POSITIVE_QUANTITY",
            )

        # The flat-rate check comes before the generic ceiling check: both would
        # reject quantity 3 on a flat service, but "this service is flat-rate"
        # tells the reader what to do about it and "quantity exceeds 1" does not.
        if item.unit is Unit.FLAT and qty != Decimal(1):
            raise PricingError(
                f"{p.catalog_code} is a flat-rate service; quantity must be 1, got {qty}.",
                code="INVALID_FLAT_QUANTITY",
            )

        ceiling = MAX_QUANTITY_BY_UNIT[item.unit]
        if qty > ceiling:
            raise PricingError(
                f"{p.catalog_code}: quantity {qty} exceeds the sanity ceiling of "
                f"{ceiling} {item.unit}. This is an extraction failure, not a job.",
                code="QUANTITY_OUT_OF_RANGE",
            )

        if p.catalog_code == TREE_REMOVAL_CODE and p.trunk_diameter_band is None:
            raise PricingError(
                "Tree removal requires a trunk diameter band; the price varies by "
                "more than $1,100 across bands and cannot be guessed.",
                code="MISSING_TRUNK_DIAMETER",
            )


def _price_line(
    p: ProposedLineItem,
    item: CatalogItem,
    context: PricingContext,
) -> ComputedLineItem:
    qty = Decimal(p.quantity)
    rate = Decimal(item.base_rate_cents)

    # 10 — base amount
    base_cents = to_cents(qty * rate)
    running = base_cents
    rules: list[AppliedRule] = []

    # 11 — tree-removal trunk-diameter surcharge (per tree)
    if p.catalog_code == TREE_REMOVAL_CODE and p.trunk_diameter_band is not None:
        band: TrunkDiameterBand = p.trunk_diameter_band
        per_tree = TREE_REMOVAL_SURCHARGE_CENTS[band]
        if per_tree:
            delta = to_cents(qty * Decimal(per_tree))
            running += delta
            rules.append(
                AppliedRule(
                    code="TREE_DIAMETER_SURCHARGE",
                    description=(
                        f"Trunk diameter {band.value.replace('_', ' ')}: "
                        f"{fmt_money(per_tree)} per tree"
                    ),
                    delta_cents=delta,
                )
            )

    # 12 — access-difficulty multiplier, labor-sensitive services only.
    # Chemical applications and a winterization blowout cost the same whether
    # the yard is flat or steep, so they are exempt.
    if item.labor_sensitive and context.access_difficulty is not AccessDifficulty.EASY:
        mult = ACCESS_MULTIPLIER[context.access_difficulty]
        adjusted = apply_multiplier(running, mult)
        delta = adjusted - running
        running = adjusted
        rules.append(
            AppliedRule(
                code="ACCESS_DIFFICULTY_MODIFIER",
                description=f"{context.access_difficulty.value.title()} site access (x{mult})",
                delta_cents=delta,
            )
        )

    # 13 — per-line minimum charge
    if running < item.min_charge_cents:
        delta = item.min_charge_cents - running
        running = item.min_charge_cents
        rules.append(
            AppliedRule(
                code="LINE_MINIMUM_CHARGE",
                description=f"{item.name} minimum charge {fmt_money(item.min_charge_cents)}",
                delta_cents=delta,
            )
        )

    return ComputedLineItem(
        catalog_code=item.code,
        description=item.name,
        quantity=qty,
        unit=item.unit,
        unit_price_cents=to_cents(rate),
        base_cents=base_cents,
        subtotal_cents=running,
        applied_rules=rules,
    )


def compute_quote(
    proposed: list[ProposedLineItem],
    context: PricingContext | None = None,
    *,
    sanity_ceiling_cents: int = 5_000_000,
) -> ComputedQuote:
    """Price a proposal. Raises PricingError rather than returning a bad quote."""
    context = context or PricingContext()
    _validate_proposal(proposed)

    line_items = [
        _price_line(p, CATALOG_BY_CODE[p.catalog_code], context) for p in proposed
    ]
    line_subtotal = sum(li.subtotal_cents for li in line_items)

    running = line_subtotal
    adjustments: list[AppliedRule] = []

    # 30 — urgency
    if context.urgency is not None and URGENCY_MULTIPLIER[context.urgency] != Decimal("1.00"):
        mult = URGENCY_MULTIPLIER[context.urgency]
        adjusted = apply_multiplier(running, mult)
        adjustments.append(
            AppliedRule(
                code="URGENCY_MULTIPLIER",
                description=f"{context.urgency.value.title()} scheduling (x{mult})",
                delta_cents=adjusted - running,
            )
        )
        running = adjusted

    # 31 — season
    if SEASON_MULTIPLIER[context.season] != Decimal("1.00"):
        mult = SEASON_MULTIPLIER[context.season]
        adjusted = apply_multiplier(running, mult)
        adjustments.append(
            AppliedRule(
                code="SEASONAL_MULTIPLIER",
                description=f"{context.season.value.replace('_', ' ').title()} season (x{mult})",
                delta_cents=adjusted - running,
            )
        )
        running = adjusted

    # 40 — travel zone
    travel = TRAVEL_SURCHARGE_CENTS[context.travel_zone]
    if travel:
        adjustments.append(
            AppliedRule(
                code="TRAVEL_ZONE_SURCHARGE",
                description=(
                    f"Travel {context.travel_zone.value.replace('_', ' ')} "
                    f"surcharge {fmt_money(travel)}"
                ),
                delta_cents=travel,
            )
        )
        running += travel

    # 50 — global job minimum
    if running < GLOBAL_JOB_MINIMUM_CENTS:
        delta = GLOBAL_JOB_MINIMUM_CENTS - running
        adjustments.append(
            AppliedRule(
                code="GLOBAL_JOB_MINIMUM",
                description=f"Minimum job charge {fmt_money(GLOBAL_JOB_MINIMUM_CENTS)}",
                delta_cents=delta,
            )
        )
        running = GLOBAL_JOB_MINIMUM_CENTS

    adjusted_subtotal = running

    # 60 — tax
    tax_cents = to_cents(Decimal(adjusted_subtotal) * TAX_RATE)
    total_cents = adjusted_subtotal + tax_cents

    # Final guardrails. These exist because a plausible-looking quote with an
    # absurd total is the single most expensive failure mode in this system.
    if total_cents <= 0:
        raise PricingError(
            f"Computed a non-positive total ({fmt_money(total_cents)}).",
            code="NON_POSITIVE_TOTAL",
        )
    if total_cents >= sanity_ceiling_cents:
        raise PricingError(
            f"Computed total {fmt_money(total_cents)} meets or exceeds the sanity "
            f"ceiling {fmt_money(sanity_ceiling_cents)}. Refusing to quote; a human "
            f"should price this job.",
            code="TOTAL_EXCEEDS_CEILING",
        )

    return ComputedQuote(
        line_items=line_items,
        line_subtotal_cents=line_subtotal,
        adjustments=adjustments,
        adjusted_subtotal_cents=adjusted_subtotal,
        tax_cents=tax_cents,
        total_cents=total_cents,
        engine_version=ENGINE_VERSION,
        context=context,
    )
