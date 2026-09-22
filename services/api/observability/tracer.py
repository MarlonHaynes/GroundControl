"""Run and step tracing.

A lightweight in-house tracer rather than Langfuse. The tradeoff was
deliberate: Langfuse would add a service to the compose stack and put the
traces behind a second UI, when the thing a reviewer actually wants is to click
a run in *this* app and see what the agent did and what it cost. Traces live in
Postgres next to the quotes they produced.

The tracer is usable without a database session (`Tracer(db=None)`), which is
what the eval harness uses when it wants timings and cost without writing rows.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from db.models import AgentRun, RunStatus, StepStatus, TraceStep
from observability.cost import Usage, cost_microcents

logger = logging.getLogger(__name__)

MAX_FIELD_CHARS = 20_000


def _truncate(value: Any) -> Any:
    """Keep a single pathological payload from bloating the traces table."""
    if isinstance(value, str) and len(value) > MAX_FIELD_CHARS:
        return value[:MAX_FIELD_CHARS] + f"... [truncated {len(value) - MAX_FIELD_CHARS} chars]"
    if isinstance(value, dict):
        return {k: _truncate(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_truncate(v) for v in value]
    return value


@dataclass
class StepRecord:
    seq: int
    tool_name: str
    uses_llm: bool = False
    status: StepStatus = StepStatus.OK
    input: dict[str, Any] = field(default_factory=dict)
    output: dict[str, Any] = field(default_factory=dict)
    usage: Usage = field(default_factory=Usage)
    cost_microcents: int = 0
    latency_ms: int = 0
    cache_hit: bool = False
    error: str | None = None


@dataclass
class RunRecord:
    run_id: uuid.UUID
    model: str
    started_at: datetime
    steps: list[StepRecord] = field(default_factory=list)
    status: RunStatus = RunStatus.RUNNING
    outcome: str | None = None
    error: str | None = None
    ended_at: datetime | None = None

    @property
    def usage(self) -> Usage:
        total = Usage()
        for s in self.steps:
            total = total + s.usage
        return total

    @property
    def cost_microcents(self) -> int:
        return sum(s.cost_microcents for s in self.steps)

    @property
    def latency_ms(self) -> int:
        if self.ended_at is None:
            return 0
        return int((self.ended_at - self.started_at).total_seconds() * 1000)


class StepHandle:
    """Handed to a tool so it can attach usage and output to its own step."""

    def __init__(self, record: StepRecord, model: str) -> None:
        self._record = record
        self._model = model

    def set_output(self, output: dict[str, Any]) -> None:
        self._record.output = _truncate(output)

    def record_usage(self, usage: Usage, *, cache_hit: bool = False) -> None:
        self._record.usage = self._record.usage + usage
        self._record.cost_microcents += cost_microcents(self._model, usage)
        self._record.cache_hit = cache_hit
        self._record.uses_llm = True


class Tracer:
    def __init__(self, db: Session | None, *, model: str, job_request_id: uuid.UUID | None = None):
        self.db = db
        self.model = model
        self.job_request_id = job_request_id
        self.run = RunRecord(
            run_id=uuid.uuid4(), model=model, started_at=datetime.now(UTC)
        )
        self._db_run: AgentRun | None = None
        self._seq = 0

    # --- lifecycle ---------------------------------------------------------

    def start(self) -> RunRecord:
        if self.db is not None:
            self._db_run = AgentRun(
                id=self.run.run_id,
                job_request_id=self.job_request_id,
                status=RunStatus.RUNNING,
                model=self.model,
                started_at=self.run.started_at,
            )
            self.db.add(self._db_run)
            self.db.flush()
        return self.run

    def finish(self, status: RunStatus, *, outcome: str | None = None, error: str | None = None) -> RunRecord:
        self.run.status = status
        self.run.outcome = outcome
        self.run.error = error
        self.run.ended_at = datetime.now(UTC)

        if self._db_run is not None:
            u = self.run.usage
            self._db_run.status = status
            self._db_run.outcome = outcome
            self._db_run.error = error
            self._db_run.ended_at = self.run.ended_at
            self._db_run.latency_ms = self.run.latency_ms
            self._db_run.total_tokens_in = u.input_tokens
            self._db_run.total_tokens_out = u.output_tokens
            self._db_run.total_cached_tokens = u.cache_read_tokens
            self._db_run.total_cost_microcents = self.run.cost_microcents
            self.db.flush()  # type: ignore[union-attr]

        logger.info(
            "run %s %s | %d steps | %d in / %d out tok | %d microcents | %d ms",
            self.run.run_id,
            status.value,
            len(self.run.steps),
            self.run.usage.input_tokens,
            self.run.usage.output_tokens,
            self.run.cost_microcents,
            self.run.latency_ms,
        )
        return self.run

    # --- steps -------------------------------------------------------------

    @contextmanager
    def step(self, tool_name: str, payload: dict[str, Any] | None = None) -> Iterator[StepHandle]:
        self._seq += 1
        record = StepRecord(
            seq=self._seq, tool_name=tool_name, input=_truncate(payload or {})
        )
        self.run.steps.append(record)
        handle = StepHandle(record, self.model)
        started = time.perf_counter()
        try:
            yield handle
        except Exception as exc:
            record.status = StepStatus.ERROR
            record.error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            record.latency_ms = int((time.perf_counter() - started) * 1000)
            self._persist_step(record)

    def _persist_step(self, record: StepRecord) -> None:
        if self.db is None:
            return
        self.db.add(
            TraceStep(
                run_id=self.run.run_id,
                seq=record.seq,
                tool_name=record.tool_name,
                uses_llm=record.uses_llm,
                status=record.status,
                input=record.input,
                output=record.output,
                tokens_in=record.usage.input_tokens,
                tokens_out=record.usage.output_tokens,
                cached_tokens=record.usage.cache_read_tokens,
                cost_microcents=record.cost_microcents,
                latency_ms=record.latency_ms,
                cache_hit=record.cache_hit,
                error=record.error,
            )
        )
        self.db.flush()
