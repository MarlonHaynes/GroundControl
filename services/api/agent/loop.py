"""The orchestrator.

An explicit, code-driven pipeline rather than a model-driven agent loop. The
model is called three times, each for a narrow schema-constrained sub-task; the
order, the branching, and every write are owned by this function.

That is a deliberate trade and worth stating plainly: we give up the agent's
ability to improvise, and in exchange we get a pipeline whose control flow can
be read in one screen, whose cost per run is bounded by construction, and whose
failure modes are enumerable. For a system that emits prices a business will
honour, that is the right side of the trade. The tradeoff is discussed in
CASE_STUDY.md.

Two terminal outcomes, and only two:

* `submit_for_approval` — a quote exists and waits for a human.
* `route_to_human`      — no quote exists and a human is told why.

There is no third path, and in particular no path that sends anything.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from agent.guardrails import (
    RouteToHuman,
    check_scope,
    evaluate_confidence,
    verify_contact_grounding,
)
from agent.llm import LLMClient
from agent.schemas import JobRequestParsed
from agent.tools.approve import next_quote_number, submit_for_approval
from agent.tools.draft import draft_customer_email
from agent.tools.match import MatchResult, lookup_customer
from agent.tools.parse import parse_job_request
from agent.tools.propose import propose_line_items
from app.config import settings
from db.models import Approval, JobRequest, JobRequestStatus, Quote, RunStatus
from observability.tracer import RunRecord, Tracer
from pricing.engine import compute_quote
from pricing.types import (
    AccessDifficulty,
    ComputedQuote,
    PricingContext,
    PricingError,
    Season,
    TravelZone,
    Urgency,
)

logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    outcome: str  # "submitted_for_approval" | "routed_to_human"
    run: RunRecord
    job_request: JobRequest
    quote: Quote | None = None
    approval: Approval | None = None
    parsed: JobRequestParsed | None = None
    match: MatchResult | None = None
    computed: ComputedQuote | None = None
    route_reason: str | None = None
    route_kind: str | None = None

    @property
    def routed(self) -> bool:
        return self.outcome == "routed_to_human"


def _season_for(month: int) -> Season:
    if month in (4, 5, 6):
        return Season.PEAK
    if month in (12, 1, 2):
        return Season.OFF_PEAK
    return Season.SHOULDER


def _travel_zone_for(match: MatchResult) -> TravelZone:
    """Take the zone from the matched customer's address on file.

    For a new customer we default to zone 1 rather than guessing from a parsed
    address string. Under-charging travel on a first quote is recoverable; a
    wrong surcharge on a stranger's first impression is not.
    """
    if match.customer and match.customer.addresses:
        primary = next((a for a in match.customer.addresses if a.is_primary), match.customer.addresses[0])
        try:
            return TravelZone(primary.travel_zone)
        except ValueError:
            return TravelZone.ZONE_1
    return TravelZone.ZONE_1


def run_pipeline(
    db: Session,
    *,
    job_request: JobRequest,
    client: LLMClient,
    model: str | None = None,
    persist_traces: bool = True,
) -> PipelineResult:
    model = model or getattr(client, "model", settings.llm_model)
    tracer = Tracer(
        db if persist_traces else None, model=model, job_request_id=job_request.id
    )
    tracer.start()

    job_request.status = JobRequestStatus.PROCESSING
    parsed: JobRequestParsed | None = None
    match: MatchResult | None = None

    try:
        # --- 1. parse ------------------------------------------------------
        with tracer.step("parse_job_request", {"chars": len(job_request.raw_source_text)}) as step:
            parsed = parse_job_request(
                client=client,
                email_text=job_request.raw_source_text,
                subject=job_request.subject,
                sender_email=job_request.sender_email,
                step=step,
            )

        # --- 2. ground the contact details ---------------------------------
        with tracer.step("verify_contact_grounding") as step:
            parsed, violations = verify_contact_grounding(parsed, job_request.raw_source_text)
            step.set_output({"violations": violations})

        job_request.parsed = parsed.model_dump(mode="json")
        job_request.field_confidence = parsed.field_confidence.model_dump(mode="json")

        # --- 3. match the customer -----------------------------------------
        with tracer.step("lookup_customer") as step:
            match = lookup_customer(
                db,
                name=parsed.contact.company or parsed.contact.name,
                email=parsed.contact.email or job_request.sender_email,
                address=parsed.property_address,
            )
            step.set_output(
                {
                    "matched": match.customer_id,
                    "score": match.score,
                    "is_new": match.is_new,
                    "reason": match.reason,
                    "candidates": [c.__dict__ for c in match.candidates[:3]],
                }
            )

        job_request.customer_id = match.customer.id if match.customer else None
        job_request.is_new_customer = match.is_new
        job_request.customer_match_score = round(match.score, 4)

        # --- 4. review flags ------------------------------------------------
        flags = evaluate_confidence(
            parsed, settings.field_confidence_threshold, is_new_customer=match.is_new
        )
        if match.needs_review:
            flags.add(match.reason)
        for v in violations:
            flags.add(v)

        job_request.needs_review = flags.needs_review
        job_request.review_reasons = flags.reasons

        # --- 5. scope gate --------------------------------------------------
        with tracer.step("check_scope") as step:
            check_scope(parsed)
            step.set_output({"in_scope": True})

        # --- 6. propose -----------------------------------------------------
        with tracer.step("propose_line_items") as step:
            proposed = propose_line_items(client=client, parsed=parsed, step=step)

        # --- 7. price (deterministic) ---------------------------------------
        with tracer.step("compute_quote", {"n_items": len(proposed)}) as step:
            context = PricingContext(
                urgency=parsed.urgency or Urgency.STANDARD,
                access_difficulty=parsed.access_difficulty or AccessDifficulty.EASY,
                travel_zone=_travel_zone_for(match),
                season=_season_for(job_request.received_at.month),
            )
            try:
                computed = compute_quote(
                    proposed,
                    context,
                    sanity_ceiling_cents=settings.quote_sanity_ceiling_cents,
                )
            except PricingError as exc:
                raise RouteToHuman(str(exc), kind="pricing_refused") from exc
            step.set_output(
                {
                    "total_cents": computed.total_cents,
                    "line_subtotal_cents": computed.line_subtotal_cents,
                    "n_lines": len(computed.line_items),
                }
            )

        # --- 8. draft --------------------------------------------------------
        recipient = parsed.contact.email or job_request.sender_email
        with tracer.step("draft_customer_email") as step:
            # Number allocated before drafting so the draft can cite it and the
            # verifier can confirm it did.
            quote_number = next_quote_number()
            drafted = draft_customer_email(
                client=client,
                quote=computed,
                quote_number=quote_number,
                customer_name=(
                    match.customer.contact_name if match.customer else parsed.contact.name
                ),
                original_email=job_request.raw_source_text,
                step=step,
            )

        # --- 9. submit for approval (terminal) -------------------------------
        with tracer.step("submit_for_approval") as step:
            quote, approval = submit_for_approval(
                db,
                job_request=job_request,
                computed=computed,
                drafted=drafted,
                to_email=recipient,
                # Only reference the run when its row was actually written.
                # The eval harness runs with persist_traces=False, and stamping
                # a run id that no agent_runs row backs violates the FK.
                run_id=tracer.run.run_id if persist_traces else None,
                quote_number=quote_number,
            )
            step.set_output(
                {
                    "quote_id": str(quote.id),
                    "quote_number": quote.quote_number,
                    "approval_id": str(approval.id),
                    "status": quote.status.value,
                    "needs_review": job_request.needs_review,
                }
            )

        run = tracer.finish(RunStatus.SUCCEEDED, outcome="submitted_for_approval")
        return PipelineResult(
            outcome="submitted_for_approval",
            run=run,
            job_request=job_request,
            quote=quote,
            approval=approval,
            parsed=parsed,
            match=match,
            computed=computed,
        )

    except RouteToHuman as exc:
        with tracer.step("route_to_human", {"kind": exc.kind}) as step:
            job_request.status = JobRequestStatus.ROUTED_TO_HUMAN
            job_request.routed_reason = exc.reason
            job_request.needs_review = True
            reasons = list(job_request.review_reasons or [])
            if exc.reason not in reasons:
                reasons.append(exc.reason)
            job_request.review_reasons = reasons
            db.flush()
            step.set_output({"kind": exc.kind, "reason": exc.reason})

        run = tracer.finish(RunStatus.ROUTED_TO_HUMAN, outcome="routed_to_human")
        logger.info("routed %s to human: %s (%s)", job_request.id, exc.reason, exc.kind)
        return PipelineResult(
            outcome="routed_to_human",
            run=run,
            job_request=job_request,
            parsed=parsed,
            match=match,
            route_reason=exc.reason,
            route_kind=exc.kind,
        )

    except Exception as exc:  # noqa: BLE001 — the run must be closed out
        job_request.status = JobRequestStatus.FAILED
        run = tracer.finish(RunStatus.FAILED, error=f"{type(exc).__name__}: {exc}")
        logger.exception("pipeline failed for job request %s", job_request.id)
        raise

