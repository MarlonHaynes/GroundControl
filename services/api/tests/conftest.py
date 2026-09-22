"""Shared test fixtures.

The suite runs against its own database, created on demand, rather than the
one `make seed` populates. That isolation is not a nicety: the seeded demo
data contains a customer named Amara Osei and all 18 catalog codes, so tests
that create their own fixtures would hit unique-constraint violations and
fuzzy-match near-ties depending on whether someone had run `make seed` first.
A test suite whose result depends on that is not a test suite.

`DATABASE_URL` is redirected before any application module is imported, so
`app.config.settings` — which is built once at import time — picks up the test
database rather than the demo one.

Every database test then runs inside a transaction that is rolled back, so the
suite leaves no rows behind and does not depend on ordering. No test in this
suite makes a network call: the LLM is always the fake.
"""

from __future__ import annotations

import os

# --- must happen before any app import -------------------------------------

DEFAULT_URL = "postgresql+psycopg://groundcontrol:groundcontrol@db:5432/groundcontrol"


def _test_database_url() -> str:
    url = os.environ.get("DATABASE_URL", DEFAULT_URL).rstrip("/")
    return url if url.endswith("_test") else f"{url}_test"


os.environ["DATABASE_URL"] = _test_database_url()

# ---------------------------------------------------------------------------

from collections.abc import Iterator  # noqa: E402

import db.models  # noqa: E402,F401  (registers every table on Base.metadata)
import pytest  # noqa: E402
from db.base import Base  # noqa: E402
from db.session import SessionLocal, engine  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402


def _bootstrap_database() -> bool:
    """Create the test database and its schema. Returns False if unreachable."""
    url = make_url(os.environ["DATABASE_URL"])
    admin_url = url.set(database="postgres")

    try:
        admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with admin.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": url.database},
            ).scalar()
            if not exists:
                # Identifier cannot be parameterized; the name is derived from
                # our own configured URL, not from user input.
                conn.execute(text(f'CREATE DATABASE "{url.database}"'))
        admin.dispose()
    except Exception:
        return False

    try:
        # The schema comes from the same models Alembic generates from, so the
        # tests and the migration cannot drift apart silently.
        Base.metadata.create_all(engine)
        return True
    except Exception:
        return False


DB_AVAILABLE = _bootstrap_database()

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
