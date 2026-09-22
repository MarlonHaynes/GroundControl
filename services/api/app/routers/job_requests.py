"""Job request ingestion and the Inbox screen."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from agent.llm import LLMError, build_llm_client
from agent.loop import run_pipeline
from app.api_schemas import CreateJobRequest, JobRequestDetail, JobRequestSummary
from app.serializers import job_request_detail, job_request_summary
from db.models import AgentRun, JobRequest, JobRequestStatus
from db.session import get_db

router = APIRouter(prefix="/api/job-requests", tags=["job requests"])


def _latest_run_ids(db: Session, job_request_ids: list[uuid.UUID]) -> dict[uuid.UUID, uuid.UUID]:
    if not job_request_ids:
        return {}
    subq = (
        select(AgentRun.job_request_id, func.max(AgentRun.started_at).label("latest"))
        .where(AgentRun.job_request_id.in_(job_request_ids))
        .group_by(AgentRun.job_request_id)
        .subquery()
    )
    rows = db.execute(
        select(AgentRun.job_request_id, AgentRun.id)
        .join(
            subq,
            (AgentRun.job_request_id == subq.c.job_request_id)
            & (AgentRun.started_at == subq.c.latest),
        )
    ).all()
    return {jr_id: run_id for jr_id, run_id in rows}


@router.get("", response_model=list[JobRequestSummary])
def list_job_requests(
    db: Session = Depends(get_db),
    status: JobRequestStatus | None = None,
    needs_review: bool | None = None,
    limit: int = Query(default=100, le=500),
    offset: int = 0,
) -> list[JobRequestSummary]:
    stmt = select(JobRequest).order_by(JobRequest.received_at.desc())
    if status is not None:
        stmt = stmt.where(JobRequest.status == status)
    if needs_review is not None:
        stmt = stmt.where(JobRequest.needs_review == needs_review)

    rows = db.execute(stmt.limit(limit).offset(offset)).scalars().all()
    runs = _latest_run_ids(db, [r.id for r in rows])
    return [job_request_summary(r, runs.get(r.id)) for r in rows]


@router.get("/{job_request_id}", response_model=JobRequestDetail)
def get_job_request(job_request_id: uuid.UUID, db: Session = Depends(get_db)) -> JobRequestDetail:
    jr = db.get(JobRequest, job_request_id)
    if jr is None:
        raise HTTPException(404, f"No job request {job_request_id}")
    runs = _latest_run_ids(db, [jr.id])
    return job_request_detail(jr, runs.get(jr.id))


@router.post("", response_model=JobRequestDetail, status_code=201)
def create_job_request(
    payload: CreateJobRequest, db: Session = Depends(get_db)
) -> JobRequestDetail:
    """Ingest an inbound email and, by default, run the pipeline on it.

    The pipeline is synchronous here. For a company receiving a few dozen
    requests a day that is the right call: it keeps the request/response model
    honest and avoids a queue the deployment does not need yet. A real
    production deployment would move this to a worker; that is called out in
    the README.
    """
    jr = JobRequest(
        raw_source_text=payload.raw_source_text,
        subject=payload.subject,
        sender_email=payload.sender_email,
        received_at=payload.received_at or datetime.now(UTC),
        status=JobRequestStatus.RECEIVED,
    )
    db.add(jr)
    db.flush()

    if payload.run_now:
        try:
            client = build_llm_client()
        except LLMError as exc:
            db.commit()
            raise HTTPException(
                503,
                f"Job request ingested but the pipeline could not run: {exc}",
            ) from exc

        try:
            run_pipeline(db, job_request=jr, client=client)
        except Exception as exc:  # noqa: BLE001
            db.commit()  # keep the failed run and its trace
            raise HTTPException(500, f"Pipeline failed: {type(exc).__name__}: {exc}") from exc

    db.commit()
    db.refresh(jr)
    runs = _latest_run_ids(db, [jr.id])
    return job_request_detail(jr, runs.get(jr.id))


@router.post("/{job_request_id}/rerun", response_model=JobRequestDetail)
def rerun_pipeline(job_request_id: uuid.UUID, db: Session = Depends(get_db)) -> JobRequestDetail:
    """Re-run the pipeline on an existing request, e.g. after a prompt change."""
    jr = db.get(JobRequest, job_request_id)
    if jr is None:
        raise HTTPException(404, f"No job request {job_request_id}")

    sent = [q for q in jr.quotes if q.sent_at is not None]
    if sent:
        raise HTTPException(
            409,
            f"Job request {job_request_id} already produced a sent quote "
            f"({sent[0].quote_number}). Re-running would create a second quote for "
            f"work the customer has already been told the price of.",
        )

    try:
        client = build_llm_client()
    except LLMError as exc:
        raise HTTPException(503, str(exc)) from exc

    run_pipeline(db, job_request=jr, client=client)
    db.commit()
    db.refresh(jr)
    runs = _latest_run_ids(db, [jr.id])
    return job_request_detail(jr, runs.get(jr.id))
