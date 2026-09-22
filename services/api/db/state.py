"""Quote lifecycle state machine.

The legal transitions are declared once, here, and every status change in the
system goes through `transition()`. Without this, "how does a quote become
sent?" would be answerable only by grepping for assignments to `.status`.
"""

from __future__ import annotations

from db.models import QuoteStatus

# draft -> pending_approval -> approved -> sent
#                           -> rejected
ALLOWED_TRANSITIONS: dict[QuoteStatus, frozenset[QuoteStatus]] = {
    QuoteStatus.DRAFT: frozenset({QuoteStatus.PENDING_APPROVAL}),
    QuoteStatus.PENDING_APPROVAL: frozenset({QuoteStatus.APPROVED, QuoteStatus.REJECTED}),
    QuoteStatus.APPROVED: frozenset({QuoteStatus.SENT}),
    QuoteStatus.REJECTED: frozenset(),  # terminal
    QuoteStatus.SENT: frozenset(),  # terminal
}


class IllegalTransition(ValueError):
    def __init__(self, current: QuoteStatus, target: QuoteStatus) -> None:
        allowed = sorted(s.value for s in ALLOWED_TRANSITIONS[current])
        super().__init__(
            f"Cannot move a quote from {current.value} to {target.value}. "
            f"Allowed from {current.value}: {allowed or ['(terminal)']}."
        )
        self.current = current
        self.target = target


def can_transition(current: QuoteStatus, target: QuoteStatus) -> bool:
    return target in ALLOWED_TRANSITIONS[current]


def transition(current: QuoteStatus, target: QuoteStatus) -> QuoteStatus:
    """Return `target` if the move is legal, else raise IllegalTransition."""
    if not can_transition(current, target):
        raise IllegalTransition(current, target)
    return target
