"""propose_line_items — LLM proposes services and quantities. Never prices.

The model's output is validated against the catalog here rather than trusted.
An unknown service code is not a recoverable formatting slip; it means the
model invented a service Riverside does not sell, and the request goes to a
human.
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation

from agent.guardrails import RouteToHuman
from agent.llm import LLMClient
from agent.prompts import render_prompt
from agent.schemas import JobRequestParsed, ProposalResult
from agent.tools.catalog import catalog_codes, render_catalog
from observability.tracer import StepHandle
from pricing.types import ProposedLineItem, TrunkDiameterBand

VALID_BANDS = {b.value for b in TrunkDiameterBand}


def _render_request(parsed: JobRequestParsed) -> str:
    payload = {
        "property_address": parsed.property_address,
        "property_size_sqft": parsed.property_size_sqft,
        "services_requested": [s.model_dump() for s in parsed.services_requested],
        "special_requests": parsed.special_requests,
        "urgency": parsed.urgency.value,
        "access_difficulty": parsed.access_difficulty.value,
    }
    return (
        "Produce catalog line items for this parsed request.\n\n"
        + json.dumps(payload, indent=2)
    )


def propose_line_items(
    *,
    client: LLMClient,
    parsed: JobRequestParsed,
    step: StepHandle | None = None,
) -> list[ProposedLineItem]:
    system = render_prompt("propose_line_items", catalog=render_catalog())
    result = client.structured(
        system=system, prompt=_render_request(parsed), schema=ProposalResult
    )
    proposal: ProposalResult = result.parsed  # type: ignore[assignment]

    if step is not None:
        step.record_usage(result.usage, cache_hit=result.cache_hit)
        step.set_output(proposal.model_dump(mode="json"))

    if proposal.unpriceable_reason:
        raise RouteToHuman(proposal.unpriceable_reason, kind="incomplete")

    if not proposal.line_items:
        raise RouteToHuman(
            "No catalog line items could be produced for this request.", kind="incomplete"
        )

    known = catalog_codes()
    items: list[ProposedLineItem] = []

    for raw in proposal.line_items:
        code = raw.catalog_code.strip().upper()
        if code not in known:
            raise RouteToHuman(
                f"Proposed service {raw.catalog_code!r} is not in the Riverside catalog.",
                kind="out_of_scope",
            )

        try:
            quantity = Decimal(str(raw.quantity))
        except (InvalidOperation, ValueError):
            raise RouteToHuman(
                f"Proposed quantity {raw.quantity!r} for {code} is not a number.",
                kind="incomplete",
            ) from None

        band: TrunkDiameterBand | None = None
        if raw.trunk_diameter_band:
            value = raw.trunk_diameter_band.strip().lower()
            if value not in VALID_BANDS:
                raise RouteToHuman(
                    f"Trunk diameter band {raw.trunk_diameter_band!r} is not one of "
                    f"{sorted(VALID_BANDS)}.",
                    kind="incomplete",
                )
            band = TrunkDiameterBand(value)

        items.append(
            ProposedLineItem(
                catalog_code=code,
                quantity=quantity,
                rationale=raw.rationale,
                confidence=raw.confidence,
                trunk_diameter_band=band,
            )
        )

    return items
