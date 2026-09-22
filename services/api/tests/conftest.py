"""Shared test fixtures.

Database tests run inside a transaction that is rolled back afterwards, so the
suite never leaves rows behind and never depends on ordering. No test in this
suite makes a network call: the LLM is always the fake.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from db.session import SessionLocal, engine


def _database_available() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


DB_AVAILABLE = _database_available()

requires_db = pytest.mark.skipif(
    not DB_AVAILABLE,
    reason="Postgres is not reachable. Run inside the compose stack: make test",
)


@pytest.fixture
def db() -> Iterator[Session]:
    """A session bound to a transaction that is rolled back after the test."""
    connection = engine.connect()
    transaction = connection.begin()
    session = SessionLocal(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()
