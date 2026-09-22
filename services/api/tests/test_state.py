"""Quote state machine and database-level invariants.

These tests exist because the approval gate is only as strong as the weakest
path to `status = sent`. Here we pin both halves: the state machine refuses the
illegal moves, and the database refuses rows that would let a caller sidestep it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from db.models import (
    Approval,
    ApprovalAction,
    ApprovalStatus,
    JobRequest,
    Quote,
    QuoteStatus,
)
from db.state import ALLOWED_TRANSITIONS, IllegalTransition, can_transition, transition
from tests.conftest import requires_db

ALL_STATUSES = list(QuoteStatus)


class TestStateMachine:
    def test_happy_path_to_sent(self) -> None:
        s = QuoteStatus.DRAFT
        for target in (QuoteStatus.PENDING_APPROVAL, QuoteStatus.APPROVED, QuoteStatus.SENT):
            s = transition(s, target)
        assert s is QuoteStatus.SENT

    def test_rejection_path(self) -> None:
        s = transition(QuoteStatus.DRAFT, QuoteStatus.PENDING_APPROVAL)
        assert transition(s, QuoteStatus.REJECTED) is QuoteStatus.REJECTED

    def test_draft_cannot_jump_straight_to_sent(self) -> None:
        """The single most important assertion in this file."""
        with pytest.raises(IllegalTransition):
            transition(QuoteStatus.DRAFT, QuoteStatus.SENT)

    def test_pending_cannot_jump_to_sent(self) -> None:
        with pytest.raises(IllegalTransition):
            transition(QuoteStatus.PENDING_APPROVAL, QuoteStatus.SENT)

    def test_only_approved_may_reach_sent(self) -> None:
        senders = [s for s in ALL_STATUSES if can_transition(s, QuoteStatus.SENT)]
        assert senders == [QuoteStatus.APPROVED]

    @pytest.mark.parametrize("terminal", [QuoteStatus.SENT, QuoteStatus.REJECTED])
    def test_terminal_states_have_no_exits(self, terminal: QuoteStatus) -> None:
        assert ALLOWED_TRANSITIONS[terminal] == frozenset()
        for target in ALL_STATUSES:
            assert not can_transition(terminal, target)

    def test_rejected_cannot_be_revived(self) -> None:
        with pytest.raises(IllegalTransition):
            transition(QuoteStatus.REJECTED, QuoteStatus.APPROVED)

    def test_no_status_may_transition_to_itself(self) -> None:
        for s in ALL_STATUSES:
            assert not can_transition(s, s)

    def test_every_status_is_covered(self) -> None:
        assert set(ALLOWED_TRANSITIONS) == set(ALL_STATUSES)

    def test_error_message_names_the_legal_moves(self) -> None:
        with pytest.raises(IllegalTransition, match="pending_approval"):
            transition(QuoteStatus.DRAFT, QuoteStatus.SENT)


# ---------------------------------------------------------------------------
# Database-level invariants
# ---------------------------------------------------------------------------


def _job_request(db) -> JobRequest:
    jr = JobRequest(
        raw_source_text="Can you mow the back field?",
        received_at=datetime.now(UTC),
    )
    db.add(jr)
    db.flush()
    return jr


def _quote(db, jr: JobRequest, **kw) -> Quote:
    defaults = dict(
        job_request_id=jr.id,
        quote_number=f"Q-{uuid.uuid4().hex[:8].upper()}",
        line_subtotal_cents=24_000,
        adjusted_subtotal_cents=24_000,
        tax_cents=1_524,
        total_cents=25_524,
        priced_by_engine_version="1.0.0",
    )
    defaults.update(kw)
    q = Quote(**defaults)
    db.add(q)
    db.flush()
    return q


@requires_db
class TestDatabaseInvariants:
    def test_quote_totals_must_reconcile(self, db) -> None:
        """A quote whose parts do not add up cannot be stored at all."""
        jr = _job_request(db)
        with pytest.raises(IntegrityError, match="total_reconciles"):
            _quote(db, jr, adjusted_subtotal_cents=24_000, tax_cents=1_524, total_cents=99_999)

    def test_valid_quote_persists(self, db) -> None:
        jr = _job_request(db)
        q = _quote(db, jr)
        assert q.adjusted_subtotal_cents + q.tax_cents == q.total_cents

    def test_negative_total_is_rejected(self, db) -> None:
        jr = _job_request(db)
        with pytest.raises(IntegrityError):
            _quote(db, jr, adjusted_subtotal_cents=-2_000, tax_cents=0, total_cents=-2_000)

    def test_pending_approval_may_not_carry_a_decision(self, db) -> None:
        """A pending row with an action set would look approved to a careless query."""
        jr = _job_request(db)
        q = _quote(db, jr, status=QuoteStatus.PENDING_APPROVAL)
        db.add(
            Approval(
                quote_id=q.id,
                status=ApprovalStatus.PENDING,
                action=ApprovalAction.APPROVE,  # illegal while pending
            )
        )
        with pytest.raises(IntegrityError, match="resolved_requires_action_and_time"):
            db.flush()

    def test_resolved_approval_requires_action_and_timestamp(self, db) -> None:
        jr = _job_request(db)
        q = _quote(db, jr, status=QuoteStatus.PENDING_APPROVAL)
        db.add(Approval(quote_id=q.id, status=ApprovalStatus.RESOLVED, action=None))
        with pytest.raises(IntegrityError, match="resolved_requires_action_and_time"):
            db.flush()

    def test_well_formed_approval_persists(self, db) -> None:
        jr = _job_request(db)
        q = _quote(db, jr, status=QuoteStatus.PENDING_APPROVAL)
        a = Approval(
            quote_id=q.id,
            status=ApprovalStatus.RESOLVED,
            action=ApprovalAction.APPROVE,
            actor="office@riversidegrounds.com",
            decided_at=datetime.now(UTC),
        )
        db.add(a)
        db.flush()
        assert a.id is not None

    def test_quote_number_is_unique(self, db) -> None:
        jr = _job_request(db)
        _quote(db, jr, quote_number="Q-DUPLICATE")
        with pytest.raises(IntegrityError):
            _quote(db, jr, quote_number="Q-DUPLICATE")
