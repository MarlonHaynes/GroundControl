"""The agent's tool contracts.

Every LLM call in GroundControl is constrained to one of these schemas. The
model never returns free text that code then has to parse; it returns a typed
object or the call is a failure.

Two constraints are load-bearing and deliberately expressed in the type system
rather than in a prompt:

1. `ProposedLineItem` (in `pricing.types`) has no price field. The model cannot
   name a dollar amount even if it wants to.
2. `ParsedContact` fields are verified against the source text after parsing.
   The schema permits a contact value; `guardrails.verify_contact_grounding`
   rejects one that was not literally present in the email.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from pricing.types import AccessDifficulty, Urgency


class ParsedContact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, description="Person's name, exactly as written")
    company: str | None = Field(default=None, description="Company or property name")
    email: str | None = Field(default=None, description="Email address, copied verbatim")
    phone: str | None = Field(default=None, description="Phone number, copied verbatim")


class ParsedService(BaseModel):
    """One service the customer asked for, in their words plus a catalog guess."""

    model_config = ConfigDict(extra="forbid")

    request_text: str = Field(description="What the customer asked for, in their words")
    catalog_code: str = Field(description="Best-matching service code from the catalog")
    quantity_hint: str | None = Field(
        default=None,
        description="Any size or amount the customer stated, verbatim ('about half an acre')",
    )


class FieldConfidence(BaseModel):
    """Per-field extraction confidence. Low values flag the request for review."""

    model_config = ConfigDict(extra="forbid")

    contact: float = Field(ge=0.0, le=1.0)
    property_address: float = Field(ge=0.0, le=1.0)
    property_size_sqft: float = Field(ge=0.0, le=1.0)
    services_requested: float = Field(ge=0.0, le=1.0)
    special_requests: float = Field(ge=0.0, le=1.0)
    urgency: float = Field(ge=0.0, le=1.0)
    access_difficulty: float = Field(ge=0.0, le=1.0)

    def below(self, threshold: float) -> list[str]:
        return sorted(
            name for name, value in self.model_dump().items() if float(value) < threshold
        )


class JobRequestParsed(BaseModel):
    """Structured form of an inbound job request. Output of `parse_job_request`."""

    model_config = ConfigDict(extra="forbid")

    contact: ParsedContact
    property_address: str | None = Field(
        default=None, description="Service address if stated; null if not mentioned"
    )
    property_size_sqft: int | None = Field(
        default=None,
        description=(
            "Lawn or work area in square feet. Convert stated acres (1 acre = 43560 sq ft). "
            "Null if the customer gave no usable size."
        ),
    )
    services_requested: list[ParsedService] = Field(default_factory=list)
    special_requests: list[str] = Field(
        default_factory=list, description="Constraints and asks that are not services"
    )
    urgency: Urgency = Urgency.STANDARD
    access_difficulty: AccessDifficulty = AccessDifficulty.EASY

    # --- scope and completeness ------------------------------------------
    is_in_scope: bool = Field(
        default=True,
        description="False if the customer asked for work Riverside does not do at all",
    )
    out_of_scope_reason: str | None = Field(default=None)
    missing_information: list[str] = Field(
        default_factory=list,
        description="Facts needed to quote that the email does not provide",
    )
    contradictions: list[str] = Field(
        default_factory=list,
        description="Statements in the email that cannot both be true",
    )

    field_confidence: FieldConfidence


class ProposalResult(BaseModel):
    """Output of `propose_line_items`. Still no prices anywhere."""

    model_config = ConfigDict(extra="forbid")

    line_items: list[ProposedLineItemLLM] = Field(default_factory=list)
    unpriceable_reason: str | None = Field(
        default=None,
        description="Set when the request cannot be turned into catalog line items",
    )


class ProposedLineItemLLM(BaseModel):
    """What the model returns. Mirrors pricing.ProposedLineItem, minus money.

    Quantity is a float on the wire because JSON has no decimal type; it is
    converted to Decimal before it reaches the pricing engine.
    """

    model_config = ConfigDict(extra="forbid")

    catalog_code: str = Field(description="Service code, exactly as it appears in the catalog")
    quantity: float = Field(gt=0, description="Quantity in that service's unit")
    rationale: str = Field(description="One sentence: why this service and this quantity")
    confidence: float = Field(ge=0.0, le=1.0)
    trunk_diameter_band: str | None = Field(
        default=None,
        description=(
            "Required for TREE_REMOVAL. One of: under_12in, 12_24in, 24_36in, over_36in. "
            "Null if the customer did not say."
        ),
    )


class DraftedEmail(BaseModel):
    """Output of `draft_customer_email`."""

    model_config = ConfigDict(extra="forbid")

    subject: str = Field(description="Email subject line")
    body: str = Field(description="Plain-text email body, signed by Riverside Grounds")


ProposalResult.model_rebuild()
