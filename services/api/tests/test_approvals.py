"""Approval, edit, reject, and the send gate end to end.

The golden path's final leg: a human approves and only then does the mock
adapter fire. Everything that is not that path is asserted to fail.
"""

from __future__ import annotations

import pytest

from agent.guardrails import ApprovalRequired
from agent.loop import run_pipeline
from agent.tools.send import send_quote
from app.services.approvals import (
    ApprovalConflict,
    LineItemEdit,
    approve,
    edit_quote,
    reject,
)
from db.models import (
    ApprovalAction,
    ApprovalStatus,
    LineItemSource,
    QuoteStatus,
    SentMessage,
)
from integrations.mock.messaging import MockMessagingAdapter
from tests.conftest import requires_db
from tests.fakes import golden_client, make_customer, make_job_request

pytestmark = requires_db

ACTOR = "office@riversidegrounds.com"


@pytest.fixture
def adapter() -> MockMessagingAdapter:
    return MockMessagingAdapter()


@pytest.fixture
def pending(db):
    """A quote sitting in the approval queue, produced by a real pipeline run."""
    from tests.test_pipeline import _seed_catalog

    _seed_catalog(db)
    make_customer(db)
    jr = make_job_request(db)
    result = run_pipeline(db, job_request=jr, client=golden_client())
    assert result.quote.status is QuoteStatus.PENDING_APPROVAL
    return result


class TestApproveSends:
    def test_approval_sends_and_marks_sent(self, db, pending, adapter) -> None:
        approval, sent = approve(db, quote=pending.quote, actor=ACTOR, adapter=adapter)

        assert approval.status is ApprovalStatus.RESOLVED
        assert approval.action is ApprovalAction.APPROVE
        assert approval.actor == ACTOR
        assert approval.decided_at is not None
        assert pending.quote.status is QuoteStatus.SENT
        assert pending.quote.sent_at is not None
        assert sent.to_email == "amara.osei@gmail.com"

    def test_the_adapter_actually_received_it(self, db, pending, adapter) -> None:
        approve(db, quote=pending.quote, actor=ACTOR, adapter=adapter)

        assert len(adapter.outbox) == 1
        assert adapter.outbox[0]["to"] == "amara.osei@gmail.com"
        assert pending.quote.quote_number in adapter.outbox[0]["body"]

    def test_sent_message_row_is_written(self, db, pending, adapter) -> None:
        approve(db, quote=pending.quote, actor=ACTOR, adapter=adapter)

        rows = db.query(SentMessage).filter(SentMessage.quote_id == pending.quote.id).all()
        assert len(rows) == 1
        assert rows[0].adapter == "mock"
        assert rows[0].provider_message_id.startswith("mock-")

    def test_double_send_is_refused(self, db, pending, adapter) -> None:
        """Two independent guards cover this; the status gate happens to fire first.

        `require_approval` rejects it because the quote is already `sent` rather
        than `approved`, before the already-sent lookup is reached. Asserting on
        refusal rather than on which layer refuses keeps the test honest about
        what is guaranteed.
        """
        approval, _ = approve(db, quote=pending.quote, actor=ACTOR, adapter=adapter)

        with pytest.raises(ApprovalRequired):
            send_quote(db, approval_id=approval.id, adapter=adapter)
        assert len(adapter.outbox) == 1
        assert db.query(SentMessage).count() == 1


class TestSendRequiresApproval:
    def test_send_on_a_pending_quote_refuses(self, db, pending, adapter) -> None:
        """The headline guardrail: a queued quote cannot be sent."""
        approval = pending.approval
        with pytest.raises(ApprovalRequired, match="still pending"):
            send_quote(db, approval_id=approval.id, adapter=adapter)

        assert adapter.outbox == []
        assert db.query(SentMessage).count() == 0
        assert pending.quote.status is QuoteStatus.PENDING_APPROVAL

    def test_send_with_an_unknown_approval_id_refuses(self, db, adapter) -> None:
        import uuid

        with pytest.raises(ApprovalRequired, match="No approval"):
            send_quote(db, approval_id=uuid.uuid4(), adapter=adapter)

    def test_rejected_quote_cannot_be_sent(self, db, pending, adapter) -> None:
        approval = reject(db, quote=pending.quote, actor=ACTOR, notes="Customer withdrew.")

        with pytest.raises(ApprovalRequired):
            send_quote(db, approval_id=approval.id, adapter=adapter)
        assert adapter.outbox == []


