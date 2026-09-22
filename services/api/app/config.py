"""Central configuration. Every tunable lives here; nothing reads os.environ directly."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# services/api/app/config.py -> services/api
API_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = API_ROOT.parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", API_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- LLM ---------------------------------------------------------------
    anthropic_api_key: str = ""
    llm_model: str = "claude-sonnet-5"
    llm_effort: str = "medium"
    llm_max_tokens: int = 8000
    llm_cache_enabled: bool = True
    llm_cache_dir: str = ".llm_cache"

    # --- Database ----------------------------------------------------------
    database_url: str = (
        "postgresql+psycopg://groundcontrol:groundcontrol@localhost:5432/groundcontrol"
    )

    # --- API ---------------------------------------------------------------
    api_port: int = 8000
    api_cors_origins: str = "http://localhost:3000"
    log_level: str = "INFO"

    # --- Pricing guardrails ------------------------------------------------
    quote_sanity_ceiling_cents: int = 5_000_000

    # --- Matching / confidence thresholds ----------------------------------
    customer_match_confident: float = 0.88
    customer_match_review: float = 0.65
    field_confidence_threshold: float = 0.70

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.api_cors_origins.split(",") if o.strip()]

    @property
    def cache_path(self) -> Path:
        p = Path(self.llm_cache_dir)
        return p if p.is_absolute() else API_ROOT / p


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
