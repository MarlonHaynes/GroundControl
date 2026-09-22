"""draft_customer_email — LLM writes the email, code verifies every figure in it.

The verification is the interesting half. The model is given the computed
quote and told to use only those figures; `verify_draft` then checks that every
dollar amount in the body is one of them. One regeneration is allowed, after
which the request goes to a human rather than sending a draft nobody has
checked.
"""

from __future__ import annotations

import logging

from agent.guardrails import RouteToHuman, verify_draft
from agent.llm import LLMClient
from agent.prompts import render_prompt
from agent.schemas import DraftedEmail
from observability.tracer import StepHandle
from pricing.money import fmt_money
from pricing.types import ComputedQuote

logger = logging.getLogger(__name__)


def _amount_strings(quote: ComputedQuote) -> set[str]:
    """Every figure the draft is allowed to contain."""
    amounts: set[str] = set()

    def add(cents: int) -> None:
        amounts.add(fmt_money(cents).lstrip("$"))
        amounts.add(str(cents // 100))  # the customer-facing rounded dollars

    for li in quote.line_items:
        add(li.subtotal_cents)
        for rule in li.applied_rules:
            add(abs(rule.delta_cents))
    for adj in quote.adjustments:
        add(abs(adj.delta_cents))
    add(quote.line_subtotal_cents)
    add(quote.adjusted_subtotal_cents)
    add(quote.tax_cents)
    add(quote.total_cents)
    return amounts


def _render_quote(quote: ComputedQuote, *, quote_number: str, customer_name: str | None) -> str:
    lines = [f"Quote number: {quote_number}"]
    if customer_name:
        lines.append(f"Customer: {customer_name}")
    lines.append("\nLine items:")
    for li in quote.line_items:
        lines.append(f"  - {li.description}: {fmt_money(li.subtotal_cents)}")
        for rule in li.applied_rules:
            lines.append(f"      ({rule.description})")

    if quote.adjustments:
        lines.append("\nAdjustments:")
        for adj in quote.adjustments:
            sign = "+" if adj.delta_cents >= 0 else "-"
            lines.append(f"  - {adj.description}: {sign}{fmt_money(abs(adj.delta_cents))}")

    lines.append(f"\nSubtotal: {fmt_money(quote.adjusted_subtotal_cents)}")
    lines.append(f"Sales tax: {fmt_money(quote.tax_cents)}")
    lines.append(f"TOTAL: {fmt_money(quote.total_cents)}")
    return "\n".join(lines)


def draft_customer_email(
    *,
    client: LLMClient,
    quote: ComputedQuote,
    quote_number: str,
    customer_name: str | None,
    original_email: str,
    step: StepHandle | None = None,
    max_attempts: int = 2,
) -> DraftedEmail:
    system = render_prompt("draft_customer_email")
    allowed = _amount_strings(quote)

    base_prompt = (
        "Write the quote email for this customer.\n\n"
        "--- THEIR ORIGINAL REQUEST ---\n"
        f"{original_email}\n"
        "--- END REQUEST ---\n\n"
        "--- THE PRICED QUOTE (use only these figures) ---\n"
        f"{_render_quote(quote, quote_number=quote_number, customer_name=customer_name)}\n"
        "--- END QUOTE ---"
    )

    problems: list[str] = []
    for attempt in range(1, max_attempts + 1):
        prompt = base_prompt
        if problems:
            prompt += (
                "\n\nYour previous draft was rejected by an automated check:\n"
                + "\n".join(f"- {p}" for p in problems)
                + "\n\nWrite it again, using only the figures listed above."
            )

        result = client.structured(
            system=system,
            prompt=prompt,
            schema=DraftedEmail,
            temperature_key=f"attempt-{attempt}",
        )
        drafted: DraftedEmail = result.parsed  # type: ignore[assignment]

        if step is not None:
            step.record_usage(result.usage, cache_hit=result.cache_hit)

        problems = verify_draft(
            drafted.body,
            drafted.subject,
            quote_number=quote_number,
            allowed_amounts=allowed,
        )

        if not problems:
            if step is not None:
                step.set_output(
                    {"subject": drafted.subject, "body": drafted.body, "attempts": attempt}
                )
            return drafted

        logger.warning("draft attempt %d rejected: %s", attempt, problems)

    if step is not None:
        step.set_output({"rejected": problems, "attempts": max_attempts})

    raise RouteToHuman(
        "The drafted email failed verification twice: " + "; ".join(problems),
        kind="draft_unverified",
    )
