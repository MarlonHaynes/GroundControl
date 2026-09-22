"""Load a coherent demo state.

Idempotent: re-running replaces the demo data rather than duplicating it. The
catalog, pricing rules, and customers always load. Job requests load from the
fixtures if they have been generated.

By default this seeds *data* only and does not run the pipeline, so `make seed`
costs nothing. Pass --run-pipeline N to additionally process N requests through
the agent, which is what produces a populated approval queue and trace viewer
for a demo.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

from sqlalchemy import delete, select

from db.models import (
    Address,
    AgentRun,
    Approval,
    Customer,
    CustomerStatus,
    DraftMessage,
    EvalResult,
    EvalRun,
    JobRequest,
    LineItem,
    PricingRule,
    Quote,
    SentMessage,
    ServiceCatalogItem,
    TraceStep,
)
from db.session import session_scope
from evals.dataset import CASES_PATH, load_cases, load_customers
from pricing.catalog_data import (
    ACCESS_MULTIPLIER,
    CATALOG,
    GLOBAL_JOB_MINIMUM_CENTS,
    SEASON_MULTIPLIER,
    TAX_RATE,
    TRAVEL_SURCHARGE_CENTS,
    TREE_REMOVAL_SURCHARGE_CENTS,
    URGENCY_MULTIPLIER,
)

RULE_ROWS = [
    ("LINE_MINIMUM_CHARGE", "minimum", "Per-service minimum charge.", "per_service", {}, 13),
    (
        "TREE_DIAMETER_SURCHARGE",
        "surcharge",
        "Tree removal surcharge by trunk diameter band.",
        "TREE_REMOVAL",
        {"bands_cents": {k.value: v for k, v in TREE_REMOVAL_SURCHARGE_CENTS.items()}},
        11,
    ),
    (
        "ACCESS_DIFFICULTY_MODIFIER",
        "multiplier",
        "Site access multiplier on labor-sensitive services.",
        "labor_sensitive",
        {"multipliers": {k.value: str(v) for k, v in ACCESS_MULTIPLIER.items()}},
        12,
    ),
    (
        "URGENCY_MULTIPLIER",
        "multiplier",
        "Rush and emergency scheduling multiplier.",
        "global",
        {"multipliers": {k.value: str(v) for k, v in URGENCY_MULTIPLIER.items()}},
        30,
    ),
    (
        "SEASONAL_MULTIPLIER",
        "multiplier",
        "Peak and off-peak seasonal multiplier.",
        "global",
        {"multipliers": {k.value: str(v) for k, v in SEASON_MULTIPLIER.items()}},
        31,
    ),
    (
        "TRAVEL_ZONE_SURCHARGE",
        "surcharge",
        "Flat surcharge by travel zone, applied after multipliers.",
        "global",
        {"surcharges_cents": {k.value: v for k, v in TRAVEL_SURCHARGE_CENTS.items()}},
        40,
    ),
    (
        "GLOBAL_JOB_MINIMUM",
        "minimum",
        "Minimum total job charge before tax.",
        "global",
        {"minimum_cents": GLOBAL_JOB_MINIMUM_CENTS},
        50,
    ),
    ("SALES_TAX", "tax", "Connecticut sales tax on landscaping services.", "global",
     {"rate": str(TAX_RATE)}, 60),
]


def reset(db) -> None:
    """Clear demo data. Order respects foreign keys."""
    for model in (
        EvalResult, EvalRun, SentMessage, TraceStep, AgentRun, Approval,
        DraftMessage, LineItem, Quote, JobRequest, Address, Customer,
        PricingRule, ServiceCatalogItem,
    ):
        db.execute(delete(model))
    db.flush()


def seed_catalog(db) -> int:
    for item in CATALOG:
        db.add(
            ServiceCatalogItem(
                code=item.code,
                name=item.name,
                service_type=item.service_type,
                unit=item.unit.value,
                base_rate_cents=item.base_rate_cents,
                min_charge_cents=item.min_charge_cents,
                description=item.description,
                labor_sensitive=item.labor_sensitive,
                keywords=list(item.keywords),
            )
        )
    for code, kind, description, applies_to, config, order in RULE_ROWS:
        db.add(
            PricingRule(
                code=code, kind=kind, description=description,
                applies_to=applies_to, config=config, apply_order=order,
            )
        )
    db.flush()
    return len(CATALOG)


def seed_customers(db) -> int:
    fixtures = load_customers()
    for fc in fixtures:
        c = Customer(
            name=fc.name,
            contact_name=fc.contact_name,
            email=fc.email,
            phone=fc.phone,
            notes=fc.notes or None,
            status=CustomerStatus.EXISTING,
        )
        a = fc.address
        c.addresses.append(
            Address(
                line1=a.line1,
                city=a.city,
                state=a.state,
                postal_code=a.postal_code,
                travel_zone=a.travel_zone.value,
                property_notes=a.property_notes or None,
                is_primary=True,
            )
        )
        db.add(c)
    db.flush()
    return len(fixtures)


def seed_job_requests(db, limit: int | None = None) -> int:
    if not CASES_PATH.exists():
        print(
            "  ! fixtures/job_requests.jsonl not found — run "
            "`python -m scripts.generate_dataset` first.",
            file=sys.stderr,
        )
        cases = load_cases(include_adversarial=True)
    else:
        cases = load_cases(include_adversarial=True, limit=limit)

    for case in cases:
        gt = case.ground_truth
        db.add(
            JobRequest(
                raw_source_text=case.email_body,
                subject=case.email_subject,
                sender_email=case.sender_email,
                received_at=datetime.fromisoformat(case.received_at),
                fixture_case_id=gt.case_id,
            )
        )
    db.flush()
    return len(cases)


def run_pipeline_on(db, count: int) -> tuple[int, int]:
    """Process the first N requests so the demo has a populated queue."""
    from agent.llm import build_llm_client
    from agent.loop import run_pipeline

    client = build_llm_client()
    requests = (
        db.execute(select(JobRequest).order_by(JobRequest.received_at).limit(count))
        .scalars()
        .all()
    )

    quoted = routed = 0
    for i, jr in enumerate(requests, 1):
        result = run_pipeline(db, job_request=jr, client=client)
        if result.routed:
            routed += 1
        else:
            quoted += 1
        if i % 5 == 0:
            print(f"    processed {i}/{len(requests)}")
    db.flush()
    return quoted, routed


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=None, help="cap the job requests loaded")
    ap.add_argument(
        "--run-pipeline",
        type=int,
        default=0,
        metavar="N",
        help="after seeding, run the agent on N requests (costs API spend)",
    )
    ap.add_argument("--keep", action="store_true", help="do not clear existing data first")
    args = ap.parse_args()

    with session_scope() as db:
        if not args.keep:
            print("Clearing existing data...")
            reset(db)

        n_services = seed_catalog(db)
        print(f"  catalog      {n_services} services, {len(RULE_ROWS)} pricing rules")

        n_customers = seed_customers(db)
        print(f"  customers    {n_customers}")

        n_requests = seed_job_requests(db, args.limit)
        print(f"  job requests {n_requests}")

        if args.run_pipeline:
            print(f"\nRunning the pipeline on {args.run_pipeline} requests...")
            quoted, routed = run_pipeline_on(db, args.run_pipeline)
            print(f"  quoted           {quoted}")
            print(f"  routed to human  {routed}")

    print("\nSeed complete.")
    if not args.run_pipeline:
        print("Tip: `--run-pipeline 10` populates the approval queue and trace viewer.")


if __name__ == "__main__":
    main()
