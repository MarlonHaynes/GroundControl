"""parse_job_request — LLM, strict schema, grounded contact details."""

from __future__ import annotations

from agent.llm import LLMClient
from agent.prompts import render_prompt
from agent.schemas import JobRequestParsed
from agent.tools.catalog import render_catalog
from observability.tracer import StepHandle


def parse_job_request(
    *,
    client: LLMClient,
    email_text: str,
    subject: str | None = None,
    sender_email: str | None = None,
    step: StepHandle | None = None,
) -> JobRequestParsed:
    system = render_prompt("parse_job_request", catalog=render_catalog())

    header = []
    if sender_email:
        header.append(f"From: {sender_email}")
    if subject:
        header.append(f"Subject: {subject}")
    prompt = (
        "Extract the job request from this email.\n\n"
        "--- BEGIN EMAIL ---\n"
        + ("\n".join(header) + "\n\n" if header else "")
        + email_text
        + "\n--- END EMAIL ---"
    )

    result = client.structured(system=system, prompt=prompt, schema=JobRequestParsed)
    parsed: JobRequestParsed = result.parsed  # type: ignore[assignment]

    if step is not None:
        step.record_usage(result.usage, cache_hit=result.cache_hit)
        step.set_output(parsed.model_dump(mode="json"))

    return parsed
