"""The one place adapters are chosen.

Swapping a mock for a real implementation is an edit to this file and nothing
else. That is the whole claim the architecture makes about its integration
seams, so it is kept small enough to verify at a glance.
"""

from __future__ import annotations

from functools import lru_cache

from integrations.base import AccountingAdapter, CalendarAdapter, MessagingAdapter
from integrations.mock.messaging import MockMessagingAdapter
from integrations.mock.stubs import MockAccountingAdapter, MockCalendarAdapter


@lru_cache
def get_messaging_adapter() -> MessagingAdapter:
    # To go live: return PostmarkMessagingAdapter(settings.postmark_token)
    return MockMessagingAdapter()


@lru_cache
def get_calendar_adapter() -> CalendarAdapter:
    return MockCalendarAdapter()


@lru_cache
def get_accounting_adapter() -> AccountingAdapter:
    return MockAccountingAdapter()


def reset_adapters() -> None:
    """Clear cached adapters. Used by tests that assert on a fresh outbox."""
    get_messaging_adapter.cache_clear()
    get_calendar_adapter.cache_clear()
    get_accounting_adapter.cache_clear()
