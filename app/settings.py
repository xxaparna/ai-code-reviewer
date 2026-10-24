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
