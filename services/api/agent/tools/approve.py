"""submit_for_approval — the agent's terminal step.

Persists the quote, its line items, and the drafted email, then creates a
pending `Approval` and stops. There is no continuation. The agent has no
ability to resolve the approval it just created, and no tool that sends
anything: `send_quote` lives in a different module and is not registered.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from agent.schemas import DraftedEmail
from db.models import (
    Approval,
    ApprovalStatus,
    DraftMessage,
    JobRequest,
    JobRequestStatus,
    LineItem,
    LineItemSource,
    Quote,
    QuoteStatus,
    ServiceCatalogItem,
)
from db.state import transition
from pricing.types import ComputedQuote


def next_quote_number() -> str:
    """Human-readable, sortable, and unique enough for a company this size.

    Allocated before drafting rather than at insert time, so the drafted email
    can cite the number and the draft verifier can check that it did. The
    uniqueness constraint on `quotes.quote_number` is the real guarantee; a
    collision here would be a failed insert, not a wrong quote.
    """
    stamp = datetime.now(UTC).strftime("%Y%m")
    suffix = uuid.uuid4().hex[:5].upper()
    return f"RG-{stamp}-{suffix}"


def submit_for_approval(
    db: Session,
    *,
    job_request: JobRequest,
    computed: ComputedQuote,
    drafted: DraftedEmail,
    to_email: str | None,
    run_id: uuid.UUID | None = None,
    quote_number: str | None = None,
) -> tuple[Quote, Approval]:
    number = quote_number or next_quote_number(db)

    quote = Quote(
        job_request_id=job_request.id,
        quote_number=number,
        status=QuoteStatus.DRAFT,
        line_subtotal_cents=computed.line_subtotal_cents,
        adjustments=[a.model_dump(mode="json") for a in computed.adjustments],
        adjusted_subtotal_cents=computed.adjusted_subtotal_cents,
        tax_cents=computed.tax_cents,
        total_cents=computed.total_cents,
        pricing_context=computed.context.model_dump(mode="json"),
        priced_by_engine_version=computed.engine_version,
    )
    db.add(quote)
    db.flush()

    catalog_ids = {
        row.code: row.id for row in db.query(ServiceCatalogItem).all()
    }

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
                source=LineItemSource.LLM_PROPOSED,
            )
        )

    db.add(
        DraftMessage(
            quote_id=quote.id,
            channel="email",
            to_email=to_email,
            subject=drafted.subject,
            body=drafted.body,
            generated_by_run_id=run_id,
        )
    )

    # Move to pending and open the approval. Order matters only for clarity;
    # both happen in the same transaction.
    quote.status = transition(quote.status, QuoteStatus.PENDING_APPROVAL)
    approval = Approval(quote_id=quote.id, status=ApprovalStatus.PENDING)
    db.add(approval)

    job_request.status = JobRequestStatus.QUOTED
    db.flush()

    return quote, approval
