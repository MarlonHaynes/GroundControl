"""send_quote — the only customer-facing action in the system.

This function is deliberately NOT in the tool registry. The agent loop cannot
reach it: `agent/registry.py` never imports this module, and the only caller is
the approval endpoint, after a human has decided. That is the structural half
of the guardrail.

The behavioural half is `require_approval`, re-checked here rather than trusted
from the caller. Two independent mistakes would have to line up for an
unapproved quote to go out: someone would have to both call this directly and
fabricate a resolved approving Approval row that the database's CHECK
constraint accepts.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from agent.guardrails import ApprovalRequired, require_approval
from db.models import Approval, DraftMessage, Quote, QuoteStatus, SentMessage
from db.state import transition
from integrations.base import MessagingAdapter
from integrations.registry import get_messaging_adapter

logger = logging.getLogger(__name__)


def send_quote(
    db: Session,
    *,
    approval_id,
    adapter: MessagingAdapter | None = None,
) -> SentMessage:
    """Send an approved quote. Refuses anything else."""
    approval = db.get(Approval, approval_id)
    if approval is None:
        raise ApprovalRequired(f"No approval {approval_id}. Nothing may be sent.")

    quote = db.get(Quote, approval.quote_id)
    if quote is None:
        raise ApprovalRequired(f"Approval {approval_id} references a missing quote.")

    # The gate. Everything above this line is lookup; nothing below runs unless
    # a human approved this exact quote.
    require_approval(quote, approval)

    already = db.execute(
        select(SentMessage).where(SentMessage.quote_id == quote.id)
    ).scalar_one_or_none()
    if already is not None:
        raise ApprovalRequired(
            f"Quote {quote.quote_number} was already sent at {already.sent_at.isoformat()}. "
            f"Refusing to send a second time."
        )

    draft = db.execute(
        select(DraftMessage).where(DraftMessage.quote_id == quote.id)
    ).scalars().first()
    if draft is None:
        raise ApprovalRequired(f"Quote {quote.quote_number} has no drafted message to send.")

    if not draft.to_email:
        raise ApprovalRequired(
            f"Quote {quote.quote_number} has no recipient address. A human must supply one."
        )

    messaging = adapter or get_messaging_adapter()
    result = messaging.send_email(
        to=draft.to_email,
        subject=draft.final_subject,
        body=draft.final_body,
    )

    sent = SentMessage(
        approval_id=approval.id,
        quote_id=quote.id,
        adapter=result.adapter,
        to_email=draft.to_email,
        subject=draft.final_subject,
        body=draft.final_body,
        provider_message_id=result.provider_message_id,
        sent_at=result.sent_at,
    )
    db.add(sent)

    quote.status = transition(quote.status, QuoteStatus.SENT)
    quote.sent_at = result.sent_at
    db.flush()

    logger.info("sent quote %s via %s (%s)", quote.quote_number, result.adapter, result.provider_message_id)
    return sent
