"""API request and response models.

Separate from the ORM models and from the agent's internal schemas on purpose:
this is the contract the frontend generates its TypeScript from, and it should
be free to differ from the database's shape. Money crosses the wire as integer
cents plus a preformatted display string, so the frontend never does currency
arithmetic.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from db.models import (
    ApprovalAction,
    ApprovalStatus,
    JobRequestStatus,
    LineItemSource,
    QuoteStatus,
    RunStatus,
    StepStatus,
)
from pricing.money import fmt_money


class Money(BaseModel):
    """Cents plus its rendering. The frontend formats nothing itself."""

    model_config = ConfigDict(extra="forbid")

    cents: int
    display: str

    @classmethod
    def of(cls, cents: int) -> Money:
        return cls(cents=cents, display=fmt_money(cents))


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


class CatalogItemOut(BaseModel):
    code: str
    name: str
    service_type: str
    unit: str
    base_rate_cents: Decimal
    min_charge: Money
    description: str
    labor_sensitive: bool


class PricingRuleOut(BaseModel):
    code: str
    kind: str
    description: str
    applies_to: str
    config: dict[str, Any]
    apply_order: int


class CatalogOut(BaseModel):
    services: list[CatalogItemOut]
    rules: list[PricingRuleOut]


# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------


class AddressOut(BaseModel):
    id: uuid.UUID
    line1: str
    city: str | None
    state: str | None
    postal_code: str | None
    travel_zone: str
    property_notes: str | None
    is_primary: bool


class CustomerOut(BaseModel):
    id: uuid.UUID
    name: str
    contact_name: str | None
    email: str | None
    phone: str | None
    status: str
    notes: str | None
    addresses: list[AddressOut]


# ---------------------------------------------------------------------------
# Job requests
# ---------------------------------------------------------------------------


class CreateJobRequest(BaseModel):
    """Ingest one inbound email."""

    model_config = ConfigDict(extra="forbid")

    raw_source_text: str = Field(min_length=1)
    subject: str | None = None
    sender_email: str | None = None
    received_at: datetime | None = None
    run_now: bool = Field(
        default=True, description="Run the agent pipeline immediately after ingesting"
    )


class JobRequestSummary(BaseModel):
    id: uuid.UUID
    subject: str | None
    sender_email: str | None
    received_at: datetime
    status: JobRequestStatus
    needs_review: bool
    review_reasons: list[str]
    is_new_customer: bool
    customer_id: uuid.UUID | None
    customer_name: str | None
    customer_match_score: float | None
    routed_reason: str | None
    quote_id: uuid.UUID | None
    quote_number: str | None
    quote_status: QuoteStatus | None
    quote_total: Money | None
    latest_run_id: uuid.UUID | None
    created_at: datetime


class JobRequestDetail(JobRequestSummary):
    raw_source_text: str
    parsed: dict[str, Any] | None
    field_confidence: dict[str, Any] | None


# ---------------------------------------------------------------------------
# Quotes
# ---------------------------------------------------------------------------


class AppliedRuleOut(BaseModel):
    code: str
    description: str
    delta: Money


class LineItemOut(BaseModel):
    id: uuid.UUID
    position: int
    catalog_code: str
    description: str
    quantity: Decimal
    unit: str
    unit_price: Money
    base: Money
    subtotal: Money
    applied_rules: list[AppliedRuleOut]
    rationale: str
    source: LineItemSource


class DraftMessageOut(BaseModel):
    id: uuid.UUID
    channel: str
    to_email: str | None
    subject: str
    body: str
    edited_subject: str | None
    edited_body: str | None
    was_edited: bool


class ApprovalOut(BaseModel):
    id: uuid.UUID
    status: ApprovalStatus
    action: ApprovalAction | None
    actor: str | None
    decision_notes: str | None
    decided_at: datetime | None
    edit_history: list[dict[str, Any]]


class QuoteOut(BaseModel):
    id: uuid.UUID
    job_request_id: uuid.UUID
    quote_number: str
    status: QuoteStatus
    line_subtotal: Money
    adjustments: list[AppliedRuleOut]
    adjusted_subtotal: Money
    tax: Money
    total: Money
    currency: str
    pricing_context: dict[str, Any]
    priced_by_engine_version: str
    sent_at: datetime | None
    created_at: datetime
    line_items: list[LineItemOut]
    draft: DraftMessageOut | None
    approval: ApprovalOut | None


class ApprovalQueueItem(BaseModel):
    """One card in the approval queue: everything a reviewer needs in one call."""

    quote: QuoteOut
    job_request: JobRequestDetail
    customer: CustomerOut | None


# ---------------------------------------------------------------------------
# Approval actions
# ---------------------------------------------------------------------------


class LineItemEditIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    catalog_code: str
    quantity: Decimal = Field(gt=0)
    trunk_diameter_band: str | None = None
    rationale: str = ""


class EditQuoteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    line_items: list[LineItemEditIn] = Field(min_length=1)
    actor: str = Field(min_length=1)
    notes: str | None = None
    draft_subject: str | None = None
    draft_body: str | None = None


class DecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: str = Field(min_length=1)
    notes: str | None = None


class SendResultOut(BaseModel):
    quote_id: uuid.UUID
    quote_number: str
    status: QuoteStatus
    to_email: str
    subject: str
    provider_message_id: str | None
    adapter: str
    sent_at: datetime


# ---------------------------------------------------------------------------
# Traces
# ---------------------------------------------------------------------------


class TraceStepOut(BaseModel):
    id: uuid.UUID
    seq: int
    tool_name: str
    uses_llm: bool
    status: StepStatus
    input: dict[str, Any]
    output: dict[str, Any]
    tokens_in: int
    tokens_out: int
    cost_microcents: int
    cost_display: str
    latency_ms: int
    cache_hit: bool
    error: str | None


class AgentRunSummary(BaseModel):
    id: uuid.UUID
    job_request_id: uuid.UUID | None
    job_request_subject: str | None
    status: RunStatus
    model: str
    started_at: datetime
    ended_at: datetime | None
    latency_ms: int | None
    total_tokens_in: int
    total_tokens_out: int
    total_cost_microcents: int
    cost_display: str
    outcome: str | None
    error: str | None
    n_steps: int


class AgentRunDetail(AgentRunSummary):
    steps: list[TraceStepOut]


# ---------------------------------------------------------------------------
# Evals
# ---------------------------------------------------------------------------


class EvalRunOut(BaseModel):
    id: uuid.UUID
    started_at: datetime
    ended_at: datetime | None
    model: str
    n_cases: int
    subset: str | None
    metrics: dict[str, Any]
    thresholds: dict[str, Any]
    passed: bool
    total_cost_microcents: int
    cost_display: str
    notes: str | None


class EvalCaseResultOut(BaseModel):
    case_id: str
    tags: list[str]
    passed: bool
    scores: dict[str, Any]
    diffs: dict[str, Any]
    cost_microcents: int
    latency_ms: int


class EvalRunDetail(EvalRunOut):
    results: list[EvalCaseResultOut]


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


class DashboardStats(BaseModel):
    total_job_requests: int
    pending_approval: int
    needs_review: int
    routed_to_human: int
    sent: int
    rejected: int
    total_spend_microcents: int
    total_spend_display: str
    mean_cost_per_run_microcents: int
    mean_cost_per_run_display: str
