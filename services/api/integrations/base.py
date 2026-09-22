"""Integration seams.

Riverside runs on a CRM, a scheduling calendar, an accounting package, and
email. None of those are wired up here — every one is behind a Protocol with a
mock implementation. The point of the design is that swapping any single mock
for the real thing is one file and no changes to the agent, the pricing engine,
or the API.

`MessagingAdapter` is the one that matters for the MVP, because it is the only
adapter that can do something irreversible. It is therefore the only one called
behind the approval gate.

Productionization notes for each adapter live in README.md under "What's mocked
and how you'd make it real".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class SendResult:
    provider_message_id: str
    sent_at: datetime
    adapter: str


@runtime_checkable
class MessagingAdapter(Protocol):
    """Outbound customer communication. The only irreversible adapter."""

    name: str

    def send_email(self, *, to: str, subject: str, body: str, reply_to: str | None = None) -> SendResult:
        """Deliver an email. Must only ever be reached through the approval gate."""
        ...


@runtime_checkable
class CRMAdapter(Protocol):
    """Customer records. Read-mostly for the MVP."""

    name: str

    def find_customer(self, *, name: str | None, email: str | None, address: str | None) -> dict[str, Any] | None: ...

    def upsert_customer(self, payload: dict[str, Any]) -> dict[str, Any]: ...


@runtime_checkable
class CalendarAdapter(Protocol):
    """Crew scheduling. Interface only — Phase 2 of the roadmap.

    Declared now so the approval flow has somewhere to hand an approved job
    without the service layer growing a scheduling-shaped hole later.
    """

    name: str

    def find_slots(self, *, duration_minutes: int, after: datetime) -> list[dict[str, Any]]: ...

    def book(self, *, slot_id: str, payload: dict[str, Any]) -> dict[str, Any]: ...


@runtime_checkable
class AccountingAdapter(Protocol):
    """Invoicing. Interface only — Phase 2 of the roadmap."""

    name: str

    def create_invoice(self, *, quote_id: str, payload: dict[str, Any]) -> dict[str, Any]: ...

    def get_invoice(self, *, invoice_id: str) -> dict[str, Any] | None: ...
