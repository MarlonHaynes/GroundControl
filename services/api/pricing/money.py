"""Money handling.

Every monetary value in GroundControl is an integer number of cents. Decimal is
used for intermediate arithmetic and rounded to cents at each observable step
(per line item, then per quote-level adjustment) so that the stored line items
always sum exactly to the stored total. Floats never touch money.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

CENTS = Decimal("1")


def to_cents(value: Decimal | int | str) -> int:
    """Round a decimal amount of cents to a whole cent, half-up.

    Banker's rounding (Python's default) would round 0.5 to even, which produces
    totals that disagree with what a human checking the math on paper expects.
    Quotes are read by customers, so half-up is the correct choice here.
    """
    return int(Decimal(value).quantize(CENTS, rounding=ROUND_HALF_UP))


def dollars_to_cents(dollars: Decimal | int | str) -> int:
    return to_cents(Decimal(str(dollars)) * 100)


def fmt_money(cents: int) -> str:
    """Render cents as a display string: 12345 -> '$123.45'."""
    sign = "-" if cents < 0 else ""
    whole, frac = divmod(abs(cents), 100)
    return f"{sign}${whole:,}.{frac:02d}"


def apply_multiplier(cents: int, multiplier: Decimal) -> int:
    """Apply a multiplier to a cent amount, rounding half-up to the cent."""
    return to_cents(Decimal(cents) * multiplier)
