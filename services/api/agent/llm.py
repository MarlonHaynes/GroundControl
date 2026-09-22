"""LLM access.

One narrow interface — "given a system prompt, a user prompt, and a Pydantic
model, return a validated instance of that model plus its token usage" — with
three implementations:

* `AnthropicClient`  — the real thing, via the official SDK's structured outputs.
* `CachingLLMClient` — wraps any client with a content-addressed disk cache, so
  re-running the eval while iterating on metrics costs nothing.
* `FakeLLMClient`    — returns scripted responses. Every test uses this, which
  is why `make test` is deterministic, offline, and free.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol, TypeVar

from pydantic import BaseModel

from app.config import settings
from observability.cost import Usage

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class LLMResult(BaseModel):
    """Envelope carrying the parsed object alongside what the call cost."""

    model_config = {"arbitrary_types_allowed": True}

    parsed: BaseModel
    usage: Usage
    model: str
    latency_ms: int
    cache_hit: bool = False


class LLMError(RuntimeError):
    """A call failed in a way the pipeline should treat as a step failure."""


class LLMClient(Protocol):
    def structured(
        self,
        *,
        system: str,
        prompt: str,
        schema: type[T],
        max_tokens: int | None = None,
        temperature_key: str = "",
    ) -> LLMResult: ...


def _cache_key(model: str, system: str, prompt: str, schema: type[BaseModel], extra: str) -> str:
    """Content address for a call. Schema shape is part of the key.

    If the schema changes, old cached responses are no longer valid answers to
    the new question, so they must miss rather than silently deserialize.
    """
    payload = json.dumps(
        {
            "model": model,
            "system": system,
            "prompt": prompt,
            "schema": schema.model_json_schema(),
            "extra": extra,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Real client
# ---------------------------------------------------------------------------


class AnthropicClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        effort: str | None = None,
        max_tokens: int | None = None,
        max_retries: int = 3,
    ) -> None:
        import anthropic

        key = api_key if api_key is not None else settings.anthropic_api_key
        if not key:
            raise LLMError(
                "No ANTHROPIC_API_KEY configured. Set it in .env — the agent "
                "pipeline, dataset generation, and eval runs all require it."
            )
        self._anthropic = anthropic
        self._client = anthropic.Anthropic(api_key=key, max_retries=max_retries)
        self.model = model or settings.llm_model
        self.effort = effort or settings.llm_effort
        self.max_tokens = max_tokens or settings.llm_max_tokens

    def structured(
        self,
        *,
        system: str,
        prompt: str,
        schema: type[T],
        max_tokens: int | None = None,
        temperature_key: str = "",
    ) -> LLMResult:
        started = time.perf_counter()
        try:
            response = self._client.messages.parse(
                model=self.model,
                max_tokens=max_tokens or self.max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                output_format=schema,
                output_config={"effort": self.effort},
            )
        except self._anthropic.APIStatusError as exc:
            raise LLMError(f"Anthropic API error {exc.status_code}: {exc.message}") from exc
        except self._anthropic.APIConnectionError as exc:
            raise LLMError(f"Could not reach the Anthropic API: {exc}") from exc

        latency_ms = int((time.perf_counter() - started) * 1000)

        if response.stop_reason == "refusal":
            raise LLMError(f"Model refused the request: {response.stop_details}")

        parsed = response.parsed_output
        if parsed is None:
            raise LLMError(
                f"Model returned no parseable output (stop_reason={response.stop_reason})."
            )

        usage = Usage(
            input_tokens=response.usage.input_tokens or 0,
            output_tokens=response.usage.output_tokens or 0,
            cache_read_tokens=getattr(response.usage, "cache_read_input_tokens", 0) or 0,
        )
        return LLMResult(
            parsed=parsed,
            usage=usage,
            model=self.model,
            latency_ms=latency_ms,
        )


# ---------------------------------------------------------------------------
# Disk cache
# ---------------------------------------------------------------------------


class CachingLLMClient:
    """Content-addressed disk cache around another client.

    A cache hit reports zero usage and zero cost, which is honest: that call did
    not happen. Eval runs report cache-hit counts alongside spend so a $0.00
    result is never mistaken for a free model.
    """

    def __init__(self, inner: LLMClient, cache_dir: Path | None = None) -> None:
        self.inner = inner
        self.cache_dir = cache_dir or settings.cache_path
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.hits = 0
        self.misses = 0

    @property
    def model(self) -> str:
        return getattr(self.inner, "model", settings.llm_model)

    def structured(
        self,
        *,
        system: str,
        prompt: str,
        schema: type[T],
        max_tokens: int | None = None,
        temperature_key: str = "",
    ) -> LLMResult:
        key = _cache_key(self.model, system, prompt, schema, temperature_key)
        path = self.cache_dir / f"{key}.json"

        if path.exists():
            try:
                blob = json.loads(path.read_text(encoding="utf-8"))
                parsed = schema.model_validate(blob["parsed"])
                self.hits += 1
                return LLMResult(
                    parsed=parsed,
                    usage=Usage(),  # a cache hit costs nothing; say so
                    model=blob.get("model", self.model),
                    latency_ms=0,
                    cache_hit=True,
                )
            except Exception:
                logger.warning("Discarding unreadable cache entry %s", path.name)
                path.unlink(missing_ok=True)

        result = self.inner.structured(
            system=system,
            prompt=prompt,
            schema=schema,
            max_tokens=max_tokens,
            temperature_key=temperature_key,
        )
        self.misses += 1
        path.write_text(
            json.dumps(
                {
                    "model": result.model,
                    "parsed": result.parsed.model_dump(mode="json"),
                    "usage": {
                        "input_tokens": result.usage.input_tokens,
                        "output_tokens": result.usage.output_tokens,
                    },
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return result


# ---------------------------------------------------------------------------
# Fake client, for tests
# ---------------------------------------------------------------------------


class FakeLLMClient:
    """Scripted responses keyed by schema name.

    Handlers receive the rendered prompt so a test can vary the response by
    input. Every call is recorded for assertions.
    """

    def __init__(
        self,
        responses: dict[str, BaseModel | Callable[[str], BaseModel]] | None = None,
        *,
        model: str = "claude-sonnet-5",
        usage: Usage | None = None,
    ) -> None:
        self.responses = responses or {}
        self.model = model
        self.usage = usage or Usage(input_tokens=1200, output_tokens=400)
        self.calls: list[dict[str, str]] = []

    def set(self, schema: type[BaseModel], response: BaseModel | Callable[[str], BaseModel]) -> None:
        self.responses[schema.__name__] = response

    def structured(
        self,
        *,
        system: str,
        prompt: str,
        schema: type[T],
        max_tokens: int | None = None,
        temperature_key: str = "",
    ) -> LLMResult:
        self.calls.append({"schema": schema.__name__, "system": system, "prompt": prompt})

        try:
            scripted = self.responses[schema.__name__]
        except KeyError:
            raise LLMError(
                f"FakeLLMClient has no scripted response for {schema.__name__}. "
                f"Scripted: {sorted(self.responses)}"
            ) from None

        value = scripted(prompt) if callable(scripted) else scripted
        if not isinstance(value, schema):
            raise LLMError(
                f"Scripted response for {schema.__name__} is a {type(value).__name__}."
            )
        return LLMResult(parsed=value, usage=self.usage, model=self.model, latency_ms=7)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_llm_client(*, use_cache: bool | None = None) -> LLMClient:
    """The client the pipeline uses in production and in eval runs."""
    client: LLMClient = AnthropicClient()
    enabled = settings.llm_cache_enabled if use_cache is None else use_cache
    if enabled:
        client = CachingLLMClient(client)
    return client
