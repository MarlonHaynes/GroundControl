"""Customer matching, quote correctness, guardrails, draft quality, performance.

Each scorer accumulates across cases and emits a summary dict that goes
straight into the eval run's metrics JSON and the dashboard.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from agent.loop import PipelineResult
from evals.dataset import GroundTruth
from observability.cost import fmt_microcents

QUOTE_TOLERANCE = 0.10


# ---------------------------------------------------------------------------
# Customer matching
# ---------------------------------------------------------------------------


@dataclass
class CustomerMatchScorer:
    correct_link: int = 0
    correct_new: int = 0
    wrong_link: int = 0  # linked, but to the wrong customer
    missed_link: int = 0  # should have linked, flagged new instead
    false_link: int = 0  # should have been new, linked to someone

    def score_case(self, result: PipelineResult, gt: GroundTruth) -> tuple[bool, str]:
        match = result.match
        if match is None:
            self.missed_link += 1
            return False, "no match attempted"

        if gt.expect_new_customer:
            if match.is_new:
                self.correct_new += 1
                return True, ""
            self.false_link += 1
            return False, f"linked to {match.customer.name if match.customer else '?'}, expected new"

        # Ground truth says this is an existing customer.
        if match.is_new:
            self.missed_link += 1
            return False, f"flagged new, expected {gt.expected_customer_id}"

        # The fixture customer id is not the database id, so compare on the
        # name the fixture was created from.
        expected = _fixture_name(gt.expected_customer_id)
        got = match.customer.name if match.customer else ""
        if expected and got and _same_account(expected, got):
            self.correct_link += 1
            return True, ""

        self.wrong_link += 1
        return False, f"linked to {got!r}, expected {expected!r}"

    @property
    def total(self) -> int:
        return (
            self.correct_link + self.correct_new + self.wrong_link
            + self.missed_link + self.false_link
        )

    def summary(self) -> dict[str, object]:
        total = self.total
        correct = self.correct_link + self.correct_new
        return {
            "accuracy": (correct / total) if total else 0.0,
            "correct_link": self.correct_link,
            "correct_new": self.correct_new,
            "wrong_link": self.wrong_link,
            "missed_link": self.missed_link,
            "false_link": self.false_link,
            "n_scored": total,
        }


_FIXTURE_NAMES: dict[str, str] | None = None


def _fixture_name(customer_id: str | None) -> str:
    global _FIXTURE_NAMES
    if customer_id is None:
        return ""
    if _FIXTURE_NAMES is None:
        from evals.dataset import load_customers

        _FIXTURE_NAMES = {c.id: c.name for c in load_customers()}
    return _FIXTURE_NAMES.get(customer_id, "")


def _same_account(a: str, b: str) -> bool:
    return a.strip().lower() == b.strip().lower()


# ---------------------------------------------------------------------------
# Quote correctness
# ---------------------------------------------------------------------------


@dataclass
class QuoteCorrectnessScorer:
    in_band: int = 0
    out_of_band: int = 0
    no_quote: int = 0
    errors: list[float] = field(default_factory=list)

    def score_case(self, result: PipelineResult, gt: GroundTruth) -> tuple[bool, str]:
        expected = gt.expected_total_cents
        if expected is None:
            return True, ""  # nothing to compare against

        if result.quote is None:
            self.no_quote += 1
            return False, f"no quote produced; expected {expected} cents"

        actual = result.quote.total_cents
        error = abs(actual - expected) / expected
        self.errors.append(error)

        if error <= QUOTE_TOLERANCE:
            self.in_band += 1
            return True, ""

        self.out_of_band += 1
        return False, f"total {actual} vs expected {expected} ({error:.1%} off)"

    def summary(self) -> dict[str, object]:
        scored = self.in_band + self.out_of_band + self.no_quote
        return {
            "in_band_rate": (self.in_band / scored) if scored else 0.0,
            "in_band": self.in_band,
            "out_of_band": self.out_of_band,
            "no_quote": self.no_quote,
            "n_scored": scored,
            "tolerance": QUOTE_TOLERANCE,
            "mean_abs_pct_error": statistics.fmean(self.errors) if self.errors else 0.0,
            "median_abs_pct_error": statistics.median(self.errors) if self.errors else 0.0,
        }


# ---------------------------------------------------------------------------
# Guardrails
# ---------------------------------------------------------------------------


@dataclass
class GuardrailScorer:
    """Scored on the adversarial cases only.

    `hallucinated_quotes` is reported as a count, not folded into a rate. One
    fabricated quote on a request that should have gone to a human is a
    different kind of event from a percentage point of accuracy, and the
    threshold on it is zero.
    """

    correct_routes: int = 0
    correct_quotes: int = 0
    hallucinated_quotes: int = 0
    over_routed: int = 0  # routed a case that should have been quoted
    wrong_reason_kind: int = 0

    def score_case(self, result: PipelineResult, gt: GroundTruth) -> tuple[bool, str]:
        if gt.expect_route_to_human:
            if not result.routed:
                self.hallucinated_quotes += 1
                total = result.quote.total_cents if result.quote else "?"
                return False, f"produced a quote ({total} cents) instead of routing to a human"

            self.correct_routes += 1
            # The kind is informational: the guardrail held either way, but a
            # mismatch means the reason shown to the reviewer is off.
            if (
                gt.route_reason_kind
                and result.route_kind
                and not _kind_matches(gt.route_reason_kind, result.route_kind)
            ):
                self.wrong_reason_kind += 1
                return True, (
                    f"routed correctly but as {result.route_kind!r}, "
                    f"expected {gt.route_reason_kind!r}"
                )
            return True, ""

        # Should have produced a quote.
        if result.routed:
            self.over_routed += 1
            return False, f"routed to human ({result.route_kind}) but should have quoted"

        self.correct_quotes += 1
        return True, ""

    @property
    def total(self) -> int:
        return self.correct_routes + self.correct_quotes + self.hallucinated_quotes + self.over_routed

    def summary(self) -> dict[str, object]:
        total = self.total
        correct = self.correct_routes + self.correct_quotes
        return {
            "pass_rate": (correct / total) if total else 0.0,
            "n_adversarial": total,
            "correct_routes": self.correct_routes,
            "correct_quotes": self.correct_quotes,
            "hallucinated_quotes": self.hallucinated_quotes,
            "over_routed": self.over_routed,
            "wrong_reason_kind": self.wrong_reason_kind,
        }


def _kind_matches(expected: str, actual: str) -> bool:
    """`pricing_refused` and `draft_unverified` are both ways of being incomplete."""
    equivalents = {
        "incomplete": {"incomplete", "pricing_refused", "draft_unverified"},
        "out_of_scope": {"out_of_scope"},
        "contradictory": {"contradictory"},
    }
    return actual in equivalents.get(expected, {expected})


# ---------------------------------------------------------------------------
# Draft quality
# ---------------------------------------------------------------------------


@dataclass
class DraftQualityScorer:
    passed: int = 0
    failed: int = 0
    failures: dict[str, int] = field(default_factory=dict)

    def score_case(self, result: PipelineResult, gt: GroundTruth) -> tuple[bool, str]:
        if result.quote is None or result.computed is None:
            return True, ""  # nothing drafted; not a draft-quality failure

        draft = result.quote.draft_messages[0] if result.quote.draft_messages else None
        if draft is None:
            self.failed += 1
            self._bump("no draft attached to a quote")
            return False, "quote has no draft message"

        from agent.guardrails import verify_draft
        from agent.tools.draft import _amount_strings

        problems = verify_draft(
            draft.body,
            draft.subject,
            quote_number=result.quote.quote_number,
            allowed_amounts=_amount_strings(result.computed),
        )

        if problems:
            self.failed += 1
            for p in problems:
                self._bump(_classify(p))
            return False, "; ".join(problems[:2])

        self.passed += 1
        return True, ""

    def _bump(self, key: str) -> None:
        self.failures[key] = self.failures.get(key, 0) + 1

    def summary(self) -> dict[str, object]:
        total = self.passed + self.failed
        return {
            "pass_rate": (self.passed / total) if total else 1.0,
            "n_checked": total,
            "passed": self.passed,
            "failed": self.failed,
            "failures": dict(sorted(self.failures.items(), key=lambda kv: -kv[1])),
        }


def _classify(problem: str) -> str:
    if "not a figure from the quote" in problem:
        return "invented figure"
    if "quote number" in problem:
        return "missing quote number"
    if "no dollar figure" in problem:
        return "no figures at all"
    if "placeholder" in problem:
        return "placeholder text"
    if "short" in problem:
        return "too short"
    return problem


# ---------------------------------------------------------------------------
# Cost and latency
# ---------------------------------------------------------------------------


@dataclass
class PerformanceScorer:
    tokens_in: list[int] = field(default_factory=list)
    tokens_out: list[int] = field(default_factory=list)
    costs: list[int] = field(default_factory=list)
    latencies: list[int] = field(default_factory=list)
    cache_hits: int = 0
    llm_calls: int = 0

    def score_case(self, result: PipelineResult) -> None:
        run = result.run
        self.tokens_in.append(run.usage.input_tokens)
        self.tokens_out.append(run.usage.output_tokens)
        self.costs.append(run.cost_microcents)
        self.latencies.append(run.latency_ms)
        for step in run.steps:
            if step.uses_llm:
                self.llm_calls += 1
                if step.cache_hit:
                    self.cache_hits += 1

    def summary(self) -> dict[str, object]:
        def pct(values: list[int], p: float) -> int:
            if not values:
                return 0
            ordered = sorted(values)
            return ordered[min(len(ordered) - 1, int(len(ordered) * p))]

        mean_cost = int(statistics.fmean(self.costs)) if self.costs else 0
        return {
            "mean_tokens_in": int(statistics.fmean(self.tokens_in)) if self.tokens_in else 0,
            "mean_tokens_out": int(statistics.fmean(self.tokens_out)) if self.tokens_out else 0,
            "mean_cost_microcents": mean_cost,
            "mean_cost_display": fmt_microcents(mean_cost),
            "total_cost_microcents": sum(self.costs),
            "total_cost_display": fmt_microcents(sum(self.costs)),
            "p50_latency_ms": pct(self.latencies, 0.5),
            "p95_latency_ms": pct(self.latencies, 0.95),
            "llm_calls": self.llm_calls,
            "cache_hits": self.cache_hits,
            "cache_hit_rate": (self.cache_hits / self.llm_calls) if self.llm_calls else 0.0,
        }
