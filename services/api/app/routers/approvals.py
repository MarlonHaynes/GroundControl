"""The approval queue — the only route that can cause a customer-facing send."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from agent.guardrails import ApprovalRequired
from app.api_schemas import (
    ApprovalQueueItem,
    DecisionIn,
    EditQuoteIn,
    QuoteOut,
    SendResultOut,
)
from app.serializers import customer_out, job_request_detail, quote_out
from app.services import approvals as approval_service
from app.services.approvals import ApprovalConflict, LineItemEdit
from db.models import Quote, QuoteStatus
from db.session import get_db

router = APIRouter(prefix="/api", tags=["approvals"])


def _load(db: Session, quote_id: uuid.UUID) -> Quote:
    quote = db.get(Quote, quote_id)
    if quote is None:
        raise HTTPException(404, f"No quote {quote_id}")
    return quote


@router.get("/approvals", response_model=list[ApprovalQueueItem])
def approval_queue(
    db: Session = Depends(get_db), limit: int = Query(default=100, le=500)
) -> list[ApprovalQueueItem]:
    """Everything a reviewer needs, in one request per screen rather than per card."""
    quotes = (
        db.execute(
            select(Quote)
            .where(Quote.status == QuoteStatus.PENDING_APPROVAL)
            .order_by(Quote.created_at.asc())
            .limit(limit)
        )
        .scalars()
        .all()
    )

    items: list[ApprovalQueueItem] = []
    for q in quotes:
        jr = q.job_request
        items.append(
            ApprovalQueueItem(
                quote=quote_out(q),
                job_request=job_request_detail(jr),
                customer=customer_out(jr.customer),
            )
        )
    return items


@router.get("/quotes/{quote_id}", response_model=QuoteOut)
def get_quote(quote_id: uuid.UUID, db: Session = Depends(get_db)) -> QuoteOut:
    return quote_out(_load(db, quote_id))


@router.post("/quotes/{quote_id}/edit", response_model=QuoteOut)
def edit_quote(
    quote_id: uuid.UUID, payload: EditQuoteIn, db: Session = Depends(get_db)
) -> QuoteOut:
    """Correct a quote and re-price it. Does not approve it."""
    quote = _load(db, quote_id)
    try:
        approval_service.edit_quote(
            db,
            quote=quote,
            line_items=[
                LineItemEdit(
                    catalog_code=li.catalog_code,
                    quantity=li.quantity,
                    trunk_diameter_band=li.trunk_diameter_band,
                    rationale=li.rationale,
                )
                for li in payload.line_items
            ],
            actor=payload.actor,
            notes=payload.notes,
            draft_subject=payload.draft_subject,
            draft_body=payload.draft_body,
        )
    except ApprovalConflict as exc:
        raise HTTPException(409, str(exc)) from exc

    db.commit()
    db.refresh(quote)
    return quote_out(quote)


@router.post("/quotes/{quote_id}/approve", response_model=SendResultOut)
def approve_quote(
    quote_id: uuid.UUID, payload: DecisionIn, db: Session = Depends(get_db)
) -> SendResultOut:
    """Approve and send. The only path in the API that reaches the messaging adapter."""
    quote = _load(db, quote_id)
    try:
        _, sent = approval_service.approve(
            db, quote=quote, actor=payload.actor, notes=payload.notes
        )
    except ApprovalConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ApprovalRequired as exc:
        # The gate refused. 403 rather than 409: this is a permission failure,
        # not a state conflict the caller can resolve by retrying.
        raise HTTPException(403, str(exc)) from exc

    db.commit()
    db.refresh(quote)
    return SendResultOut(
        quote_id=quote.id,
        quote_number=quote.quote_number,
        status=quote.status,
        to_email=sent.to_email,
        subject=sent.subject,
        provider_message_id=sent.provider_message_id,
        adapter=sent.adapter,
        sent_at=sent.sent_at,
    )


@router.post("/quotes/{quote_id}/reject", response_model=QuoteOut)
def reject_quote(
    quote_id: uuid.UUID, payload: DecisionIn, db: Session = Depends(get_db)
) -> QuoteOut:
    quote = _load(db, quote_id)
    try:
        approval_service.reject(db, quote=quote, actor=payload.actor, notes=payload.notes)
    except ApprovalConflict as exc:
        raise HTTPException(409, str(exc)) from exc

    db.commit()
    db.refresh(quote)
    return quote_out(quote)


@router.post("/quotes/{quote_id}/send", status_code=403, include_in_schema=True)
def send_without_approval(quote_id: uuid.UUID) -> None:
    """Deliberately unimplemented, and documented as such.

    A direct send endpoint is the obvious thing for a caller to look for, and
    its absence would read as an oversight. It exists, in the schema, and
    always refuses — pointing the caller at the approval flow.
    """
    raise HTTPException(
        403,
        "There is no unapproved send path. POST /api/quotes/{id}/approve resolves "
        "the approval and sends as one action.",
    )
