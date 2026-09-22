"""Field-level extraction accuracy.

Scored per field rather than as a single blob, because "94% accurate" hides
which field is failing and a per-field number tells you which prompt line to
change.

Matching is normalized, not exact: an address written "34 Wampanoag Dr, West
Hartford CT" should count as matching "34 Wampanoag Drive, West Hartford, CT
06117". Comparing raw strings would measure formatting rather than extraction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from agent.schemas import JobRequestParsed
from evals.dataset import GroundTruth

# How close a numeric size has to be to count. Customers round; so may we.
SIZE_TOLERANCE = 0.15

_STREET_ABBREV = {
    "street": "st", "road": "rd", "drive": "dr", "avenue": "ave", "lane": "ln",
    "terrace": "ter", "court": "ct", "place": "pl", "boulevard": "blvd",
    "highway": "hwy", "turnpike": "tpke", "circle": "cir", "way": "way",
}

FIELDS = (
    "service_types",
    "property_size_sqft",
    "property_address",
    "special_requests",
    "urgency",
)


def _norm_text(value: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", value.lower()).strip()


def _norm_address(value: str | None) -> str:
    if not value:
        return ""
    text = _norm_text(value)
    text = re.sub(r"\bct\b|\bconnecticut\b", "", text)
    text = re.sub(r"\b\d{5}(?:-\d{4})?\b", "", text)  # postal codes
    words = [_STREET_ABBREV.get(w, w) for w in text.split()]
    return " ".join(words).strip()


@dataclass
class FieldScore:
    correct: int = 0
    total: int = 0

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 0.0

    def add(self, ok: bool) -> None:
        self.total += 1
        self.correct += int(ok)


@dataclass
class ExtractionScorer:
    fields: dict[str, FieldScore] = field(
        default_factory=lambda: {name: FieldScore() for name in FIELDS}
    )

    def score_case(
        self, parsed: JobRequestParsed | None, gt: GroundTruth
    ) -> dict[str, bool | None]:
        """Score one case. Returns per-field pass/fail, None where not applicable."""
        result: dict[str, bool | None] = {}

        # --- service types -------------------------------------------------
        # Scored as a set: the model must identify the right services, and the
        # order it lists them in is not a fact about the email.
        expected_codes = gt.expected_catalog_codes
        if expected_codes:
            got = {s.catalog_code.strip().upper() for s in (parsed.services_requested if parsed else [])}
            ok = got == expected_codes
            result["service_types"] = ok
            self.fields["service_types"].add(ok)
        else:
            result["service_types"] = None

        # --- size ----------------------------------------------------------
        if gt.property_size_sqft is not None:
            got_size = parsed.property_size_sqft if parsed else None
            if got_size is None:
                ok = False
            else:
                expected = gt.property_size_sqft
                ok = abs(got_size - expected) <= expected * SIZE_TOLERANCE
            result["property_size_sqft"] = ok
            self.fields["property_size_sqft"].add(ok)
        else:
            # Ground truth has no size; the correct answer is null. Claiming a
            # number here is a hallucination and is scored as one.
            if parsed is not None:
                ok = parsed.property_size_sqft is None
                result["property_size_sqft"] = ok
                self.fields["property_size_sqft"].add(ok)
            else:
                result["property_size_sqft"] = None

        # --- address ---------------------------------------------------------
        if gt.property_address:
            expected_addr = _norm_address(gt.property_address)
            got_addr = _norm_address(parsed.property_address if parsed else None)
            # Street number plus street name is the identifying part; the town
            # and state are usually redundant and inconsistently written.
            ok = bool(got_addr) and (
                got_addr in expected_addr
                or expected_addr in got_addr
                or _street_key(got_addr) == _street_key(expected_addr)
            )
            result["property_address"] = ok
            self.fields["property_address"].add(ok)
        else:
            result["property_address"] = None

        # --- special requests -------------------------------------------------
        # Recall-based: did each ground-truth request survive extraction? The
        # model is allowed to pick up extras the generator did not label.
        if gt.special_requests:
            got_blob = _norm_text(" ".join(parsed.special_requests)) if parsed else ""
            hits = sum(1 for s in gt.special_requests if _overlaps(_norm_text(s), got_blob))
            ok = hits == len(gt.special_requests)
            result["special_requests"] = ok
            self.fields["special_requests"].add(ok)
        else:
            result["special_requests"] = None

        # --- urgency ----------------------------------------------------------
        got_urgency = parsed.urgency if parsed else None
        ok = got_urgency == gt.urgency
        result["urgency"] = ok
        self.fields["urgency"].add(ok)

        return result

    def summary(self) -> dict[str, object]:
        per_field = {name: score.accuracy for name, score in self.fields.items()}
        scored = [s for s in self.fields.values() if s.total]
        total_correct = sum(s.correct for s in scored)
        total = sum(s.total for s in scored)
        return {
            "aggregate": (total_correct / total) if total else 0.0,
            "fields": per_field,
            "counts": {name: {"correct": s.correct, "total": s.total} for name, s in self.fields.items()},
        }


def _street_key(address: str) -> str:
    """Leading house number plus the next two words: '34 wampanoag dr'."""
    parts = address.split()
    return " ".join(parts[:3]) if parts else ""


def _overlaps(expected: str, got_blob: str) -> bool:
    """Loose containment: enough content words in common to be the same request."""
    if not expected or not got_blob:
        return False
    if expected in got_blob:
        return True
    words = [w for w in expected.split() if len(w) > 3]
    if not words:
        return False
    hits = sum(1 for w in words if w in got_blob)
    return hits / len(words) >= 0.6
