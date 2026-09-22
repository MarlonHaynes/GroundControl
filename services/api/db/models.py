"""SQLAlchemy models.

Money is stored as integer cents everywhere. Quantities and unit rates are
Numeric, never Float. Enum columns are native Postgres enums so a bad value is
a database error rather than a silent string.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base, TimestampMixin, UUIDMixin


def _enum(py_enum: type[StrEnum], name: str) -> SAEnum:
    return SAEnum(py_enum, name=name, native_enum=True, values_callable=lambda e: [m.value for m in e])


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class CustomerStatus(StrEnum):
    EXISTING = "existing"
    NEW = "new"


class JobRequestSource(StrEnum):
    EMAIL = "email"
    VOICEMAIL_TRANSCRIPT = "voicemail_transcript"


class JobRequestStatus(StrEnum):
    RECEIVED = "received"
    PROCESSING = "processing"
    QUOTED = "quoted"  # a quote exists and is waiting on a human
    ROUTED_TO_HUMAN = "routed_to_human"  # guardrail fired; no quote produced
    FAILED = "failed"


class QuoteStatus(StrEnum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    SENT = "sent"


class LineItemSource(StrEnum):
    LLM_PROPOSED = "llm_proposed"
    HUMAN_EDITED = "human_edited"


class ApprovalAction(StrEnum):
    APPROVE = "approve"
    EDIT = "edit"
    REJECT = "reject"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    RESOLVED = "resolved"


class RunStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    ROUTED_TO_HUMAN = "routed_to_human"
    FAILED = "failed"


class StepStatus(StrEnum):
    OK = "ok"
    ERROR = "error"


# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------


class Customer(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "customers"

    name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    contact_name: Mapped[str | None] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(320), index=True)
    phone: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[CustomerStatus] = mapped_column(
        _enum(CustomerStatus, "customer_status"), default=CustomerStatus.EXISTING, nullable=False
    )
    notes: Mapped[str | None] = mapped_column(Text)
    created_from_job_request_id: Mapped[uuid.UUID | None] = mapped_column()

    addresses: Mapped[list[Address]] = relationship(
        back_populates="customer", cascade="all, delete-orphan", lazy="selectin"
    )
    job_requests: Mapped[list[JobRequest]] = relationship(back_populates="customer")


class Address(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "addresses"

    customer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    line1: Mapped[str] = mapped_column(String(250), nullable=False)
    city: Mapped[str | None] = mapped_column(String(120))
    state: Mapped[str | None] = mapped_column(String(40))
    postal_code: Mapped[str | None] = mapped_column(String(20))
    travel_zone: Mapped[str] = mapped_column(String(20), default="zone_1", nullable=False)
    property_notes: Mapped[str | None] = mapped_column(Text)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    customer: Mapped[Customer] = relationship(back_populates="addresses")


# ---------------------------------------------------------------------------
# Catalog & pricing rules
# ---------------------------------------------------------------------------


class ServiceCatalogItem(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "service_catalog_items"

    code: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    service_type: Mapped[str] = mapped_column(String(50), nullable=False)
    unit: Mapped[str] = mapped_column(String(20), nullable=False)
    # Numeric, not Integer: sub-cent unit rates are real (mowing is 1.2c/sqft).
    base_rate_cents: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    min_charge_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    labor_sensitive: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    keywords: Mapped[list[str]] = mapped_column(ARRAY(String), default=list, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    __table_args__ = (
        CheckConstraint("base_rate_cents > 0", name="base_rate_positive"),
        CheckConstraint("min_charge_cents > 0", name="min_charge_positive"),
    )


class PricingRule(UUIDMixin, TimestampMixin, Base):
    """The rule book, mirrored into the database for inspection and for the UI.

    The engine reads its constants from `pricing.catalog_data`, not from here:
    pricing must not change because someone edited a row. These rows exist so a
    reviewer (and the catalog screen) can see the rules that were in force.
    """

    __tablename__ = "pricing_rules"

    code: Mapped[str] = mapped_column(String(60), nullable=False, unique=True)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    applies_to: Mapped[str] = mapped_column(String(100), default="global", nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    apply_order: Mapped[int] = mapped_column(Integer, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


# ---------------------------------------------------------------------------
# Job requests
# ---------------------------------------------------------------------------


class JobRequest(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "job_requests"

    raw_source_text: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[JobRequestSource] = mapped_column(
        _enum(JobRequestSource, "job_request_source"),
        default=JobRequestSource.EMAIL,
        nullable=False,
    )
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sender_email: Mapped[str | None] = mapped_column(String(320))
    subject: Mapped[str | None] = mapped_column(String(500))

    # Structured extraction plus its per-field confidence map.
    parsed: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    field_confidence: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    needs_review: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    review_reasons: Mapped[list[str]] = mapped_column(ARRAY(String), default=list, nullable=False)

    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("customers.id", ondelete="SET NULL"), index=True
    )
    is_new_customer: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    customer_match_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))

    status: Mapped[JobRequestStatus] = mapped_column(
        _enum(JobRequestStatus, "job_request_status"),
        default=JobRequestStatus.RECEIVED,
        nullable=False,
        index=True,
    )
    routed_reason: Mapped[str | None] = mapped_column(Text)

    # Links a seeded request back to its labeled fixture, for the eval harness.
    fixture_case_id: Mapped[str | None] = mapped_column(String(80), index=True)

    customer: Mapped[Customer | None] = relationship(back_populates="job_requests")
    quotes: Mapped[list[Quote]] = relationship(back_populates="job_request", lazy="selectin")
    runs: Mapped[list[AgentRun]] = relationship(back_populates="job_request")


# ---------------------------------------------------------------------------
# Quotes
# ---------------------------------------------------------------------------


class Quote(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "quotes"

    job_request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("job_requests.id", ondelete="CASCADE"), nullable=False, index=True
    )
    quote_number: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    status: Mapped[QuoteStatus] = mapped_column(
        _enum(QuoteStatus, "quote_status"), default=QuoteStatus.DRAFT, nullable=False, index=True
    )

    line_subtotal_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    adjustments: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False)
    adjusted_subtotal_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tax_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="USD", nullable=False)

    pricing_context: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    priced_by_engine_version: Mapped[str] = mapped_column(String(20), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)

    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    job_request: Mapped[JobRequest] = relationship(back_populates="quotes")
    line_items: Mapped[list[LineItem]] = relationship(
        back_populates="quote",
        cascade="all, delete-orphan",
        order_by="LineItem.position",
        lazy="selectin",
    )
    draft_messages: Mapped[list[DraftMessage]] = relationship(
        back_populates="quote", cascade="all, delete-orphan", lazy="selectin"
    )
    approvals: Mapped[list[Approval]] = relationship(
        back_populates="quote", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (
        CheckConstraint("total_cents >= 0", name="total_non_negative"),
        CheckConstraint(
            "adjusted_subtotal_cents + tax_cents = total_cents", name="total_reconciles"
        ),
        Index("ix_quotes_status_created", "status", "created_at"),
    )


class LineItem(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "line_items"

    quote_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("quotes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    catalog_item_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("service_catalog_items.id", ondelete="SET NULL")
    )
    catalog_code: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str] = mapped_column(String(300), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    unit: Mapped[str] = mapped_column(String(20), nullable=False)
    unit_price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    base_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    subtotal_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    applied_rules: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False)
    trunk_diameter_band: Mapped[str | None] = mapped_column(String(20))
    rationale: Mapped[str] = mapped_column(Text, default="", nullable=False)
    source: Mapped[LineItemSource] = mapped_column(
        _enum(LineItemSource, "line_item_source"),
        default=LineItemSource.LLM_PROPOSED,
        nullable=False,
    )

    quote: Mapped[Quote] = relationship(back_populates="line_items")

    __table_args__ = (
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("subtotal_cents >= 0", name="subtotal_non_negative"),
    )


class DraftMessage(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "draft_messages"

    quote_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("quotes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    channel: Mapped[str] = mapped_column(String(20), default="email", nullable=False)
    to_email: Mapped[str | None] = mapped_column(String(320))
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    # Populated when a human edits before approving; the edit is what gets sent.
    edited_subject: Mapped[str | None] = mapped_column(String(500))
    edited_body: Mapped[str | None] = mapped_column(Text)
    generated_by_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL")
    )

    quote: Mapped[Quote] = relationship(back_populates="draft_messages")

    @property
    def final_subject(self) -> str:
        return self.edited_subject or self.subject

    @property
    def final_body(self) -> str:
        return self.edited_body or self.body


class Approval(UUIDMixin, TimestampMixin, Base):
    """The human decision record.

    Nothing customer-facing happens without a row here in state
    (action=approve, status=resolved). That rule is enforced in the service
    layer, in `send_quote`, and pinned by tests.
    """

    __tablename__ = "approvals"

    quote_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("quotes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[ApprovalStatus] = mapped_column(
        _enum(ApprovalStatus, "approval_status"),
        default=ApprovalStatus.PENDING,
        nullable=False,
        index=True,
    )
    action: Mapped[ApprovalAction | None] = mapped_column(_enum(ApprovalAction, "approval_action"))
    actor: Mapped[str | None] = mapped_column(String(200))
    decision_notes: Mapped[str | None] = mapped_column(Text)
    edited_fields: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    quote: Mapped[Quote] = relationship(back_populates="approvals")

    __table_args__ = (
        CheckConstraint(
            "(status = 'pending' AND action IS NULL AND decided_at IS NULL) "
            "OR (status = 'resolved' AND action IS NOT NULL AND decided_at IS NOT NULL)",
            name="resolved_requires_action_and_time",
        ),
    )


class SentMessage(UUIDMixin, TimestampMixin, Base):
    """What the messaging adapter actually delivered. Written only by send_quote."""

    __tablename__ = "sent_messages"

    approval_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("approvals.id", ondelete="CASCADE"), nullable=False, index=True
    )
    quote_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("quotes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    adapter: Mapped[str] = mapped_column(String(40), default="mock", nullable=False)
    to_email: Mapped[str] = mapped_column(String(320), nullable=False)
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    provider_message_id: Mapped[str | None] = mapped_column(String(120))
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# ---------------------------------------------------------------------------
# Observability
# ---------------------------------------------------------------------------


class AgentRun(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "agent_runs"

    job_request_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("job_requests.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[RunStatus] = mapped_column(
        _enum(RunStatus, "run_status"), default=RunStatus.RUNNING, nullable=False, index=True
    )
    model: Mapped[str] = mapped_column(String(80), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    latency_ms: Mapped[int | None] = mapped_column(Integer)

    total_tokens_in: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_tokens_out: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_cached_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Micro-cents: a single Haiku call can cost a fraction of a cent, and
    # rounding each step to a whole cent would make per-run cost meaningless.
    total_cost_microcents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    outcome: Mapped[str | None] = mapped_column(String(60))
    error: Mapped[str | None] = mapped_column(Text)

    job_request: Mapped[JobRequest | None] = relationship(back_populates="runs")
    steps: Mapped[list[TraceStep]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="TraceStep.seq",
        lazy="selectin",
    )


class TraceStep(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "trace_steps"

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    tool_name: Mapped[str] = mapped_column(String(80), nullable=False)
    uses_llm: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[StepStatus] = mapped_column(
        _enum(StepStatus, "step_status"), default=StepStatus.OK, nullable=False
    )

    input: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    output: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    tokens_in: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cached_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_microcents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)

    run: Mapped[AgentRun] = relationship(back_populates="steps")

    __table_args__ = (UniqueConstraint("run_id", "seq", name="uq_trace_steps_run_id_seq"),)


# ---------------------------------------------------------------------------
# Evals
# ---------------------------------------------------------------------------


class EvalRun(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "eval_runs"

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    model: Mapped[str] = mapped_column(String(80), nullable=False)
    n_cases: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    subset: Mapped[str | None] = mapped_column(String(80))
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    thresholds: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    total_cost_microcents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)

    results: Mapped[list[EvalResult]] = relationship(
        back_populates="eval_run", cascade="all, delete-orphan"
    )


class EvalResult(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "eval_results"

    eval_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("eval_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    case_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String), default=list, nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    scores: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    diffs: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    cost_microcents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    eval_run: Mapped[EvalRun] = relationship(back_populates="results")
