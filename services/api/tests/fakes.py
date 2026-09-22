"""Builders for test data and scripted LLM responses.

Everything here is deterministic. No test in this suite touches the network.
"""

from __future__ import annotations

from datetime import UTC, datetime

from agent.llm import FakeLLMClient
from agent.schemas import (
    DraftedEmail,
    FieldConfidence,
    JobRequestParsed,
    ParsedContact,
    ParsedService,
    ProposalResult,
    ProposedLineItemLLM,
)
from db.models import Address, Customer, CustomerStatus, JobRequest
from pricing.types import AccessDifficulty, Urgency


def confidence(value: float = 0.95, **overrides: float) -> FieldConfidence:
    base = {
        "contact": value,
        "property_address": value,
        "property_size_sqft": value,
        "services_requested": value,
        "special_requests": value,
        "urgency": value,
        "access_difficulty": value,
    }
    base.update(overrides)
    return FieldConfidence(**base)


GOLDEN_EMAIL = """\
Hi there,

We'd like to get the lawn mowed at 34 Wampanoag Drive, West Hartford. It's
about 20,000 square feet. Could you also edge along the driveway and the front
walk while you're there? Probably a couple of hours of trimming.

No huge rush, sometime in the next week or two is fine.

Thanks,
Amara Osei
amara.osei@gmail.com
(860) 555-0221
"""


def golden_parsed() -> JobRequestParsed:
    return JobRequestParsed(
        contact=ParsedContact(
            name="Amara Osei",
            company=None,
            email="amara.osei@gmail.com",
            phone="(860) 555-0221",
        ),
        property_address="34 Wampanoag Drive, West Hartford",
        property_size_sqft=20_000,
        services_requested=[
            ParsedService(
                request_text="get the lawn mowed",
                catalog_code="MOW_STD",
                quantity_hint="about 20,000 square feet",
            ),
            ParsedService(
                request_text="edge along the driveway and the front walk",
                catalog_code="EDGE_TRIM",
                quantity_hint="a couple of hours",
            ),
        ],
        special_requests=[],
        urgency=Urgency.STANDARD,
        access_difficulty=AccessDifficulty.EASY,
        is_in_scope=True,
        missing_information=[],
        contradictions=[],
        field_confidence=confidence(0.93),
    )


def golden_proposal() -> ProposalResult:
    return ProposalResult(
        line_items=[
            ProposedLineItemLLM(
                catalog_code="MOW_STD",
                quantity=20_000,
                rationale="Customer stated 20,000 sq ft of lawn.",
                confidence=0.95,
            ),
            ProposedLineItemLLM(
                catalog_code="EDGE_TRIM",
                quantity=2,
                rationale="Customer estimated a couple of hours of edging and trimming.",
                confidence=0.8,
            ),
        ]
    )


# MOW_STD 20,000 sqft = $240.00, EDGE_TRIM 2h = $116.00, subtotal $356.00,
# tax $22.61, total $378.61. The draft cites exactly these figures.
def golden_draft(quote_number: str = "RG-202609-ABCDE") -> DraftedEmail:
    return DraftedEmail(
        subject=f"Your quote from Riverside Grounds ({quote_number})",
        body=(
            "Hi Amara,\n\n"
            "Thanks for getting in touch. Here is the quote for the work at 34 "
            "Wampanoag Drive.\n\n"
            "Lawn Mowing (Standard): $240.00\n"
            "Edging and String Trimming: $116.00\n\n"
            "Subtotal: $356.00\n"
            "Sales tax: $22.61\n"
            "Total: $378.61\n\n"
            f"This is quote {quote_number}. If that looks right, reply and our crew "
            "will reach out to get you on the schedule.\n\n"
            "The team at Riverside Grounds"
        ),
    )


def golden_client(quote_number: str = "RG-202609-ABCDE") -> FakeLLMClient:
    """A fake scripted for the happy path.

    The draft is returned by a callable so it can echo whichever quote number
    the pipeline allocated at run time — the draft verifier checks for it.
    """

    def draft_for(prompt: str) -> DraftedEmail:
        number = quote_number
        for token in prompt.split():
            if token.startswith("RG-"):
                number = token.strip(".,)")
                break
        return golden_draft(number)

    return FakeLLMClient(
        {
            "JobRequestParsed": golden_parsed(),
            "ProposalResult": golden_proposal(),
            "DraftedEmail": draft_for,
        }
    )


# ---------------------------------------------------------------------------
# Database builders
# ---------------------------------------------------------------------------


def make_customer(
    db,
    *,
    name: str = "Amara Osei",
    email: str | None = "amara.osei@gmail.com",
    contact_name: str | None = "Amara Osei",
    line1: str = "34 Wampanoag Drive",
    city: str = "West Hartford",
    postal_code: str = "06117",
    travel_zone: str = "zone_1",
) -> Customer:
    c = Customer(
        name=name,
        contact_name=contact_name,
        email=email,
        phone="(860) 555-0221",
        status=CustomerStatus.EXISTING,
    )
    c.addresses.append(
        Address(
            line1=line1,
            city=city,
            state="CT",
            postal_code=postal_code,
            travel_zone=travel_zone,
            is_primary=True,
        )
    )
    db.add(c)
    db.flush()
    return c


def make_job_request(
    db,
    *,
    body: str = GOLDEN_EMAIL,
    subject: str | None = "Lawn mowing quote",
    sender_email: str | None = "amara.osei@gmail.com",
    received_at: datetime | None = None,
) -> JobRequest:
    jr = JobRequest(
        raw_source_text=body,
        subject=subject,
        sender_email=sender_email,
        received_at=received_at or datetime(2026, 9, 10, 9, 0, tzinfo=UTC),
    )
    db.add(jr)
    db.flush()
    return jr
