"""The human decision layer.

This is the only place in the system that can resolve an approval, and
`approve()` is the only caller of `send_quote`. The agent cannot reach any of
it.

Editing before approval re-prices through the engine rather than accepting the
human's arithmetic. An office manager correcting "that's 12,000 sq ft not
20,000" should change the quantity and get a correct total, not have to work
out the tax themselves.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from agent.guardrails import ApprovalRequired
from agent.tools.send import send_quote
from db.models import (
    Approval,
    ApprovalAction,
    ApprovalStatus,
    DraftMessage,
    LineItem,
    LineItemSource,
    Quote,
    QuoteStatus,
    SentMessage,
    ServiceCatalogItem,
)
from db.state import IllegalTransition, transition
from integrations.base import MessagingAdapter
from pricing.engine import compute_quote
from pricing.types import (
    AccessDifficulty,
    PricingContext,
    PricingError,
    ProposedLineItem,
    Season,
    TravelZone,
    TrunkDiameterBand,
    Urgency,
)

logger = logging.getLogger(__name__)


class ApprovalConflict(ValueError):
    """The requested decision cannot be applied to this quote's current state."""


@dataclass
class LineItemEdit:
    catalog_code: str
    quantity: Decimal
    trunk_diameter_band: str | None = None
    rationale: str = ""


def _pending_approval(db: Session, quote: Quote) -> Approval:
    pending = [a for a in quote.approvals if a.status is ApprovalStatus.PENDING]
    if not pending:
        raise ApprovalConflict(
            f"Quote {quote.quote_number} has no pending approval to act on "
            f"(status is {quote.status.value})."
        )
    return pending[0]


def _context_from(quote: Quote) -> PricingContext:
    ctx = quote.pricing_context or {}
    return PricingContext(
        urgency=Urgency(ctx.get("urgency", "standard")),
        access_difficulty=AccessDifficulty(ctx.get("access_difficulty", "easy")),
        travel_zone=TravelZone(ctx.get("travel_zone", "zone_1")),
        season=Season(ctx.get("season", "shoulder")),
    )


