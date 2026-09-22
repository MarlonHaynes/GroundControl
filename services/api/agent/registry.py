"""The agent's tool registry.

This module is the answer to "what can the agent do?", and it is short on
purpose. Read the `TOOLS` list: there is no tool that sends anything, and no
tool that resolves an approval. `agent.tools.send` is not imported here, and a
test asserts that it never becomes so.

Registering a tool does not grant the model the ability to call it at will —
the orchestrator in `loop.py` decides the order. The registry exists so the set
of capabilities is enumerable, testable, and visible to a reviewer in one
screen.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    uses_llm: bool
    terminal: bool = False
    mutates_db: bool = False


TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="parse_job_request",
        description="Extract a structured job request from raw email text.",
        uses_llm=True,
    ),
    ToolSpec(
        name="lookup_customer",
        description="Fuzzy-match the sender against the customer list, or flag them as new.",
        uses_llm=False,
    ),
    ToolSpec(
        name="get_service_catalog",
        description="Return the service catalog and active pricing rules.",
        uses_llm=False,
    ),
    ToolSpec(
        name="propose_line_items",
        description="Propose catalog services and quantities. Cannot express a price.",
        uses_llm=True,
    ),
    ToolSpec(
        name="compute_quote",
        description="Price a proposal deterministically. No LLM involvement.",
        uses_llm=False,
    ),
    ToolSpec(
        name="draft_customer_email",
        description="Draft the customer-facing quote email from the computed quote.",
        uses_llm=True,
    ),
    ToolSpec(
        name="submit_for_approval",
        description="Persist the quote and open a pending approval. Terminal.",
        uses_llm=False,
        terminal=True,
        mutates_db=True,
    ),
    ToolSpec(
        name="route_to_human",
        description="Stop with a reason instead of quoting. Terminal.",
        uses_llm=False,
        terminal=True,
        mutates_db=True,
    ),
)

TOOL_NAMES = frozenset(t.name for t in TOOLS)

# Names that must never appear in the registry. Kept explicit so the intent
# survives a future refactor that adds tools without reading this file.
FORBIDDEN_TOOL_NAMES = frozenset(
    {"send_quote", "send_email", "approve_quote", "resolve_approval", "set_price"}
)


def terminal_tools() -> frozenset[str]:
    return frozenset(t.name for t in TOOLS if t.terminal)
