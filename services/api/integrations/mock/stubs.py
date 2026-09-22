"""Interface-only mocks for the adapters the MVP does not use.

These exist so the seams are visible and the Phase 2 roadmap has somewhere to
land. They raise `NotImplementedError` rather than returning plausible fake
data: a stub that silently returns `[]` is how a half-built integration ships
to production by accident.

`MockCRMAdapter` is the exception — customer lookup is a real MVP path, and it
is backed by the database rather than by a third-party CRM.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any


class MockCalendarAdapter:
    """Crew scheduling — roadmap Phase 2.1."""

    name = "mock-calendar"

    def find_slots(self, *, duration_minutes: int, after: datetime) -> list[dict[str, Any]]:
        raise NotImplementedError(
            "Crew scheduling is not part of the MVP. The interface is declared so "
            "the approval flow has a defined place to hand off an approved job."
        )

    def book(self, *, slot_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError("Crew scheduling is not part of the MVP.")


class MockAccountingAdapter:
    """Invoicing — roadmap Phase 2.2."""

    name = "mock-accounting"

    def create_invoice(self, *, quote_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError(
            "Invoicing is not part of the MVP. Declared for the quote-vs-invoice "
            "anomaly detection phase."
        )

    def get_invoice(self, *, invoice_id: str) -> dict[str, Any] | None:
        raise NotImplementedError("Invoicing is not part of the MVP.")
