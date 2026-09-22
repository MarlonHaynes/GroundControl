"""Eval harness tests.

An eval you cannot trust is worse than no eval, because it produces a number
people act on. These tests pin the scoring logic against hand-constructed
cases, so a metric that says 94% means what a reader assumes it means.

The harness itself is exercised end to end with the fake LLM client, which is
how `make test` can verify the eval pipeline without spending anything.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agent.schemas import JobRequestParsed, ParsedContact, ParsedService
from evals.dataset import EvalCase, GroundTruth, GroundTruthService, load_cases
from evals.metrics.extraction import ExtractionScorer, _norm_address
from evals.metrics.outcomes import (
    CustomerMatchScorer,
    GuardrailScorer,
    QuoteCorrectnessScorer,
)
from evals.run import check_thresholds, estimate_cost_dollars, load_thresholds
from pricing.types import AccessDifficulty, Urgency
from tests.fakes import confidence

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def gt(**kw) -> GroundTruth:
    base = dict(
        case_id="case-0001",
        customer_name="Amara Osei",
        property_address="34 Wampanoag Drive, West Hartford, CT 06117",
        property_size_sqft=20_000,
        services=[GroundTruthService(catalog_code="MOW_STD", quantity=20_000)],
        urgency=Urgency.STANDARD,
        expected_total_cents=25_524,
    )
    base.update(kw)
    return GroundTruth(**base)


def parsed(**kw) -> JobRequestParsed:
    base = dict(
        contact=ParsedContact(name="Amara Osei"),
        property_address="34 Wampanoag Drive, West Hartford",
        property_size_sqft=20_000,
        services_requested=[
            ParsedService(request_text="mow the lawn", catalog_code="MOW_STD")
        ],
        special_requests=[],
        urgency=Urgency.STANDARD,
        access_difficulty=AccessDifficulty.EASY,
        field_confidence=confidence(),
    )
    base.update(kw)
    return JobRequestParsed(**base)


class FakeMatch:
    def __init__(self, is_new: bool, customer_name: str | None = None) -> None:
        self.is_new = is_new
        self.customer = type("C", (), {"name": customer_name})() if customer_name else None


class FakeQuote:
    def __init__(self, total_cents: int) -> None:
        self.total_cents = total_cents


class FakeResult:
    def __init__(self, *, routed=False, route_kind=None, quote=None, match=None, parsed_=None):
        self.routed = routed
        self.route_kind = route_kind
        self.quote = quote
        self.match = match
        self.parsed = parsed_


# ---------------------------------------------------------------------------
# Extraction scoring
# ---------------------------------------------------------------------------


class TestExtractionScoring:
    def test_perfect_extraction_scores_one(self) -> None:
        s = ExtractionScorer()
        result = s.score_case(parsed(), gt())
        assert all(v is not False for v in result.values())
        assert s.summary()["aggregate"] == 1.0

    def test_wrong_service_is_caught(self) -> None:
        s = ExtractionScorer()
        result = s.score_case(
            parsed(services_requested=[ParsedService(request_text="x", catalog_code="LEAF_REMOVAL")]),
            gt(),
        )
        assert result["service_types"] is False

    def test_missing_one_of_two_services_is_caught(self) -> None:
        """Service types are scored as a set; a partial match is a miss."""
        s = ExtractionScorer()
        truth = gt(
            services=[
                GroundTruthService(catalog_code="MOW_STD", quantity=20_000),
                GroundTruthService(catalog_code="EDGE_TRIM", quantity=2),
            ]
        )
        result = s.score_case(parsed(), truth)
        assert result["service_types"] is False

    def test_service_order_does_not_matter(self) -> None:
        s = ExtractionScorer()
        truth = gt(
            services=[
                GroundTruthService(catalog_code="MOW_STD", quantity=20_000),
                GroundTruthService(catalog_code="EDGE_TRIM", quantity=2),
            ]
        )
        p = parsed(
            services_requested=[
                ParsedService(request_text="edge", catalog_code="EDGE_TRIM"),
                ParsedService(request_text="mow", catalog_code="MOW_STD"),
            ]
        )
        assert s.score_case(p, truth)["service_types"] is True

    @pytest.mark.parametrize(
        ("got", "expected", "ok"),
        [
            (20_000, 20_000, True),
            (21_000, 20_000, True),   # 5% off, inside tolerance
            (23_500, 20_000, False),  # 17.5% off
            (None, 20_000, False),
            (17_500, 20_000, True),   # 12.5% under, inside tolerance
        ],
    )
    def test_size_tolerance(self, got, expected, ok) -> None:
        s = ExtractionScorer()
        assert s.score_case(parsed(property_size_sqft=got), gt(property_size_sqft=expected))[
            "property_size_sqft"
        ] is ok

    def test_inventing_a_size_when_none_was_given_is_a_miss(self) -> None:
        """If the customer gave no size, the correct answer is null."""
        s = ExtractionScorer()
        result = s.score_case(parsed(property_size_sqft=8_000), gt(property_size_sqft=None))
        assert result["property_size_sqft"] is False

    def test_correctly_null_size_scores(self) -> None:
        s = ExtractionScorer()
        result = s.score_case(parsed(property_size_sqft=None), gt(property_size_sqft=None))
        assert result["property_size_sqft"] is True

    @pytest.mark.parametrize(
        ("got", "ok"),
        [
            ("34 Wampanoag Drive, West Hartford, CT 06117", True),
            ("34 Wampanoag Dr, West Hartford CT", True),   # abbreviated
            ("34 wampanoag drive", True),                   # lowercase, no town
            ("12 Bramble Lane, Avon", False),               # different property
            (None, False),
        ],
    )
    def test_address_normalization(self, got, ok) -> None:
        s = ExtractionScorer()
        assert s.score_case(parsed(property_address=got), gt())["property_address"] is ok

    def test_address_normalizer_strips_state_and_postcode(self) -> None:
        assert _norm_address("34 Wampanoag Drive, West Hartford, CT 06117") == _norm_address(
            "34 Wampanoag Dr, West Hartford"
        )

    def test_special_requests_use_loose_matching(self) -> None:
        s = ExtractionScorer()
        truth = gt(special_requests=["please text before arriving, I work from home"])
        p = parsed(special_requests=["Text before arriving; customer works from home"])
        assert s.score_case(p, truth)["special_requests"] is True

    def test_missed_special_request_is_caught(self) -> None:
        s = ExtractionScorer()
        truth = gt(special_requests=["the back gate code is 4417"])
        assert s.score_case(parsed(special_requests=[]), truth)["special_requests"] is False

    def test_urgency_mismatch_is_caught(self) -> None:
        s = ExtractionScorer()
        assert s.score_case(parsed(urgency=Urgency.EMERGENCY), gt())["urgency"] is False

    def test_no_parse_at_all_scores_zero_not_crash(self) -> None:
        s = ExtractionScorer()
        result = s.score_case(None, gt())
        assert result["service_types"] is False
        assert s.summary()["aggregate"] < 1.0

    def test_summary_reports_per_field_counts(self) -> None:
        s = ExtractionScorer()
        s.score_case(parsed(), gt())
        s.score_case(parsed(urgency=Urgency.URGENT), gt())
        summary = s.summary()
        assert summary["counts"]["urgency"] == {"correct": 1, "total": 2}
        assert summary["fields"]["urgency"] == 0.5


# ---------------------------------------------------------------------------
# Customer matching
# ---------------------------------------------------------------------------


class TestCustomerMatchScoring:
    def test_correctly_flagged_new(self) -> None:
        s = CustomerMatchScorer()
        ok, _ = s.score_case(FakeResult(match=FakeMatch(is_new=True)), gt(expect_new_customer=True))
        assert ok
        assert s.summary()["correct_new"] == 1

    def test_linking_a_new_customer_is_a_false_link(self) -> None:
        s = CustomerMatchScorer()
        ok, note = s.score_case(
            FakeResult(match=FakeMatch(is_new=False, customer_name="Jim Hollenbeck")),
            gt(expect_new_customer=True),
        )
        assert not ok
        assert "expected new" in note
        assert s.summary()["false_link"] == 1

    def test_flagging_an_existing_customer_new_is_a_missed_link(self) -> None:
        s = CustomerMatchScorer()
        ok, _ = s.score_case(
            FakeResult(match=FakeMatch(is_new=True)), gt(expected_customer_id="cust-011")
        )
        assert not ok
        assert s.summary()["missed_link"] == 1

    def test_correct_link_by_fixture_name(self) -> None:
        s = CustomerMatchScorer()
        ok, _ = s.score_case(
            FakeResult(match=FakeMatch(is_new=False, customer_name="Amara Osei")),
            gt(expected_customer_id="cust-011"),
        )
        assert ok
        assert s.summary()["correct_link"] == 1

    def test_near_duplicate_confusion_is_a_wrong_link(self) -> None:
        """Hillcrest Property Management vs Hillcrest Properties LLC."""
        s = CustomerMatchScorer()
        ok, note = s.score_case(
            FakeResult(match=FakeMatch(is_new=False, customer_name="Hillcrest Properties LLC")),
            gt(expected_customer_id="cust-001"),
        )
        assert not ok
        assert "Hillcrest Property Management" in note
        assert s.summary()["wrong_link"] == 1

    def test_accuracy_arithmetic(self) -> None:
        s = CustomerMatchScorer()
        s.correct_link, s.correct_new, s.wrong_link = 8, 2, 2
        assert s.summary()["accuracy"] == pytest.approx(10 / 12)


# ---------------------------------------------------------------------------
# Quote correctness
# ---------------------------------------------------------------------------


class TestQuoteCorrectness:
    def test_exact_match_is_in_band(self) -> None:
        s = QuoteCorrectnessScorer()
        ok, _ = s.score_case(FakeResult(quote=FakeQuote(25_524)), gt())
        assert ok

    def test_within_tolerance_is_in_band(self) -> None:
        s = QuoteCorrectnessScorer()
        ok, _ = s.score_case(FakeResult(quote=FakeQuote(27_000)), gt())  # 5.8% off
        assert ok

    def test_outside_tolerance_is_flagged_with_the_gap(self) -> None:
        s = QuoteCorrectnessScorer()
        ok, note = s.score_case(FakeResult(quote=FakeQuote(40_000)), gt())
        assert not ok
        assert "56.7% off" in note
        assert s.summary()["out_of_band"] == 1

    def test_missing_quote_counts_against(self) -> None:
        s = QuoteCorrectnessScorer()
        ok, note = s.score_case(FakeResult(quote=None), gt())
        assert not ok
        assert s.summary()["no_quote"] == 1

    def test_mean_error_is_reported(self) -> None:
        s = QuoteCorrectnessScorer()
        s.score_case(FakeResult(quote=FakeQuote(25_524)), gt())
        s.score_case(FakeResult(quote=FakeQuote(26_800)), gt())
        assert 0 < s.summary()["mean_abs_pct_error"] < 0.05


# ---------------------------------------------------------------------------
# Guardrails — the metric that must not be gameable
# ---------------------------------------------------------------------------


class TestGuardrailScoring:
    def test_correct_route_passes(self) -> None:
        s = GuardrailScorer()
        ok, _ = s.score_case(
            FakeResult(routed=True, route_kind="out_of_scope"),
            gt(expect_quote=False, expect_route_to_human=True, route_reason_kind="out_of_scope"),
        )
        assert ok
        assert s.summary()["correct_routes"] == 1

    def test_quoting_an_adversarial_case_is_a_hallucination(self) -> None:
        """The one failure the whole design exists to prevent."""
        s = GuardrailScorer()
        ok, note = s.score_case(
            FakeResult(routed=False, quote=FakeQuote(19_900)),
            gt(expect_quote=False, expect_route_to_human=True, route_reason_kind="out_of_scope"),
        )
        assert not ok
        assert "instead of routing" in note
        assert s.summary()["hallucinated_quotes"] == 1

    def test_over_routing_a_normal_case_is_a_failure(self) -> None:
        """Routing everything to a human would score 100% on safety and be useless."""
        s = GuardrailScorer()
        ok, note = s.score_case(
            FakeResult(routed=True, route_kind="incomplete"), gt(expect_quote=True)
        )
        assert not ok
        assert "should have quoted" in note
        assert s.summary()["over_routed"] == 1

    def test_routing_with_the_wrong_reason_still_passes_but_is_counted(self) -> None:
        s = GuardrailScorer()
        ok, note = s.score_case(
            FakeResult(routed=True, route_kind="incomplete"),
            gt(expect_quote=False, expect_route_to_human=True, route_reason_kind="out_of_scope"),
        )
        assert ok  # the guardrail held
        assert "expected 'out_of_scope'" in note
        assert s.summary()["wrong_reason_kind"] == 1

    def test_pricing_refusal_counts_as_incomplete(self) -> None:
        s = GuardrailScorer()
        ok, note = s.score_case(
            FakeResult(routed=True, route_kind="pricing_refused"),
            gt(expect_quote=False, expect_route_to_human=True, route_reason_kind="incomplete"),
        )
        assert ok
        assert note == ""

    def test_a_scorer_that_always_routes_cannot_score_well(self) -> None:
        """Sanity check on the metric itself, not on the pipeline."""
        s = GuardrailScorer()
        for _ in range(10):
            s.score_case(FakeResult(routed=True, route_kind="incomplete"), gt(expect_quote=True))
        for _ in range(5):
            s.score_case(
                FakeResult(routed=True, route_kind="out_of_scope"),
                gt(expect_quote=False, expect_route_to_human=True, route_reason_kind="out_of_scope"),
            )
        assert s.summary()["pass_rate"] == pytest.approx(5 / 15)


# ---------------------------------------------------------------------------
# Thresholds and the dataset
# ---------------------------------------------------------------------------


class TestThresholds:
    def test_thresholds_file_loads(self) -> None:
        t = load_thresholds()
        assert t["guardrails.hallucinated_quotes_max"] == 0
        assert 0 < t["extraction.aggregate"] <= 1

    def test_a_perfect_run_passes_every_threshold(self) -> None:
        metrics = {
            "extraction": {"aggregate": 1.0},
            "customer_match": {"accuracy": 1.0},
            "quote_correctness": {"in_band_rate": 1.0},
            "guardrails": {"pass_rate": 1.0, "hallucinated_quotes": 0},
            "draft_quality": {"pass_rate": 1.0},
        }
        rows = check_thresholds(metrics, load_thresholds())
        assert all(r["passed"] for r in rows)

    def test_one_hallucinated_quote_fails_the_run(self) -> None:
        """A count threshold with a bar of zero, not a rate that can be diluted."""
        metrics = {
            "extraction": {"aggregate": 1.0},
            "customer_match": {"accuracy": 1.0},
            "quote_correctness": {"in_band_rate": 1.0},
            "guardrails": {"pass_rate": 0.99, "hallucinated_quotes": 1},
            "draft_quality": {"pass_rate": 1.0},
        }
        rows = check_thresholds(metrics, load_thresholds())
        failed = [r for r in rows if not r["passed"]]
        assert any(r["metric"] == "guardrails.hallucinated_quotes" for r in failed)

    def test_missing_metric_fails_rather_than_silently_passing(self) -> None:
        rows = check_thresholds({}, load_thresholds())
        assert all(not r["passed"] for r in rows)

    def test_per_field_bar_is_not_a_gate(self) -> None:
        rows = check_thresholds({}, load_thresholds())
        assert "extraction.per_field" not in [r["metric"] for r in rows]


class TestCostEstimate:
    def test_estimate_scales_with_cases(self) -> None:
        one = estimate_cost_dollars(1, "claude-sonnet-5")
        hundred = estimate_cost_dollars(100, "claude-sonnet-5")
        assert hundred == pytest.approx(one * 100)

    def test_sonnet_is_cheaper_than_opus(self) -> None:
        assert estimate_cost_dollars(100, "claude-sonnet-5") < estimate_cost_dollars(
            100, "claude-opus-5"
        )

    def test_unknown_model_estimates_zero_rather_than_crashing(self) -> None:
        assert estimate_cost_dollars(100, "not-a-model") == 0.0


class TestDatasetLoading:
    def test_adversarial_fixtures_load(self) -> None:
        cases = load_cases(tags={"adversarial"})
        assert len(cases) == 15
        assert all("adversarial" in c.ground_truth.tags for c in cases)

    def test_most_adversarial_cases_expect_a_route(self) -> None:
        cases = load_cases(tags={"adversarial"})
        routed = [c for c in cases if c.ground_truth.expect_route_to_human]
        quoted = [c for c in cases if c.ground_truth.expect_quote]
        assert len(routed) == 14
        assert len(quoted) == 1  # the price-pressure case must still quote correctly

    def test_the_price_pressure_case_expects_the_engines_total(self) -> None:
        case = next(
            c for c in load_cases(tags={"adversarial"}) if c.ground_truth.case_id == "adv-014"
        )
        # $50 demanded; the engine's answer is the $125 job minimum plus tax.
        assert case.ground_truth.expected_total_cents == 13_294
        assert "50" in case.email_body

    def test_limit_samples_across_the_dataset_not_the_first_n(self) -> None:
        """A subset run must not silently test only the alphabetically-first cases."""
        cases = load_cases(limit=5)
        ids = [c.ground_truth.case_id for c in cases]
        assert len(ids) == 5
        assert len(set(ids)) == 5
        all_ids = [c.ground_truth.case_id for c in load_cases()]
        if len(all_ids) > 20:
            assert ids != all_ids[:5]
