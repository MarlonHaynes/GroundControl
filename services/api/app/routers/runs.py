"""Trace viewer: agent runs and their steps."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api_schemas import AgentRunDetail, AgentRunSummary, DashboardStats
from app.serializers import agent_run_detail, agent_run_summary
from db.models import AgentRun, JobRequest, JobRequestStatus, Quote, QuoteStatus, RunStatus
from db.session import get_db
from observability.cost import fmt_microcents

router = APIRouter(prefix="/api", tags=["observability"])


@router.get("/runs", response_model=list[AgentRunSummary])
def list_runs(
    db: Session = Depends(get_db),
    status: RunStatus | None = None,
    limit: int = Query(default=100, le=500),
    offset: int = 0,
) -> list[AgentRunSummary]:
    stmt = select(AgentRun).order_by(AgentRun.started_at.desc())
    if status is not None:
        stmt = stmt.where(AgentRun.status == status)
    rows = db.execute(stmt.limit(limit).offset(offset)).scalars().all()
    return [agent_run_summary(r) for r in rows]


@router.get("/runs/{run_id}", response_model=AgentRunDetail)
def get_run(run_id: uuid.UUID, db: Session = Depends(get_db)) -> AgentRunDetail:
    run = db.get(AgentRun, run_id)
    if run is None:
        raise HTTPException(404, f"No run {run_id}")
    return agent_run_detail(run)


@router.get("/stats", response_model=DashboardStats)
def stats(db: Session = Depends(get_db)) -> DashboardStats:
    def count(stmt) -> int:
        return db.execute(stmt).scalar_one() or 0

    total_requests = count(select(func.count()).select_from(JobRequest))
    spend = count(select(func.coalesce(func.sum(AgentRun.total_cost_microcents), 0)))
    n_runs = count(select(func.count()).select_from(AgentRun))

    return DashboardStats(
        total_job_requests=total_requests,
        pending_approval=count(
            select(func.count()).select_from(Quote).where(Quote.status == QuoteStatus.PENDING_APPROVAL)
        ),
        needs_review=count(
            select(func.count()).select_from(JobRequest).where(JobRequest.needs_review.is_(True))
        ),
        routed_to_human=count(
            select(func.count())
            .select_from(JobRequest)
            .where(JobRequest.status == JobRequestStatus.ROUTED_TO_HUMAN)
        ),
        sent=count(select(func.count()).select_from(Quote).where(Quote.status == QuoteStatus.SENT)),
        rejected=count(
            select(func.count()).select_from(Quote).where(Quote.status == QuoteStatus.REJECTED)
        ),
        total_spend_microcents=spend,
        total_spend_display=fmt_microcents(spend),
        mean_cost_per_run_microcents=(spend // n_runs) if n_runs else 0,
        mean_cost_per_run_display=fmt_microcents((spend // n_runs) if n_runs else 0),
    )
