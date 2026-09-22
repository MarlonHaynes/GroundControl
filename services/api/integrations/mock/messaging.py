"""Mock messaging adapter.

"Sends" by logging and returning a provider message id. The caller
(`agent.tools.send.send_quote`) is what writes the `SentMessage` row, so the
audit trail is identical to what a real provider would produce — only the
delivery is simulated.

To make this real: implement `send_email` against Postmark, SendGrid, or
Resend, return the provider's message id, and register it in
`integrations/registry.py`. Nothing else in the codebase changes. The approval
gate sits above this layer, so a real adapter inherits the same protection.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from integrations.base import SendResult

logger = logging.getLogger(__name__)


class MockMessagingAdapter:
    name = "mock"

    def __init__(self) -> None:
        # Kept in memory so tests can assert on what was "sent" without
        # reaching for the database.
        self.outbox: list[dict[str, str]] = []

    def send_email(
        self, *, to: str, subject: str, body: str, reply_to: str | None = None
    ) -> SendResult:
        message_id = f"mock-{uuid.uuid4().hex[:16]}"
        self.outbox.append(
            {"to": to, "subject": subject, "body": body, "message_id": message_id}
        )
        logger.info(
            "MOCK SEND -> %s | %s | %d chars | id=%s", to, subject, len(body), message_id
        )
        return SendResult(
            provider_message_id=message_id, sent_at=datetime.now(UTC), adapter=self.name
        )