def edit_quote(
    db: Session,
    *,
    quote: Quote,
    line_items: list[LineItemEdit],
    actor: str,
    notes: str | None = None,
    draft_subject: str | None = None,
    draft_body: str | None = None,
) -> Quote:
    """Replace the quote's line items and re-price deterministically.

    The quote stays in `pending_approval`: an edit is not an approval. The
    editor still has to approve the corrected quote, which is what makes the
    audit trail honest about who agreed to what.
    """
    if quote.status is not QuoteStatus.PENDING_APPROVAL:
        raise ApprovalConflict(
            f"Quote {quote.quote_number} is {quote.status.value} and cannot be edited."
        )

    if not line_items:
        raise ApprovalConflict("An edit must leave at least one line item.")

    proposed = [
        ProposedLineItem(
            catalog_code=e.catalog_code,
            quantity=Decimal(str(e.quantity)),
            rationale=e.rationale or "Edited by a human reviewer.",
            confidence=1.0,
            trunk_diameter_band=(
                TrunkDiameterBand(e.trunk_diameter_band) if e.trunk_diameter_band else None
            ),
        )
        for e in line_items
    ]

    try:
        computed = compute_quote(proposed, _context_from(quote))
    except PricingError as exc:
        raise ApprovalConflict(f"The edited quote could not be priced: {exc}") from exc

    before = {
        "total_cents": quote.total_cents,
        "line_items": [
            {"catalog_code": li.catalog_code, "quantity": str(li.quantity)}
            for li in quote.line_items
        ],
    }

    for li in list(quote.line_items):
        db.delete(li)
    db.flush()

    catalog_ids = {row.code: row.id for row in db.query(ServiceCatalogItem).all()}
    for position, li in enumerate(computed.line_items):
        db.add(
            LineItem(
                quote_id=quote.id,
                position=position,
                catalog_item_id=catalog_ids.get(li.catalog_code),
                catalog_code=li.catalog_code,
                description=li.description,
                quantity=li.quantity,
                unit=li.unit.value,
                unit_price_cents=li.unit_price_cents,
                base_cents=li.base_cents,
                subtotal_cents=li.subtotal_cents,
                applied_rules=[r.model_dump(mode="json") for r in li.applied_rules],
                source=LineItemSource.HUMAN_EDITED,
            )
        )

    quote.line_subtotal_cents = computed.line_subtotal_cents
    quote.adjustments = [a.model_dump(mode="json") for a in computed.adjustments]
    quote.adjusted_subtotal_cents = computed.adjusted_subtotal_cents
    quote.tax_cents = computed.tax_cents
    quote.total_cents = computed.total_cents
    quote.priced_by_engine_version = computed.engine_version

    if draft_subject is not None or draft_body is not None:
        draft = quote.draft_messages[0] if quote.draft_messages else None
        if draft is not None:
            if draft_subject is not None:
                draft.edited_subject = draft_subject
            if draft_body is not None:
                draft.edited_body = draft_body

    approval = _pending_approval(db, quote)
    history = list(approval.edited_fields.get("history", [])) if approval.edited_fields else []
    history.append(
        {
            "actor": actor,
            "at": datetime.now(UTC).isoformat(),
            "notes": notes,
            "before": before,
            "after": {
                "total_cents": computed.total_cents,
                "line_items": [
                    {"catalog_code": li.catalog_code, "quantity": str(li.quantity)}
                    for li in computed.line_items
                ],
            },
        }
    )
    approval.edited_fields = {"history": history}

    db.flush()
    db.refresh(quote)
    logger.info(
        "quote %s edited by %s: %d -> %d cents",
        quote.quote_number, actor, before["total_cents"], quote.total_cents,
    )
    return quote


def approve(
    db: Session,
    *,
    quote: Quote,
    actor: str,
    notes: str | None = None,
    adapter: MessagingAdapter | None = None,
) -> tuple[Approval, SentMessage]:
    """Resolve the approval and send. The only path to a customer-facing send."""
    approval = _pending_approval(db, quote)

    try:
        quote.status = transition(quote.status, QuoteStatus.APPROVED)
    except IllegalTransition as exc:
        raise ApprovalConflict(str(exc)) from exc

    approval.status = ApprovalStatus.RESOLVED
    approval.action = ApprovalAction.APPROVE
    approval.actor = actor
    approval.decision_notes = notes
    approval.decided_at = datetime.now(UTC)
    db.flush()

    # send_quote re-checks the gate itself rather than trusting this caller.
    sent = send_quote(db, approval_id=approval.id, adapter=adapter)
    return approval, sent


def reject(
    db: Session, *, quote: Quote, actor: str, notes: str | None = None
) -> Approval:
    approval = _pending_approval(db, quote)

    try:
        quote.status = transition(quote.status, QuoteStatus.REJECTED)
    except IllegalTransition as exc:
        raise ApprovalConflict(str(exc)) from exc

    approval.status = ApprovalStatus.RESOLVED
    approval.action = ApprovalAction.REJECT
    approval.actor = actor
    approval.decision_notes = notes
    approval.decided_at = datetime.now(UTC)
    db.flush()

    logger.info("quote %s rejected by %s", quote.quote_number, actor)
    return approval


def send_without_approval_is_impossible(db: Session, *, quote_id: uuid.UUID) -> None:
    """Documentation-as-code: there is no function that sends without approval.

    `send_quote` requires an approval id and re-validates it. This helper exists
    so a reader searching for "how do I send" finds this explanation rather than
    concluding the capability is missing by accident.
    """
    raise ApprovalRequired(
        "There is no unapproved send path. Call approvals.approve(), which "
        "resolves the Approval and then calls send_quote()."
    )
