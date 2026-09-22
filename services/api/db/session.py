"""Engine and session factory. Sync SQLAlchemy 2.0 on psycopg3.

Deliberately sync: FastAPI runs `def` endpoints in a threadpool, which keeps
the seed scripts, Alembic, the test suite, and the eval harness free of an
async entrypoint. The eval harness gets its parallelism from a thread pool
instead. LLM calls are the only meaningful latency and they are already
isolated behind the tracer.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """FastAPI dependency."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope for scripts, the agent loop, and the eval harness."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
