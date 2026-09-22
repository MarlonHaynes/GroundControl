"""Riverside Grounds' service catalog and pricing rules.

This is the customer's actual price book as captured during discovery. It is the
source of truth for `make seed` and for the pricing engine's tests. Rates are
2026 season rates for the Hartford County service area.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from pricing.types import AccessDifficulty, Season, TravelZone, TrunkDiameterBand, Unit, Urgency


@dataclass(frozen=True)
class CatalogItem:
    code: str
    name: str
    service_type: str
    unit: Unit
    # Decimal because sub-cent unit rates are real: mowing is 1.2 cents per
    # square foot, and rounding that to a whole cent would move a 30,000 sq ft
    # quote by $60. Rounding happens once, at the line total.
    base_rate_cents: Decimal | int
    min_charge_cents: int
    description: str
    # Services whose cost is driven by on-site labor, and so are affected by
    # how hard the property is to work on.
    labor_sensitive: bool = True
    keywords: tuple[str, ...] = field(default_factory=tuple)


CATALOG: tuple[CatalogItem, ...] = (
    # --- Mowing & routine maintenance -------------------------------------
    CatalogItem(
        code="MOW_STD",
        name="Lawn Mowing (Standard)",
        service_type="maintenance",
        unit=Unit.PER_SQFT,
        base_rate_cents=Decimal("1.2"),  # $0.012/sq ft
        min_charge_cents=6_500,
        description="Routine mow, blow and go on maintained turf.",
        keywords=("mow", "mowing", "cut the grass", "lawn cut", "grass cut"),
    ),
    CatalogItem(
        code="MOW_ROUGH",
        name="Rough / Overgrown Cut",
        service_type="maintenance",
        unit=Unit.PER_SQFT,
        base_rate_cents=Decimal("2.8"),  # $0.028/sq ft
        min_charge_cents=14_500,
        description="First cut on overgrown or neglected turf; brush-hog or high-deck pass.",
        keywords=("overgrown", "knee high", "hasn't been cut", "jungle", "waist high"),
    ),
    CatalogItem(
        code="EDGE_TRIM",
        name="Edging & String Trimming",
        service_type="maintenance",
        unit=Unit.PER_HOUR,
        base_rate_cents=5_800,
        min_charge_cents=5_800,
        description="Hard-edge walkways and beds, string trim fence lines and obstacles.",
        keywords=("edging", "edge", "string trim", "weed whack", "weedwacking"),
    ),
    CatalogItem(
        code="HEDGE_TRIM",
        name="Hedge & Shrub Trimming",
        service_type="maintenance",
        unit=Unit.PER_UNIT,
        base_rate_cents=1_800,
        min_charge_cents=9_500,
        description="Shape and reduce hedges and ornamental shrubs. Priced per shrub.",
        keywords=("hedge", "hedges", "shrub", "bushes", "boxwood", "privet"),
    ),
    CatalogItem(
        code="FERT_APP",
        name="Fertilizer Application",
        service_type="turf_care",
        unit=Unit.PER_SQFT,
        base_rate_cents=Decimal("0.9"),
        min_charge_cents=8_500,
        description="Granular fertilizer application, single round.",
        labor_sensitive=False,
        keywords=("fertilize", "fertilizer", "feed the lawn", "turf food"),
    ),
    CatalogItem(
        code="WEED_CTRL",
        name="Weed Control Treatment",
        service_type="turf_care",
        unit=Unit.PER_SQFT,
        base_rate_cents=Decimal("0.7"),
        min_charge_cents=7_500,
        description="Broadleaf weed control, single application.",
        labor_sensitive=False,
        keywords=("weeds", "weed control", "dandelions", "crabgrass"),
    ),
    # --- Cleanup -----------------------------------------------------------
    CatalogItem(
        code="LEAF_REMOVAL",
        name="Leaf Removal",
        service_type="cleanup",
        unit=Unit.PER_SQFT,
        base_rate_cents=Decimal("1.5"),
        min_charge_cents=12_000,
        description="Blow, collect and remove leaf litter from turf and beds.",
        keywords=("leaves", "leaf removal", "leaf cleanup", "fall leaves"),
    ),
    CatalogItem(
        code="PROPERTY_CLEANUP",
        name="Spring / Fall Property Cleanup",
        service_type="cleanup",
        unit=Unit.PER_HOUR,
        base_rate_cents=6_200,
        min_charge_cents=18_600,  # 3-hour crew minimum
        description="Full property cleanup: beds, borders, winter debris, cutbacks.",
        keywords=("spring cleanup", "fall cleanup", "clean up the yard", "tidy up"),
    ),
    CatalogItem(
        code="DEBRIS_HAUL",
        name="Debris Hauling & Disposal",
        service_type="cleanup",
        unit=Unit.PER_UNIT,
        base_rate_cents=4_200,
        min_charge_cents=4_200,
        description="Load and dispose of green waste. Priced per cubic yard.",
        keywords=("haul away", "dispose", "take it away", "debris", "dump"),
    ),
    # --- Tree work ---------------------------------------------------------
    CatalogItem(
        code="TREE_TRIM",
        name="Tree Trimming / Pruning",
        service_type="tree_work",
        unit=Unit.PER_UNIT,
        base_rate_cents=18_500,
        min_charge_cents=18_500,
        description="Crown cleaning and deadwood removal. Priced per tree.",
        keywords=("trim the tree", "prune", "pruning", "tree trimming", "limbs"),
    ),
    CatalogItem(
        code="TREE_REMOVAL",
        name="Tree Removal",
        service_type="tree_work",
        unit=Unit.PER_UNIT,
        base_rate_cents=65_000,
        min_charge_cents=65_000,
        description="Full tree takedown. Surcharged by trunk diameter band.",
        keywords=("remove the tree", "take down", "cut down the tree", "tree removal"),
    ),
    CatalogItem(
        code="STUMP_GRIND",
        name="Stump Grinding",
        service_type="tree_work",
        unit=Unit.PER_UNIT,
        base_rate_cents=14_500,
        min_charge_cents=14_500,
        description="Grind stump to 6 inches below grade. Priced per stump.",
        keywords=("stump", "grind", "stump removal"),
    ),
    # --- Installation ------------------------------------------------------
    CatalogItem(
        code="MULCH_INSTALL",
        name="Mulch Installation",
        service_type="installation",
        unit=Unit.PER_UNIT,
        base_rate_cents=9_500,
        min_charge_cents=19_000,  # 2 yd minimum
        description="Supply and install hardwood mulch. Priced per cubic yard.",
        keywords=("mulch", "mulching", "bark", "beds refreshed"),
    ),
    CatalogItem(
        code="SOD_INSTALL",
        name="Sod Installation",
        service_type="installation",
        unit=Unit.PER_SQFT,
        base_rate_cents=185,  # $1.85/sq ft
        min_charge_cents=45_000,
        description="Grade, prep and lay sod including delivery.",
        keywords=("sod", "new lawn", "turf install", "re-sod"),
    ),
    # --- Irrigation --------------------------------------------------------
    CatalogItem(
        code="IRRIGATION_REPAIR",
        name="Irrigation System Repair",
        service_type="irrigation",
        unit=Unit.PER_HOUR,
        base_rate_cents=8_800,
        min_charge_cents=13_200,
        description="Diagnose and repair heads, valves, and controller faults.",
        keywords=("sprinkler", "irrigation", "zone not working", "head broken"),
    ),
    CatalogItem(
        code="IRRIGATION_WINTERIZE",
        name="Irrigation Winterization",
        service_type="irrigation",
        unit=Unit.FLAT,
        base_rate_cents=12_500,
        min_charge_cents=12_500,
        description="Blow out irrigation lines for winter. Flat rate per system.",
        labor_sensitive=False,
        keywords=("winterize", "blow out", "blowout", "shut down the sprinklers"),
    ),
    # --- Seasonal / other --------------------------------------------------
    CatalogItem(
        code="SNOW_REMOVAL",
        name="Snow Removal (Per Visit)",
        service_type="seasonal",
        unit=Unit.PER_HOUR,
        base_rate_cents=9_500,
        min_charge_cents=14_250,
        description="Plow and shovel service, billed per crew hour on site.",
        keywords=("snow", "plow", "plowing", "shovel"),
    ),
    CatalogItem(
        code="GUTTER_CLEAN",
        name="Gutter Cleaning",
        service_type="cleanup",
        unit=Unit.PER_UNIT,
        base_rate_cents=325,  # per linear foot
        min_charge_cents=14_500,
        description="Clear gutters and downspouts. Priced per linear foot.",
        keywords=("gutter", "gutters", "downspout"),
    ),
)

CATALOG_BY_CODE: dict[str, CatalogItem] = {item.code: item for item in CATALOG}


# --- Pricing rules ---------------------------------------------------------
# Applied in `apply_order`. Line-level rules run first (10-29), then
# quote-level adjustments (30-49), then the global minimum (50) and tax (60).

TREE_REMOVAL_SURCHARGE_CENTS: dict[TrunkDiameterBand, int] = {
    TrunkDiameterBand.UNDER_12: 0,
    TrunkDiameterBand.D12_24: 27_500,
    TrunkDiameterBand.D24_36: 65_000,
    TrunkDiameterBand.OVER_36: 115_000,
}

ACCESS_MULTIPLIER: dict[AccessDifficulty, Decimal] = {
    AccessDifficulty.EASY: Decimal("1.00"),
    AccessDifficulty.MODERATE: Decimal("1.15"),
    AccessDifficulty.DIFFICULT: Decimal("1.35"),
}

URGENCY_MULTIPLIER: dict[Urgency, Decimal] = {
    Urgency.STANDARD: Decimal("1.00"),
    Urgency.URGENT: Decimal("1.20"),
    Urgency.EMERGENCY: Decimal("1.35"),
}

SEASON_MULTIPLIER: dict[Season, Decimal] = {
    Season.PEAK: Decimal("1.08"),
    Season.SHOULDER: Decimal("1.00"),
    Season.OFF_PEAK: Decimal("0.92"),
}

TRAVEL_SURCHARGE_CENTS: dict[TravelZone, int] = {
    TravelZone.ZONE_1: 0,
    TravelZone.ZONE_2: 4_500,
    TravelZone.ZONE_3: 11_000,
}

# Riverside will not roll a truck for less than this.
GLOBAL_JOB_MINIMUM_CENTS = 12_500

# Connecticut sales tax on landscaping services.
TAX_RATE = Decimal("0.0635")

# Per-unit sanity ceilings. A quantity above these is a parsing failure, not a
# real job, and the engine refuses rather than pricing a $400k mow.
MAX_QUANTITY_BY_UNIT: dict[Unit, Decimal] = {
    Unit.PER_SQFT: Decimal("500000"),  # ~11.5 acres
    Unit.PER_HOUR: Decimal("200"),
    Unit.PER_UNIT: Decimal("500"),
    Unit.FLAT: Decimal("1"),
}

ENGINE_VERSION = "1.0.0"