class TestReject:
    def test_reject_resolves_without_sending(self, db, pending, adapter) -> None:
        approval = reject(db, quote=pending.quote, actor=ACTOR, notes="Out of area.")

        assert approval.action is ApprovalAction.REJECT
        assert approval.decision_notes == "Out of area."
        assert pending.quote.status is QuoteStatus.REJECTED
        assert adapter.outbox == []

    def test_rejected_quote_cannot_then_be_approved(self, db, pending, adapter) -> None:
        reject(db, quote=pending.quote, actor=ACTOR)
        with pytest.raises(ApprovalConflict):
            approve(db, quote=pending.quote, actor=ACTOR, adapter=adapter)


class TestEditBeforeSend:
    def test_edit_reprices_through_the_engine(self, db, pending) -> None:
        """Correcting 20,000 sq ft to 12,000 must recompute tax and total.

        MOW_STD 12,000 x 1.2c = 144.00, EDGE_TRIM 2h = 116.00 -> 260.00
        tax 260.00 x 6.35% = 16.51 -> total 276.51
        """
        edit_quote(
            db,
            quote=pending.quote,
            line_items=[
                LineItemEdit(catalog_code="MOW_STD", quantity="12000"),
                LineItemEdit(catalog_code="EDGE_TRIM", quantity="2"),
            ],
            actor=ACTOR,
            notes="Measured it; the lawn is 12,000 not 20,000.",
        )

        assert pending.quote.line_subtotal_cents == 26_000
        assert pending.quote.tax_cents == 1_651
        assert pending.quote.total_cents == 27_651

    def test_edit_marks_line_items_as_human_edited(self, db, pending) -> None:
        edit_quote(
            db,
            quote=pending.quote,
            line_items=[LineItemEdit(catalog_code="MOW_STD", quantity="12000")],
            actor=ACTOR,
        )
        assert all(li.source is LineItemSource.HUMAN_EDITED for li in pending.quote.line_items)

    def test_edit_does_not_approve(self, db, pending, adapter) -> None:
        """An edit is a correction, not a decision."""
        edit_quote(
            db,
            quote=pending.quote,
            line_items=[LineItemEdit(catalog_code="MOW_STD", quantity="12000")],
            actor=ACTOR,
        )

        assert pending.quote.status is QuoteStatus.PENDING_APPROVAL
        assert pending.approval.status is ApprovalStatus.PENDING
        assert adapter.outbox == []
        assert db.query(SentMessage).count() == 0

    def test_edit_records_an_audit_trail(self, db, pending) -> None:
        edit_quote(
            db,
            quote=pending.quote,
            line_items=[LineItemEdit(catalog_code="MOW_STD", quantity="12000")],
            actor=ACTOR,
            notes="Re-measured.",
        )

        history = pending.approval.edited_fields["history"]
        assert len(history) == 1
        assert history[0]["actor"] == ACTOR
        assert history[0]["notes"] == "Re-measured."
        assert history[0]["before"]["total_cents"] == 37_861
        assert history[0]["after"]["total_cents"] == 15_314

    def test_edit_then_approve_sends_the_edited_total(self, db, pending, adapter) -> None:
        edit_quote(
            db,
            quote=pending.quote,
            line_items=[LineItemEdit(catalog_code="MOW_STD", quantity="12000")],
            actor=ACTOR,
        )
        approve(db, quote=pending.quote, actor=ACTOR, adapter=adapter)

        assert pending.quote.status is QuoteStatus.SENT
        assert pending.quote.total_cents == 15_314  # $144.00 + $9.14 tax = $153.14

    def test_edited_draft_body_is_what_gets_sent(self, db, pending, adapter) -> None:
        edit_quote(
            db,
            quote=pending.quote,
            line_items=[LineItemEdit(catalog_code="MOW_STD", quantity="12000")],
            actor=ACTOR,
            draft_body="Hi Amara, revised quote attached. The team at Riverside Grounds",
        )
        approve(db, quote=pending.quote, actor=ACTOR, adapter=adapter)

        assert adapter.outbox[0]["body"].startswith("Hi Amara, revised quote attached")

    def test_edit_to_an_unpriceable_quote_is_refused(self, db, pending) -> None:
        with pytest.raises(ApprovalConflict, match="could not be priced"):
            edit_quote(
                db,
                quote=pending.quote,
                line_items=[LineItemEdit(catalog_code="TREE_REMOVAL", quantity="1")],
                actor=ACTOR,
            )

    def test_edit_to_empty_is_refused(self, db, pending) -> None:
        with pytest.raises(ApprovalConflict, match="at least one line item"):
            edit_quote(db, quote=pending.quote, line_items=[], actor=ACTOR)

    def test_sent_quote_cannot_be_edited(self, db, pending, adapter) -> None:
        approve(db, quote=pending.quote, actor=ACTOR, adapter=adapter)
        with pytest.raises(ApprovalConflict, match="cannot be edited"):
            edit_quote(
                db,
                quote=pending.quote,
                line_items=[LineItemEdit(catalog_code="MOW_STD", quantity="1000")],
                actor=ACTOR,
            )
