"""Guardrails.

Every rule here is enforced in code. None of them depends on the model
cooperating, because a guardrail that the model can talk its way past is not a
guardrail — and two of the adversarial fixtures try exactly that.

Four rules:

1. **Contact grounding.** A contact detail that is not literally present in the
   source email is dropped. The model cannot introduce an address to send to.
2. **Confidence flagging.** Any field below threshold, and any new customer,
   raises `needs_review` with a human-readable reason.
3. **Scope gate.** Out-of-scope, materially incomplete, and self-contradictory
   requests terminate the pipeline before a quote exists.
4. **Approval gate.** Nothing customer-facing happens without a resolved
   approving `Approval` row. Enforced in `require_approval`, which is the only
   path to the mock send.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from agent.schemas import JobRequestParsed
from db.models import Approval, ApprovalAction, ApprovalStatus, Quote, QuoteStatus

# ---------------------------------------------------------------------------
# 1. Contact grounding
# ---------------------------------------------------------------------------

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _normalize(value: str) -> str:
    return _NON_ALNUM.sub("", value.lower())


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def verify_contact_grounding(
    parsed: JobRequestParsed, source_text: str
) -> tuple[JobRequestParsed, list[str]]:
    """Drop any contact value that does not appear in the source text.

    A hallucinated email address is the one extraction error that could send a
    customer's quote to a stranger, so it is removed rather than flagged.
    Phone numbers are compared digits-only so formatting differences
    ('(860) 555-0142' vs '860-555-0142') do not read as fabrication.
    """
    violations: list[str] = []
    contact = parsed.contact.model_copy()
    haystack = _normalize(source_text)
    haystack_digits = _digits(source_text)

    if contact.email and _normalize(contact.email) not in haystack:
        violations.append(f"dropped email {contact.email!r}: not present in the source text")
        contact.email = None

    if contact.phone:
        d = _digits(contact.phone)
        if not d or d not in haystack_digits:
            violations.append(f"dropped phone {contact.phone!r}: not present in the source text")
            contact.phone = None

    if contact.name and _normalize(contact.name) not in haystack:
        violations.append(f"dropped name {contact.name!r}: not present in the source text")
        contact.name = None

    return parsed.model_copy(update={"contact": contact}), violations


# ---------------------------------------------------------------------------
# 2 & 3. Review flags and the scope gate
# ---------------------------------------------------------------------------


class RouteToHuman(Exception):
    """Terminates the pipeline with a reason instead of producing a quote."""

    def __init__(self, reason: str, *, kind: str) -> None:
        super().__init__(reason)
        self.reason = reason
        self.kind = kind  # out_of_scope | incomplete | contradictory | pricing_refused


@dataclass
class ReviewFlags:
    needs_review: bool = False
    reasons: list[str] = field(default_factory=list)

    def add(self, reason: str) -> None:
        self.needs_review = True
        if reason not in self.reasons:
            self.reasons.append(reason)


def check_scope(parsed: JobRequestParsed) -> None:
    """Raise RouteToHuman when the request must not be auto-quoted.

    Order matters for the eval's reason-kind scoring: an out-of-scope request
    that is also vague is reported as out-of-scope, because that is the fact a
    human needs first.
    """
    if not parsed.is_in_scope:
        raise RouteToHuman(
            parsed.out_of_scope_reason or "Request is outside the service catalog.",
            kind="out_of_scope",
        )

    if parsed.contradictions:
        raise RouteToHuman(
            "The request contains details that cannot both be true: "
            + "; ".join(parsed.contradictions),
            kind="contradictory",
        )

    if not parsed.services_requested:
        raise RouteToHuman(
            "No service could be identified in the request.", kind="incomplete"
        )

    if parsed.missing_information:
        raise RouteToHuman(
            "Cannot quote without: " + "; ".join(parsed.missing_information),
            kind="incomplete",
        )


def evaluate_confidence(
    parsed: JobRequestParsed, threshold: float, *, is_new_customer: bool
) -> ReviewFlags:
    flags = ReviewFlags()

    for field_name in parsed.field_confidence.below(threshold):
        value = getattr(parsed.field_confidence, field_name)
        flags.add(f"low confidence on {field_name} ({value:.2f} < {threshold:.2f})")

    if is_new_customer:
        flags.add("no matching customer on file — new account")

    if parsed.property_address is None:
        flags.add("no service address extracted")

    return flags


# ---------------------------------------------------------------------------
# 4. The approval gate
# ---------------------------------------------------------------------------


class ApprovalRequired(PermissionError):
    """Raised when something customer-facing is attempted without approval."""


def require_approval(quote: Quote, approval: Approval | None) -> Approval:
    """The only gate to a customer-facing send.

    Called by `send_quote` and by the approval endpoint. Every condition here
    has a test; see tests/test_guardrails.py::TestApprovalGate.
    """
    if approval is None:
        raise ApprovalRequired(
            f"Quote {quote.quote_number} has no approval record. Nothing may be sent."
        )

    if approval.quote_id != quote.id:
        raise ApprovalRequired(
            f"Approval {approval.id} belongs to a different quote. Refusing to send."
        )

    if approval.status is not ApprovalStatus.RESOLVED:
        raise ApprovalRequired(
            f"Approval {approval.id} is still {approval.status.value}. "
            f"A human has not decided yet."
        )

    if approval.action is not ApprovalAction.APPROVE:
        raise ApprovalRequired(
            f"Approval {approval.id} records a {approval.action.value if approval.action else 'null'} "
            f"decision, not an approval."
        )

    if quote.status is not QuoteStatus.APPROVED:
        raise ApprovalRequired(
            f"Quote {quote.quote_number} is {quote.status.value}, not approved. "
            f"Refusing to send."
        )

    return approval


# ---------------------------------------------------------------------------
# Draft verification
# ---------------------------------------------------------------------------

_MONEY = re.compile(r"\$\s?([0-9][0-9,]*(?:\.[0-9]{2})?)")


def verify_draft(
    body: str, subject: str, *, quote_number: str, allowed_amounts: set[str]
) -> list[str]:
    """Check a drafted email against the quote it describes.

    Programmatic, not model-graded: a figure in the email that does not appear
    in the quote is the failure mode that would cost Riverside real money, and
    it is cheap to detect exactly.
    """
    problems: list[str] = []

    if quote_number not in body and quote_number not in subject:
        problems.append(f"quote number {quote_number} does not appear in the draft")

    found = {m.group(1).replace(",", "") for m in _MONEY.finditer(body)}
    normalized_allowed = {a.replace(",", "").lstrip("$") for a in allowed_amounts}

    for amount in sorted(found):
        candidates = {amount, f"{amount}.00" if "." not in amount else amount.split(".")[0]}
        if not candidates & normalized_allowed:
            problems.append(f"draft contains ${amount}, which is not a figure from the quote")

    if not found:
        problems.append("draft contains no dollar figure at all")

    placeholders = ["[", "TODO", "XXX", "{{", "lorem ipsum"]
    for token in placeholders:
        if token.lower() in body.lower():
            problems.append(f"draft contains placeholder text {token!r}")

    if len(body.strip()) < 120:
        problems.append("draft is implausibly short for a customer-facing quote email")

    return problems
