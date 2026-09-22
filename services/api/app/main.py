"""GroundControl API — the single backend.

The Next.js app is a pure client; all business logic, agent orchestration,
pricing, and eval reporting live behind this service.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)-8s %(name)s | %(message)s",
)

app = FastAPI(
    title="GroundControl API",
    description=(
        "Operations copilot for Riverside Grounds. Turns an inbound job-request email "
        "into a priced, human-approved quote. The LLM proposes; deterministic code prices."
    ),
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    """Liveness probe. Used by docker-compose healthchecks and the web client."""
    return {"status": "ok", "service": "groundcontrol-api", "model": settings.llm_model}
