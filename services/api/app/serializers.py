"""ORM -> API model conversion.

Kept in one module so the wire shape is defined in a single place and the
routers stay thin.
"""

from __future__ import annotations

from typing import Any

from app.api_schemas import (
    AddressOut,
    AgentRunDetail,
    AgentRunSummary,
    AppliedRuleOut,
    ApprovalOut,
    CustomerOut,
    DraftMessageOut,
    JobRequestDetail,
    JobRequestSummary,
    LineItemOut,
    Money,
    QuoteOut,
    TraceStepOut,
)
from db.models import (
    AgentRun,
    Approval,
    Customer,
    DraftMessage,
    JobRequest,
    LineItem,
    Quote,
    TraceStep,
)
from observability.cost import fmt_microcents


def _rules(raw: list[dict[str, Any]] | None) -> list[AppliedRuleOut]:
    return [
        AppliedRuleOut(
            code=r.get("code", ""),
            description=r.get("description", ""),
            delta=Money.of(int(r.get("delta_cents", 0))),
        )
        for r in (raw or [])
    ]


def address_out(a) -> AddressOut:
    return AddressOut(
        id=a.id,
        line1=a.line1,
        city=a.city,
        state=a.state,
        postal_code=a.postal_code,
        travel_zone=a.travel_zone,
        property_notes=a.property_notes,
        is_primary=a.is_primary,
    )


def customer_out(c: Customer | None) -> CustomerOut | None:
    if c is None:
        return None
    return CustomerOut(
        id=c.id,
        name=c.name,
        contact_name=c.contact_name,
        email=c.email,
        phone=c.phone,
        status=c.status.value,
        notes=c.notes,
        addresses=[address_out(a) for a in c.addresses],
    )


def line_item_out(li: LineItem) -> LineItemOut:
    return LineItemOut(
        id=li.id,
        position=li.position,
        catalog_code=li.catalog_code,
        description=li.description,
        quantity=li.quantity,
        unit=li.unit,
        unit_price=Money.of(li.unit_price_cents),
        base=Money.of(li.base_cents),
        subtotal=Money.of(li.subtotal_cents),
        applied_rules=_rules(li.applied_rules),
        rationale=li.rationale,
        source=li.source,
    )


def draft_out(d: DraftMessage | None) -> DraftMessageOut | None:
    if d is None:
        return None
    return DraftMessageOut(
        id=d.id,
        channel=d.channel,
        to_email=d.to_email,
        subject=d.subject,
        body=d.body,
        edited_subject=d.edited_subject,
        edited_body=d.edited_body,
        was_edited=bool(d.edited_body or d.edited_subject),
    )


def approval_out(a: Approval | None) -> ApprovalOut | None:
    if a is None:
        return None
    return ApprovalOut(
        id=a.id,
        status=a.status,
        action=a.action,
        actor=a.actor,
        decision_notes=a.decision_notes,
        decided_at=a.decided_at,
        edit_history=list((a.edited_fields or {}).get("history", [])),
    )


def _latest_approval(q: Quote) -> Approval | None:
    if not q.approvals:
        return None
    return sorted(q.approvals, key=lambda a: a.created_at)[-1]


def quote_out(q: Quote) -> QuoteOut:
    return QuoteOut(
        id=q.id,
        job_request_id=q.job_request_id,
        quote_number=q.quote_number,
        status=q.status,
        line_subtotal=Money.of(q.line_subtotal_cents),
        adjustments=_rules(q.adjustments),
        adjusted_subtotal=Money.of(q.adjusted_subtotal_cents),
        tax=Money.of(q.tax_cents),
        total=Money.of(q.total_cents),
        currency=q.currency,
        pricing_context=q.pricing_context or {},
        priced_by_engine_version=q.priced_by_engine_version,
        sent_at=q.sent_at,
        created_at=q.created_at,
        line_items=[line_item_out(li) for li in q.line_items],
        draft=draft_out(q.draft_messages[0] if q.draft_messages else None),
        approval=approval_out(_latest_approval(q)),
    )


def _primary_quote(jr: JobRequest) -> Quote | None:
    if not jr.quotes:
        return None
    return sorted(jr.quotes, key=lambda q: q.created_at)[-1]


def _summary_fields(jr: JobRequest, latest_run_id=None) -> dict[str, Any]:
    q = _primary_quote(jr)
    return {
        "id": jr.id,
        "subject": jr.subject,
        "sender_email": jr.sender_email,
        "received_at": jr.received_at,
        "status": jr.status,
        "needs_review": jr.needs_review,
        "review_reasons": list(jr.review_reasons or []),
        "is_new_customer": jr.is_new_customer,
        "customer_id": jr.customer_id,
        "customer_name": jr.customer.name if jr.customer else None,
        "customer_match_score": (
            float(jr.customer_match_score) if jr.customer_match_score is not None else None
        ),
        "routed_reason": jr.routed_reason,
        "quote_id": q.id if q else None,
        "quote_number": q.quote_number if q else None,
        "quote_status": q.status if q else None,
        "quote_total": Money.of(q.total_cents) if q else None,
        "latest_run_id": latest_run_id,
        "created_at": jr.created_at,
    }


def job_request_summary(jr: JobRequest, latest_run_id=None) -> JobRequestSummary:
    return JobRequestSummary(**_summary_fields(jr, latest_run_id))


def job_request_detail(jr: JobRequest, latest_run_id=None) -> JobRequestDetail:
    return JobRequestDetail(
        **_summary_fields(jr, latest_run_id),
        raw_source_text=jr.raw_source_text,
        parsed=jr.parsed,
        field_confidence=jr.field_confidence,
    )


def trace_step_out(s: TraceStep) -> TraceStepOut:
    return TraceStepOut(
        id=s.id,
        seq=s.seq,
        tool_name=s.tool_name,
        uses_llm=s.uses_llm,
        status=s.status,
        input=s.input or {},
        output=s.output or {},
        tokens_in=s.tokens_in,
        tokens_out=s.tokens_out,
        cost_microcents=s.cost_microcents,
        cost_display=fmt_microcents(s.cost_microcents),
        latency_ms=s.latency_ms,
        cache_hit=s.cache_hit,
        error=s.error,
    )


def _run_fields(r: AgentRun) -> dict[str, Any]:
    return {
        "id": r.id,
        "job_request_id": r.job_request_id,
        "job_request_subject": r.job_request.subject if r.job_request else None,
        "status": r.status,
        "model": r.model,
        "started_at": r.started_at,
        "ended_at": r.ended_at,
        "latency_ms": r.latency_ms,
        "total_tokens_in": r.total_tokens_in,
        "total_tokens_out": r.total_tokens_out,
        "total_cost_microcents": r.total_cost_microcents,
        "cost_display": fmt_microcents(r.total_cost_microcents),
        "outcome": r.outcome,
        "error": r.error,
        "n_steps": len(r.steps),
    }


def agent_run_summary(r: AgentRun) -> AgentRunSummary:
    return AgentRunSummary(**_run_fields(r))


def agent_run_detail(r: AgentRun) -> AgentRunDetail:
    return AgentRunDetail(
        **_run_fields(r), steps=[trace_step_out(s) for s in sorted(r.steps, key=lambda s: s.seq)]
    )
