"""Pricing engine tests.

Every expected total here was computed by hand from the catalog and written
down before the assertion. If the engine changes, these numbers are what tell
us whether the change was intended.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from pricing.catalog_data import CATALOG, CATALOG_BY_CODE, GLOBAL_JOB_MINIMUM_CENTS
from pricing.engine import compute_quote
from pricing.money import apply_multiplier, dollars_to_cents, fmt_money, to_cents
from pricing.types import (
    AccessDifficulty,
    PricingContext,
    PricingError,
    ProposedLineItem,
    Season,
    TravelZone,
    TrunkDiameterBand,
    Unit,
    Urgency,
)


def line(code: str, qty: str | int, **kw) -> ProposedLineItem:
    return ProposedLineItem(catalog_code=code, quantity=Decimal(str(qty)), **kw)


# ---------------------------------------------------------------------------
# Money primitives
# ---------------------------------------------------------------------------


class TestMoney:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("0.4", 0),
            ("0.5", 1),  # half-up, not banker's (which would give 0)
            ("1.5", 2),
            ("2.5", 3),  # banker's would give 2
            ("-0.5", -1),
            ("100", 100),
        ],
    )
    def test_to_cents_rounds_half_up(self, value: str, expected: int) -> None:
        assert to_cents(Decimal(value)) == expected

    def test_dollars_to_cents(self) -> None:
        assert dollars_to_cents("12.34") == 1234
        assert dollars_to_cents(Decimal("0.012")) == 1  # 1.2 cents -> 1

    @pytest.mark.parametrize(
        ("cents", "expected"),
        [(0, "$0.00"), (5, "$0.05"), (1234, "$12.34"), (123456789, "$1,234,567.89"), (-250, "-$2.50")],
    )
    def test_fmt_money(self, cents: int, expected: str) -> None:
        assert fmt_money(cents) == expected

    def test_apply_multiplier(self) -> None:
        assert apply_multiplier(24000, Decimal("1.35")) == 32400
        assert apply_multiplier(32400, Decimal("1.08")) == 34992


# ---------------------------------------------------------------------------
# The structural guardrail: the model cannot express a price
# ---------------------------------------------------------------------------


class TestProposalCannotCarryPrices:
    """The LLM proposes services and quantities. It has no vocabulary for money.

    This is enforced by the schema, not by the prompt, which is why it is
    tested here rather than hoped for.
    """

    @pytest.mark.parametrize(
        "forbidden_field",
        ["unit_price_cents", "price", "subtotal_cents", "total", "amount", "cost"],
    )
    def test_price_fields_are_rejected(self, forbidden_field: str) -> None:
        with pytest.raises(ValidationError):
            ProposedLineItem(
                catalog_code="MOW_STD",
                quantity=Decimal("1000"),
                **{forbidden_field: 9999},
            )

    def test_proposal_schema_has_no_money_field(self) -> None:
        fields = set(ProposedLineItem.model_fields)
        assert fields == {
            "catalog_code",
            "quantity",
            "rationale",
            "confidence",
            "trunk_diameter_band",
        }


# ---------------------------------------------------------------------------
# Catalog integrity
# ---------------------------------------------------------------------------


class TestCatalog:
    def test_codes_are_unique(self) -> None:
        codes = [i.code for i in CATALOG]
        assert len(codes) == len(set(codes))

    def test_every_item_has_a_positive_rate_and_minimum(self) -> None:
        for item in CATALOG:
            assert Decimal(item.base_rate_cents) > 0, item.code
            assert item.min_charge_cents > 0, item.code

    def test_flat_items_have_matching_rate_and_minimum(self) -> None:
        for item in CATALOG:
            if item.unit is Unit.FLAT:
                assert Decimal(item.base_rate_cents) == item.min_charge_cents, item.code


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestBasicPricing:
    def test_single_line_standard_context(self) -> None:
        # MOW_STD: 20,000 sq ft x 1.2c = $240.00
        # no adjustments; tax 6.35% of 24000 = 1524.0 -> 1524
        q = compute_quote([line("MOW_STD", 20_000)])

        assert q.line_subtotal_cents == 24_000
        assert q.adjustments == []
        assert q.adjusted_subtotal_cents == 24_000
        assert q.tax_cents == 1_524
        assert q.total_cents == 25_524

    def test_line_records_its_unit_and_base(self) -> None:
        q = compute_quote([line("MOW_STD", 20_000)])
        li = q.line_items[0]
        assert li.catalog_code == "MOW_STD"
        assert li.unit is Unit.PER_SQFT
        assert li.base_cents == 24_000
        assert li.subtotal_cents == 24_000
        assert li.applied_rules == []

    def test_multi_line_quote(self) -> None:
        # MOW_STD 20,000 sqft   = 24,000
        # EDGE_TRIM 2 hours     = 11,600
        # DEBRIS_HAUL 3 cu yd   = 12,600
        # line subtotal         = 48,200
        # tax 48,200 * .0635    = 3060.7 -> 3061
        q = compute_quote(
            [line("MOW_STD", 20_000), line("EDGE_TRIM", 2), line("DEBRIS_HAUL", 3)]
        )
        assert [li.subtotal_cents for li in q.line_items] == [24_000, 11_600, 12_600]
        assert q.line_subtotal_cents == 48_200
        assert q.tax_cents == 3_061
        assert q.total_cents == 51_261

    def test_flat_rate_service(self) -> None:
        # IRRIGATION_WINTERIZE flat $125.00; tax 793.75 -> 794
        q = compute_quote([line("IRRIGATION_WINTERIZE", 1)])
        assert q.line_subtotal_cents == 12_500
        assert q.total_cents == 13_294


# ---------------------------------------------------------------------------
# Line-level rules
# ---------------------------------------------------------------------------


class TestLineMinimumCharge:
    def test_minimum_lifts_a_small_line(self) -> None:
        # MOW_STD 1,000 sqft = 1,200c, below the $65.00 mowing minimum
        q = compute_quote([line("MOW_STD", 1_000)])
        li = q.line_items[0]
        assert li.base_cents == 1_200
        assert li.subtotal_cents == 6_500
        assert [r.code for r in li.applied_rules] == ["LINE_MINIMUM_CHARGE"]
        assert li.applied_rules[0].delta_cents == 5_300

    def test_minimum_not_applied_when_base_exceeds_it(self) -> None:
        q = compute_quote([line("MOW_STD", 20_000)])
        assert "LINE_MINIMUM_CHARGE" not in [r.code for r in q.line_items[0].applied_rules]


class TestAccessDifficulty:
    def test_multiplier_applies_to_labor_sensitive_service(self) -> None:
        # MOW_STD 20,000 = 24,000 -> x1.35 = 32,400
        q = compute_quote(
            [line("MOW_STD", 20_000)],
            PricingContext(access_difficulty=AccessDifficulty.DIFFICULT),
        )
        li = q.line_items[0]
        assert li.subtotal_cents == 32_400
        assert [r.code for r in li.applied_rules] == ["ACCESS_DIFFICULTY_MODIFIER"]
        assert li.applied_rules[0].delta_cents == 8_400

    def test_moderate_access(self) -> None:
        # 24,000 x 1.15 = 27,600
        q = compute_quote(
            [line("MOW_STD", 20_000)],
            PricingContext(access_difficulty=AccessDifficulty.MODERATE),
        )
        assert q.line_items[0].subtotal_cents == 27_600

    def test_chemical_application_is_exempt(self) -> None:
        # FERT_APP is not labor-sensitive: a steep yard costs the same to spray.
        # 20,000 x 0.9c = 18,000, unchanged by difficult access.
        q = compute_quote(
            [line("FERT_APP", 20_000)],
            PricingContext(access_difficulty=AccessDifficulty.DIFFICULT),
        )
        assert q.line_items[0].subtotal_cents == 18_000
        assert q.line_items[0].applied_rules == []

    def test_minimum_is_applied_after_the_multiplier(self) -> None:
        """Order matters: min-then-multiply would overcharge by 35%.

        HEDGE_TRIM 2 shrubs = 3,600c. Difficult access -> 4,860c, still under
        the $95.00 minimum, so the line lands on the minimum exactly. If the
        minimum were applied first it would be lifted to 9,500 and then
        multiplied to 12,825.
        """
        q = compute_quote(
            [line("HEDGE_TRIM", 2)],
            PricingContext(access_difficulty=AccessDifficulty.DIFFICULT),
        )
        assert q.line_items[0].subtotal_cents == 9_500
        assert [r.code for r in q.line_items[0].applied_rules] == [
            "ACCESS_DIFFICULTY_MODIFIER",
            "LINE_MINIMUM_CHARGE",
        ]


class TestTreeRemovalSurcharge:
    @pytest.mark.parametrize(
        ("band", "expected_subtotal"),
        [
            (TrunkDiameterBand.UNDER_12, 65_000),
            (TrunkDiameterBand.D12_24, 92_500),
            (TrunkDiameterBand.D24_36, 130_000),
            (TrunkDiameterBand.OVER_36, 180_000),
        ],
    )
    def test_surcharge_by_band(self, band: TrunkDiameterBand, expected_subtotal: int) -> None:
        q = compute_quote([line("TREE_REMOVAL", 1, trunk_diameter_band=band)])
        assert q.line_items[0].subtotal_cents == expected_subtotal

    def test_surcharge_scales_with_tree_count(self) -> None:
        # 2 trees: base 130,000 + surcharge 2 x 115,000 = 360,000
        q = compute_quote(
            [line("TREE_REMOVAL", 2, trunk_diameter_band=TrunkDiameterBand.OVER_36)]
        )
        assert q.line_items[0].subtotal_cents == 360_000

    def test_missing_band_is_refused(self) -> None:
        """The price swings by $1,150 across bands. Guessing is not acceptable."""
        with pytest.raises(PricingError) as exc:
            compute_quote([line("TREE_REMOVAL", 1)])
        assert exc.value.code == "MISSING_TRUNK_DIAMETER"

    def test_band_is_ignored_for_other_services(self) -> None:
        q = compute_quote(
            [line("TREE_TRIM", 1, trunk_diameter_band=TrunkDiameterBand.OVER_36)]
        )
        assert q.line_items[0].subtotal_cents == 18_500
        assert q.line_items[0].applied_rules == []


# ---------------------------------------------------------------------------
# Quote-level adjustments
# ---------------------------------------------------------------------------


class TestQuoteAdjustments:
    def test_urgency_multiplier(self) -> None:
        # 24,000 x 1.20 = 28,800; tax 1828.8 -> 1829
        q = compute_quote([line("MOW_STD", 20_000)], PricingContext(urgency=Urgency.URGENT))
        assert q.adjusted_subtotal_cents == 28_800
        assert [a.code for a in q.adjustments] == ["URGENCY_MULTIPLIER"]
        assert q.tax_cents == 1_829
        assert q.total_cents == 30_629

    def test_seasonal_multipliers(self) -> None:
        peak = compute_quote([line("MOW_STD", 20_000)], PricingContext(season=Season.PEAK))
        off = compute_quote([line("MOW_STD", 20_000)], PricingContext(season=Season.OFF_PEAK))
        assert peak.adjusted_subtotal_cents == 25_920  # 24,000 x 1.08
        assert off.adjusted_subtotal_cents == 22_080  # 24,000 x 0.92

    def test_shoulder_season_records_no_adjustment(self) -> None:
        q = compute_quote([line("MOW_STD", 20_000)], PricingContext(season=Season.SHOULDER))
        assert [a.code for a in q.adjustments] == []

    @pytest.mark.parametrize(
        ("zone", "surcharge"),
        [(TravelZone.ZONE_1, 0), (TravelZone.ZONE_2, 4_500), (TravelZone.ZONE_3, 11_000)],
    )
    def test_travel_zone_surcharge(self, zone: TravelZone, surcharge: int) -> None:
        q = compute_quote([line("MOW_STD", 20_000)], PricingContext(travel_zone=zone))
        assert q.adjusted_subtotal_cents == 24_000 + surcharge

    def test_adjustment_order_is_multiply_then_add_travel(self) -> None:
        """Travel is a flat cost and must not be inflated by the rush multiplier.

        24,000 -> x1.35 emergency = 32,400 -> x1.08 peak = 34,992 -> +11,000
        travel = 45,992. Adding travel first would yield 50,922.
        """
        q = compute_quote(
            [line("MOW_STD", 20_000)],
            PricingContext(
                urgency=Urgency.EMERGENCY, season=Season.PEAK, travel_zone=TravelZone.ZONE_3
            ),
        )
        assert [a.code for a in q.adjustments] == [
            "URGENCY_MULTIPLIER",
            "SEASONAL_MULTIPLIER",
            "TRAVEL_ZONE_SURCHARGE",
        ]
        assert q.adjusted_subtotal_cents == 45_992
        assert q.tax_cents == 2_920
        assert q.total_cents == 48_912


class TestGlobalJobMinimum:
    def test_small_job_is_lifted_to_the_minimum(self) -> None:
        # DEBRIS_HAUL 1 cu yd = 4,200, under the $125.00 job minimum
        q = compute_quote([line("DEBRIS_HAUL", 1)])
        assert q.line_subtotal_cents == 4_200
        assert q.adjusted_subtotal_cents == GLOBAL_JOB_MINIMUM_CENTS
        assert [a.code for a in q.adjustments] == ["GLOBAL_JOB_MINIMUM"]
        assert q.adjustments[0].delta_cents == 8_300
        assert q.total_cents == 13_294

    def test_minimum_applies_before_tax(self) -> None:
        """Tax must be charged on what is actually billed, not the pre-minimum figure."""
        q = compute_quote([line("DEBRIS_HAUL", 1)])
        assert q.tax_cents == to_cents(Decimal(GLOBAL_JOB_MINIMUM_CENTS) * Decimal("0.0635"))

    def test_minimum_not_applied_above_threshold(self) -> None:
        q = compute_quote([line("MOW_STD", 20_000)])
        assert "GLOBAL_JOB_MINIMUM" not in [a.code for a in q.adjustments]


# ---------------------------------------------------------------------------
# Refusals — the engine's half of the guardrail contract
# ---------------------------------------------------------------------------


class TestEngineRefusals:
    def test_empty_proposal(self) -> None:
        with pytest.raises(PricingError) as exc:
            compute_quote([])
        assert exc.value.code == "EMPTY_PROPOSAL"

    def test_unknown_service_code(self) -> None:
        with pytest.raises(PricingError) as exc:
            compute_quote([line("POOL_CLEANING", 1)])
        assert exc.value.code == "UNKNOWN_SERVICE"

    @pytest.mark.parametrize("qty", ["0", "-1", "-0.5"])
    def test_non_positive_quantity(self, qty: str) -> None:
        with pytest.raises(PricingError) as exc:
            compute_quote([line("MOW_STD", qty)])
        assert exc.value.code == "NON_POSITIVE_QUANTITY"

    @pytest.mark.parametrize(
        ("code", "qty"),
        [("MOW_STD", 500_001), ("EDGE_TRIM", 201), ("HEDGE_TRIM", 501)],
    )
    def test_quantity_above_unit_ceiling(self, code: str, qty: int) -> None:
        with pytest.raises(PricingError) as exc:
            compute_quote([line(code, qty)])
        assert exc.value.code == "QUANTITY_OUT_OF_RANGE"

    def test_flat_service_rejects_quantity_other_than_one(self) -> None:
        with pytest.raises(PricingError) as exc:
            compute_quote([line("IRRIGATION_WINTERIZE", 3)])
        assert exc.value.code == "INVALID_FLAT_QUANTITY"

    def test_total_above_sanity_ceiling_is_refused(self) -> None:
        # SOD_INSTALL 30,000 sqft x $1.85 = $55,500 before tax
        with pytest.raises(PricingError) as exc:
            compute_quote([line("SOD_INSTALL", 30_000)])
        assert exc.value.code == "TOTAL_EXCEEDS_CEILING"

    def test_sanity_ceiling_is_configurable(self) -> None:
        with pytest.raises(PricingError) as exc:
            compute_quote([line("MOW_STD", 20_000)], sanity_ceiling_cents=10_000)
        assert exc.value.code == "TOTAL_EXCEEDS_CEILING"


# ---------------------------------------------------------------------------
# Invariants that must hold for every quote the system ever produces
# ---------------------------------------------------------------------------


class TestInvariants:
    CASES = [
        ([line("MOW_STD", 20_000)], PricingContext()),
        ([line("DEBRIS_HAUL", 1)], PricingContext()),
        (
            [line("MOW_STD", 15_000), line("EDGE_TRIM", 3), line("HEDGE_TRIM", 12)],
            PricingContext(
                urgency=Urgency.URGENT,
                access_difficulty=AccessDifficulty.MODERATE,
                travel_zone=TravelZone.ZONE_2,
                season=Season.PEAK,
            ),
        ),
        (
            [line("TREE_REMOVAL", 3, trunk_diameter_band=TrunkDiameterBand.D24_36)],
            PricingContext(urgency=Urgency.EMERGENCY),
        ),
        (
            [line("LEAF_REMOVAL", 40_000), line("DEBRIS_HAUL", 8)],
            PricingContext(season=Season.OFF_PEAK, travel_zone=TravelZone.ZONE_3),
        ),
    ]

    @pytest.mark.parametrize(("items", "ctx"), CASES)
    def test_line_items_sum_to_line_subtotal(self, items, ctx) -> None:
        q = compute_quote(items, ctx)
        assert sum(li.subtotal_cents for li in q.line_items) == q.line_subtotal_cents

    @pytest.mark.parametrize(("items", "ctx"), CASES)
    def test_adjustments_reconcile_to_adjusted_subtotal(self, items, ctx) -> None:
        q = compute_quote(items, ctx)
        expected = q.line_subtotal_cents + sum(a.delta_cents for a in q.adjustments)
        assert expected == q.adjusted_subtotal_cents

    @pytest.mark.parametrize(("items", "ctx"), CASES)
    def test_total_equals_subtotal_plus_tax(self, items, ctx) -> None:
        q = compute_quote(items, ctx)
        assert q.adjusted_subtotal_cents + q.tax_cents == q.total_cents

    @pytest.mark.parametrize(("items", "ctx"), CASES)
    def test_line_rules_reconcile_to_line_subtotal(self, items, ctx) -> None:
        q = compute_quote(items, ctx)
        for li in q.line_items:
            assert li.base_cents + sum(r.delta_cents for r in li.applied_rules) == li.subtotal_cents

    @pytest.mark.parametrize(("items", "ctx"), CASES)
    def test_totals_are_positive(self, items, ctx) -> None:
        q = compute_quote(items, ctx)
        assert q.total_cents > 0
        assert q.tax_cents >= 0

    @pytest.mark.parametrize(("items", "ctx"), CASES)
    def test_engine_is_deterministic(self, items, ctx) -> None:
        assert compute_quote(items, ctx) == compute_quote(items, ctx)

    def test_every_catalog_item_prices_without_error(self) -> None:
        """A smoke test over the whole price book, so a bad catalog entry can't hide."""
        for item in CATALOG:
            qty = Decimal(1) if item.unit is not Unit.PER_SQFT else Decimal(5_000)
            kw = {}
            if item.code == "TREE_REMOVAL":
                kw["trunk_diameter_band"] = TrunkDiameterBand.D12_24
            q = compute_quote([line(item.code, qty, **kw)])
            assert q.total_cents > 0, item.code

    def test_catalog_lookup_covers_every_item(self) -> None:
        assert set(CATALOG_BY_CODE) == {i.code for i in CATALOG}
