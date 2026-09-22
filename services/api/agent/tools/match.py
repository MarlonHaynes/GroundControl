"""Customer matching. Deterministic, no LLM.

Fuzzy name matching is a solved problem and an LLM is the wrong tool for it:
rapidfuzz is faster, free, reproducible, and — most importantly — its score is
a number we can threshold and test. "Is this the same customer?" is a question
where a confident wrong answer costs Riverside a misdirected quote.

Scoring blends three signals, weighted by how much each one proves:

* **email** — an exact match is near-conclusive, so it dominates.
* **name**  — fuzzy, using token-set ratio so "Hillcrest Properties LLC" and
  "LLC Hillcrest Properties" score alike while still separating the two real
  Hillcrest accounts.
* **address** — line1 plus town, which disambiguates the Feeney siblings on the
  same street.
"""

from __future__ import annotations

from dataclasses import dataclass

from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from db.models import Address, Customer

W_EMAIL = 0.50
W_NAME = 0.32
W_ADDRESS = 0.18


@dataclass
class MatchCandidate:
    customer_id: str
    customer_name: str
    score: float
    email_score: float
    name_score: float
    address_score: float


@dataclass
class MatchResult:
    """Either a linked customer or an explicit new-customer flag."""

    customer: Customer | None
    score: float
    is_new: bool
    needs_review: bool
    reason: str
    candidates: list[MatchCandidate]

    @property
    def customer_id(self) -> str | None:
        return str(self.customer.id) if self.customer else None


def _norm(value: str | None) -> str:
    return (value or "").strip().lower()


def _score_email(query: str, candidate: str) -> float:
    if not query or not candidate:
        return 0.0
    if query == candidate:
        return 1.0
    # Same domain is weak evidence on its own, and actively misleading for
    # gmail.com, so only company domains count.
    q_domain = query.split("@")[-1]
    c_domain = candidate.split("@")[-1]
    free = {"gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "icloud.com",
            "aol.com", "comcast.net", "verizon.net", "snet.net", "sbcglobal.net",
            "protonmail.com", "mac.com"}
    if q_domain == c_domain and q_domain not in free:
        return 0.55
    return 0.0


def _score_name(query: str, candidate: str) -> float:
    if not query or not candidate:
        return 0.0
    return max(
        fuzz.token_set_ratio(query, candidate),
        fuzz.partial_ratio(query, candidate) * 0.92,
    ) / 100.0


def _score_address(query: str, candidate: str) -> float:
    if not query or not candidate:
        return 0.0
    return fuzz.token_set_ratio(query, candidate) / 100.0


def lookup_customer(
    db: Session,
    *,
    name: str | None,
    email: str | None,
    address: str | None,
    confident_threshold: float | None = None,
    review_threshold: float | None = None,
) -> MatchResult:
    confident = confident_threshold if confident_threshold is not None else settings.customer_match_confident
    review = review_threshold if review_threshold is not None else settings.customer_match_review

    q_name, q_email, q_address = _norm(name), _norm(email), _norm(address)

    if not (q_name or q_email or q_address):
        return MatchResult(
            customer=None,
            score=0.0,
            is_new=True,
            needs_review=True,
            reason="No name, email, or address to match on.",
            candidates=[],
        )

    customers = db.execute(select(Customer)).scalars().all()
    candidates: list[MatchCandidate] = []

    for c in customers:
        email_s = _score_email(q_email, _norm(c.email))
        # Match against both the account name and the contact's name: mail from
        # "Dana Whitfield" should find "Hillcrest Property Management".
        name_s = max(_score_name(q_name, _norm(c.name)), _score_name(q_name, _norm(c.contact_name)))
        addr_s = 0.0
        for a in c.addresses:
            joined = f"{a.line1} {a.city or ''} {a.postal_code or ''}"
            addr_s = max(addr_s, _score_address(q_address, _norm(joined)))

        # Only score the signals we actually have, then renormalize, so a
        # request with no address is not penalized for lacking one.
        parts = [(W_EMAIL, email_s, bool(q_email)), (W_NAME, name_s, bool(q_name)),
                 (W_ADDRESS, addr_s, bool(q_address))]
        available = sum(w for w, _, present in parts if present)
        score = sum(w * s for w, s, present in parts if present) / available if available else 0.0

        candidates.append(
            MatchCandidate(
                customer_id=str(c.id),
                customer_name=c.name,
                score=round(score, 4),
                email_score=round(email_s, 4),
                name_score=round(name_s, 4),
                address_score=round(addr_s, 4),
            )
        )

    candidates.sort(key=lambda c: c.score, reverse=True)
    top = candidates[0] if candidates else None
    top_customers = {str(c.id): c for c in customers}

    if top is None or top.score < review:
        return MatchResult(
            customer=None,
            score=top.score if top else 0.0,
            is_new=True,
            needs_review=True,
            reason=(
                f"Best match scored {top.score:.2f}, below the {review:.2f} threshold."
                if top
                else "No customers on file."
            ),
            candidates=candidates[:5],
        )

    matched = top_customers[top.customer_id]

    if top.score >= confident:
        # A near-tie against a different customer is the near-duplicate case:
        # link the best match, but make a human confirm it.
        runner_up = candidates[1] if len(candidates) > 1 else None
        if runner_up and (top.score - runner_up.score) < 0.05:
            return MatchResult(
                customer=matched,
                score=top.score,
                is_new=False,
                needs_review=True,
                reason=(
                    f"Matched {matched.name} at {top.score:.2f}, but "
                    f"{runner_up.customer_name} scored {runner_up.score:.2f} — "
                    f"too close to call automatically."
                ),
                candidates=candidates[:5],
            )
        return MatchResult(
            customer=matched,
            score=top.score,
            is_new=False,
            needs_review=False,
            reason=f"Matched {matched.name} at {top.score:.2f}.",
            candidates=candidates[:5],
        )

    return MatchResult(
        customer=matched,
        score=top.score,
        is_new=False,
        needs_review=True,
        reason=(
            f"Probable match to {matched.name} at {top.score:.2f}, below the "
            f"{confident:.2f} auto-link threshold. Confirm before sending."
        ),
        candidates=candidates[:5],
    )
