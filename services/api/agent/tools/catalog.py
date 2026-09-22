"""Catalog rendering for prompts.

The catalog block is identical on every request, so it is built once and
cached. That keeps it a stable prefix for prompt caching and, more practically,
means a catalog edit shows up as one diff rather than drifting between the two
prompts that embed it.
"""

from __future__ import annotations

from functools import lru_cache

from pricing.catalog_data import (
    CATALOG,
    GLOBAL_JOB_MINIMUM_CENTS,
    TREE_REMOVAL_SURCHARGE_CENTS,
    CatalogItem,
)
from pricing.money import fmt_money
from pricing.types import Unit

UNIT_HELP = {
    Unit.PER_SQFT: "per square foot",
    Unit.PER_HOUR: "per crew hour",
    Unit.PER_UNIT: "per unit (see description)",
    Unit.FLAT: "flat rate",
}


def _render_item(item: CatalogItem) -> str:
    return (
        f"- {item.code} | {item.name} | unit: {item.unit.value} ({UNIT_HELP[item.unit]})\n"
        f"    {item.description}"
    )


@lru_cache
def render_catalog() -> str:
    """The catalog as the model sees it. Deliberately without rates.

    The model does not need the rate card to choose a service and a quantity,
    and withholding it removes the temptation to do arithmetic the pricing
    engine owns.
    """
    by_type: dict[str, list[CatalogItem]] = {}
    for item in CATALOG:
        if item.active if hasattr(item, "active") else True:
            by_type.setdefault(item.service_type, []).append(item)

    blocks: list[str] = []
    for service_type in sorted(by_type):
        heading = service_type.replace("_", " ").title()
        lines = "\n".join(_render_item(i) for i in by_type[service_type])
        blocks.append(f"### {heading}\n{lines}")

    notes = (
        "\n### Notes\n"
        f"- Every job is subject to a minimum charge of {fmt_money(GLOBAL_JOB_MINIMUM_CENTS)}; "
        "you do not need to account for this.\n"
        "- TREE_REMOVAL requires a trunk diameter band: "
        + ", ".join(k.value for k in TREE_REMOVAL_SURCHARGE_CENTS)
        + ".\n"
        "- Riverside does not do: pools, fencing, decks, roofing, interior cleaning, "
        "paving, excavation, pest control, or anything not listed above."
    )
    return "\n\n".join(blocks) + notes


def catalog_codes() -> set[str]:
    return {i.code for i in CATALOG}
