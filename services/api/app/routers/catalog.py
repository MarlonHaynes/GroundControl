"""Catalog, pricing rules, customers, and eval results."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api_schemas import (
    CatalogItemOut,
    CatalogOut,
    CustomerOut,
    EvalCaseResultOut,
    EvalRunDetail,
    EvalRunOut,
    Money,
    PricingRuleOut,
)
from app.serializers import customer_out
from db.models import Customer, EvalRun, PricingRule, ServiceCatalogItem
from db.session import get_db
from observability.cost import fmt_microcents

router = APIRouter(prefix="/api", tags=["reference"])


@router.get("/catalog", response_model=CatalogOut)
def get_catalog(db: Session = Depends(get_db)) -> CatalogOut:
    services = (
        db.execute(
            select(ServiceCatalogItem)
            .where(ServiceCatalogItem.active.is_(True))
            .order_by(ServiceCatalogItem.service_type, ServiceCatalogItem.code)
        )
        .scalars()
        .all()
    )
    rules = (
        db.execute(
            select(PricingRule)
            .where(PricingRule.active.is_(True))
            .order_by(PricingRule.apply_order)
        )
        .scalars()
        .all()
    )
    return CatalogOut(
        services=[
            CatalogItemOut(
                code=s.code,
                name=s.name,
                service_type=s.service_type,
                unit=s.unit,
                base_rate_cents=s.base_rate_cents,
                min_charge=Money.of(s.min_charge_cents),
                description=s.description,
                labor_sensitive=s.labor_sensitive,
            )
            for s in services
        ],
        rules=[
            PricingRuleOut(
                code=r.code,
                kind=r.kind,
                description=r.description,
                applies_to=r.applies_to,
                config=r.config or {},
                apply_order=r.apply_order,
            )
            for r in rules
        ],
    )


@router.get("/customers", response_model=list[CustomerOut])
def list_customers(
    db: Session = Depends(get_db), limit: int = Query(default=200, le=1000)
) -> list[CustomerOut]:
    rows = (
        db.execute(select(Customer).order_by(Customer.name).limit(limit)).scalars().all()
    )
    return [customer_out(c) for c in rows]


def _eval_out(r: EvalRun) -> EvalRunOut:
    return EvalRunOut(
        id=r.id,
        started_at=r.started_at,
        ended_at=r.ended_at,
        model=r.model,
        n_cases=r.n_cases,
        subset=r.subset,
        metrics=r.metrics or {},
        thresholds=r.thresholds or {},
        passed=r.passed,
        total_cost_microcents=r.total_cost_microcents,
        cost_display=fmt_microcents(r.total_cost_microcents),
        notes=r.notes,
    )


@router.get("/evals", response_model=list[EvalRunOut])
def list_eval_runs(
    db: Session = Depends(get_db), limit: int = Query(default=20, le=100)
) -> list[EvalRunOut]:
    rows = (
        db.execute(select(EvalRun).order_by(EvalRun.started_at.desc()).limit(limit))
        .scalars()
        .all()
    )
    return [_eval_out(r) for r in rows]


@router.get("/evals/latest", response_model=EvalRunDetail)
def latest_eval_run(db: Session = Depends(get_db)) -> EvalRunDetail:
    run = db.execute(
        select(EvalRun).order_by(EvalRun.started_at.desc()).limit(1)
    ).scalar_one_or_none()
    if run is None:
        raise HTTPException(404, "No eval runs recorded yet. Run `make eval-full`.")
    return _eval_detail(run)


@router.get("/evals/{eval_run_id}", response_model=EvalRunDetail)
def get_eval_run(eval_run_id: uuid.UUID, db: Session = Depends(get_db)) -> EvalRunDetail:
    run = db.get(EvalRun, eval_run_id)
    if run is None:
        raise HTTPException(404, f"No eval run {eval_run_id}")
    return _eval_detail(run)


def _eval_detail(run: EvalRun) -> EvalRunDetail:
    base = _eval_out(run)
    return EvalRunDetail(
        **base.model_dump(),
        results=[
            EvalCaseResultOut(
                case_id=r.case_id,
                tags=list(r.tags or []),
                passed=r.passed,
                scores=r.scores or {},
                diffs=r.diffs or {},
                cost_microcents=r.cost_microcents,
                latency_ms=r.latency_ms,
            )
            for r in sorted(run.results, key=lambda r: r.case_id)
        ],
    )
