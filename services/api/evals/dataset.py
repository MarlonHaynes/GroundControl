"""Labeled dataset schema and loaders.

The dataset is generated label-first: a `GroundTruth` record is authored by
code, its expected total is computed by the *real* pricing engine, and only
then is a messy customer email rendered from it. Labels are therefore correct
by construction rather than by annotation, and quote-correctness scoring
measures the model's proposal quality rather than pricing drift.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from app.config import API_ROOT
from pricing.types import AccessDifficulty, Season, TravelZone, Urgency

FIXTURES_DIR = API_ROOT / "fixtures"
CUSTOMERS_PATH = FIXTURES_DIR / "customers.json"
CASES_PATH = FIXTURES_DIR / "job_requests.jsonl"
ADVERSARIAL_PATH = FIXTURES_DIR / "adversarial.jsonl"
CATALOG_PATH = FIXTURES_DIR / "catalog.json"


class FixtureAddress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    line1: str
    city: str
    state: str = "CT"
    postal_code: str
    travel_zone: TravelZone = TravelZone.ZONE_1
    property_notes: str = ""


class FixtureCustomer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    contact_name: str | None = None
    email: str | None = None
    phone: str | None = None
    notes: str = ""
    address: FixtureAddress


class GroundTruthService(BaseModel):
    model_config = ConfigDict(extra="forbid")

    catalog_code: str
    quantity: float
    trunk_diameter_band: str | None = None


class GroundTruth(BaseModel):
    """The authored truth for one case. Written before the email exists."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    tags: list[str] = Field(default_factory=list)
    mess_profile: str = "plain"

    # --- who ---------------------------------------------------------------
    customer_name: str
    contact_name: str | None = None
    customer_email: str | None = None
    customer_phone: str | None = None
    expected_customer_id: str | None = Field(
        default=None, description="Fixture customer id this should link to, or null if new"
    )
    expect_new_customer: bool = False

    # --- what --------------------------------------------------------------
    property_address: str | None = None
    property_size_sqft: int | None = None
    services: list[GroundTruthService] = Field(default_factory=list)
    special_requests: list[str] = Field(default_factory=list)
    urgency: Urgency = Urgency.STANDARD
    access_difficulty: AccessDifficulty = AccessDifficulty.EASY
    travel_zone: TravelZone = TravelZone.ZONE_1
    season: Season = Season.SHOULDER

    # --- what should happen -------------------------------------------------
    expect_quote: bool = True
    expect_route_to_human: bool = False
    route_reason_kind: str | None = Field(
        default=None,
        description="out_of_scope | incomplete | contradictory — why a human must look",
    )
    expected_total_cents: int | None = None

    @property
    def expected_catalog_codes(self) -> set[str]:
        return {s.catalog_code for s in self.services}


class EvalCase(BaseModel):
    """A ground truth plus the rendered email a customer would have sent."""

    model_config = ConfigDict(extra="forbid")

    ground_truth: GroundTruth
    email_subject: str
    email_body: str
    sender_email: str | None = None
    received_at: str


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def load_customers(path: Path = CUSTOMERS_PATH) -> list[FixtureCustomer]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [FixtureCustomer.model_validate(row) for row in data]


def _read_jsonl(path: Path) -> list[EvalCase]:
    if not path.exists():
        return []
    cases: list[EvalCase] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if raw:
            cases.append(EvalCase.model_validate_json(raw))
    return cases


def load_cases(
    *,
    include_adversarial: bool = True,
    tags: set[str] | None = None,
    limit: int | None = None,
) -> list[EvalCase]:
    """Load the labeled dataset.

    `limit` takes a deterministic stride through the dataset rather than the
    first N, so a subset run still covers the full spread of mess profiles and
    services instead of whatever happens to sort first.
    """
    cases = _read_jsonl(CASES_PATH)
    if include_adversarial:
        cases += _read_jsonl(ADVERSARIAL_PATH)

    if tags:
        cases = [c for c in cases if tags & set(c.ground_truth.tags)]

    cases.sort(key=lambda c: c.ground_truth.case_id)

    if limit is not None and 0 < limit < len(cases):
        stride = len(cases) / limit
        cases = [cases[int(i * stride)] for i in range(limit)]

    return cases


def write_cases(cases: list[EvalCase], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for case in cases:
            fh.write(case.model_dump_json() + "\n")
