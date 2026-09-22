"""Prompt loading.

Prompts live in .md files rather than inline string literals so they can be
diffed, reviewed, and blamed like the rest of the code. When an eval run moves
a metric, `git log agent/prompts/` says why.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent


@lru_cache
def load_prompt(name: str) -> str:
    path = PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        available = sorted(p.stem for p in PROMPTS_DIR.glob("*.md"))
        raise FileNotFoundError(f"No prompt {name!r}. Available: {available}")
    return path.read_text(encoding="utf-8")


def render_prompt(name: str, **kwargs: str) -> str:
    """Load a prompt and substitute {placeholders}.

    Uses str.replace rather than str.format because the prompts contain literal
    braces (JSON examples, currency formatting) that format() would choke on.
    """
    text = load_prompt(name)
    for key, value in kwargs.items():
        text = text.replace("{" + key + "}", value)
    return text
