"""Typed inputs and outputs for the pricing engine.

These are the contract between the LLM's *proposal* and the deterministic
pricing engine. Note what `ProposedLineItem` does NOT have: any price field.
The model is structurally incapable of expressing a dollar amount — it may only
say "this service, this quantity". Pricing is code's job.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Unit(StrEnum):
    PER_SQFT = "per_sqft"
    PER_HOUR = "per_hour"
    PER_UNIT = "per_unit"
    FLAT = "flat"


class Urgency(StrEnum):
    STANDARD = "standard"
    URGENT = "urgent"  # needs scheduling within ~48h
    EMERGENCY = "emergency"  # storm damage, hazard


class AccessDifficulty(StrEnum):
    EASY = "easy"
    MODERATE = "moderate"  # slope, gates, tight side yard
    DIFFICULT = "difficult"  # steep grade, no vehicle access, hand-carry


class TravelZone(StrEnum):
    ZONE_1 = "zone_1"  # 0-10 mi
    ZONE_2 = "zone_2"  # 10-25 mi
    ZONE_3 = "zone_3"  # 25+ mi


class Season(StrEnum):
    PEAK = "peak"  # Apr-Jun
    SHOULDER = "shoulder"
    OFF_PEAK = "off_peak"  # Dec-Feb


class TrunkDiameterBand(StrEnum):
    """Trunk diameter at breast height. Drives the tree-removal surcharge."""

    UNDER_12 = "under_12in"
    D12_24 = "12_24in"
    D24_36 = "24_36in"
    OVER_36 = "over_36in"


# --- Engine input ----------------------------------------------------------


class ProposedLineItem(BaseModel):
    """What the LLM is allowed to propose. Deliberately has no price field."""

    model_config = ConfigDict(extra="forbid")

    catalog_code: str = Field(description="Service code from the catalog, e.g. MOW_STD")
    quantity: Decimal = Field(description="Quantity in the catalog item's unit")
    rationale: str = Field(default="", description="Why this service and quantity")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    trunk_diameter_band: TrunkDiameterBand | None = Field(
        default=None, description="Required for TREE_REMOVAL; ignored otherwise"
    )


class PricingContext(BaseModel):
    """Quote-level facts that drive adjustments. Derived by code, not invented."""

    model_config = ConfigDict(extra="forbid")

    urgency: Urgency = Urgency.STANDARD
    access_difficulty: AccessDifficulty = AccessDifficulty.EASY
    travel_zone: TravelZone = TravelZone.ZONE_1
    season: Season = Season.SHOULDER


# --- Engine output ---------------------------------------------------------


class AppliedRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    description: str
    delta_cents: int = Field(description="Signed effect of this rule on the amount")


class ComputedLineItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    catalog_code: str
    description: str
    quantity: Decimal
    unit: Unit
    unit_price_cents: int
    base_cents: int = Field(description="quantity x unit price, before line rules")
    subtotal_cents: int = Field(description="Final line amount after line-level rules")
    applied_rules: list[AppliedRule] = Field(default_factory=list)


class ComputedQuote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    line_items: list[ComputedLineItem]
    line_subtotal_cents: int
    adjustments: list[AppliedRule] = Field(default_factory=list)
    adjusted_subtotal_cents: int
    tax_cents: int
    total_cents: int
    engine_version: str
    context: PricingContext


class PricingError(ValueError):
    """Raised when the engine refuses to price something.

    This is a guardrail, not an exception to swallow: the orchestrator turns it
    into a route-to-human outcome rather than a quote.
    """

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code
