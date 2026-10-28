"""Application configuration.

Settings are resolved lazily via :func:`get_settings` and validated the first
time they are requested - never at import time. That keeps the package
importable (for tests, linting and a fresh clone) without a populated ``.env``.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, loaded from the environment or ``.env``."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- credentials -----------------------------------------------------
    github_token: SecretStr = Field(
        description="GitHub token used to read pull requests and publish reviews.",
    )
    groq_api_key: SecretStr = Field(
        description="Groq API key used for all LLM calls.",
    )

    # --- model -----------------------------------------------------------
    groq_model: str = Field(
        default="openai/gpt-oss-120b",
        description="Groq-hosted model identifier used by every reviewer agent.",
    )
    groq_temperature: float = Field(
        default=0.2,
        ge=0.0,
        le=2.0,
        description="Sampling temperature. Low values keep reviews deterministic.",
    )

    groq_model_fast: str | None = Field(
        default=None,
        description=(
            "Model for agents with model_tier 'fast' in agents.yaml (e.g. style). "
            "Unset means every agent uses GROQ_MODEL. Check JSON-mode support first."
        ),
    )

    # --- LLM resilience ----------------------------------------------------
    llm_timeout_seconds: float = Field(default=45.0, gt=0, description="Per-call LLM timeout.")
    llm_max_attempts: int = Field(
        default=3, ge=1, le=6, description="Attempts per LLM call on 429/5xx/timeouts."
    )
    llm_structured_output: bool = Field(
        default=True, description="Ask the model for schema-constrained output first."
    )
    llm_tolerant_fallback: bool = Field(
        default=True,
        description=(
            "When structured output fails, retry once as plain text and parse it "
            "tolerantly (fences, prose-wrapped JSON). Disable to fail fast instead."
        ),
    )
    max_concurrent_llm_calls: int = Field(
        default=5, ge=1, le=64, description="Upper bound on parallel reviewer calls."
    )

    # --- review shaping (Phase 2) -----------------------------------------
    max_patch_chars: int = Field(
        default=40_000, ge=1_000, description="Files whose diff is larger are skipped."
    )
    max_review_prompt_tokens: int = Field(
        default=400_000,
        ge=1_000,
        description=(
            "Estimated prompt-token budget for one review (all files x all agents). "
            "Beyond it, lower-risk files are skipped and the review is marked partial."
        ),
    )
    max_findings_per_file: int = Field(default=10, ge=1, description="Cap after dedup, per file.")
    max_findings_per_review: int = Field(default=50, ge=1, description="Cap after dedup, total.")
    dedup_similarity_threshold: int = Field(
        default=70, ge=0, le=100, description="rapidfuzz score (0-100) for two messages to match."
    )
    dedup_line_window: int = Field(
        default=3, ge=0, description="Findings this many lines apart may be duplicates."
    )

    # --- publishing and merging -------------------------------------------
    suggestion_min_severity: Literal["LOW", "MEDIUM", "HIGH"] = Field(
        default="MEDIUM",
        description="Default lowest severity posted to the pull request as inline comments.",
    )
    merge_enabled: bool = Field(
        default=False,
        description=(
            "Allow the API to merge pull requests. Off by default: the API has no "
            "authentication yet (Phase 4.3), so enable this only on a local machine."
        ),
    )
    merge_method: Literal["squash", "merge", "rebase"] = Field(
        default="squash",
        description="Merge method used by the safe-merge endpoint.",
    )

    # --- logging ---------------------------------------------------------
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_json: bool = Field(
        default=True,
        description="Emit JSON logs. Set false for human-readable local output.",
    )


class MissingConfigurationError(RuntimeError):
    """Raised when required configuration is absent, with actionable guidance."""


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the validated settings singleton.

    Raises:
        MissingConfigurationError: if required environment variables are absent.
    """
    try:
        return Settings()  # type: ignore[call-arg]  # values come from the environment
    except Exception as exc:
        raise MissingConfigurationError(
            "Invalid or incomplete configuration. Copy .env.example to .env and "
            f"fill in the required values.\n\n{exc}"
        ) from exc
