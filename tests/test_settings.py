"""Configuration must be lazy, validated, and must never leak secrets."""

from pathlib import Path

import app.settings as settings_module
import pytest
from app.settings import MissingConfigurationError, get_settings
from pydantic import ValidationError

CREDENTIAL_VARS = ("GITHUB_TOKEN", "GROQ_API_KEY")
MODEL_VARS = ("GROQ_MODEL", "GROQ_TEMPERATURE", "LOG_LEVEL", "LOG_JSON")


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Isolate settings from the developer's own environment and .env file.

    Settings resolve ``.env`` relative to the working directory, so running from
    an empty temp directory guarantees these tests read only what they set.
    """
    monkeypatch.chdir(tmp_path)
    for var in (*CREDENTIAL_VARS, *MODEL_VARS):
        monkeypatch.delenv(var, raising=False)
    get_settings.cache_clear()


def _set_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "gh-test-token")
    monkeypatch.setenv("GROQ_API_KEY", "groq-test-key")


def test_reads_credentials_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_credentials(monkeypatch)

    settings = get_settings()

    assert settings.github_token.get_secret_value() == "gh-test-token"
    assert settings.groq_api_key.get_secret_value() == "groq-test-key"


def test_model_defaults_are_applied(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_credentials(monkeypatch)

    settings = get_settings()

    assert settings.groq_model == "openai/gpt-oss-120b"
    assert settings.groq_temperature == 0.2
    assert settings.log_level == "INFO"
    assert settings.log_json is True


def test_environment_overrides_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_credentials(monkeypatch)
    monkeypatch.setenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    monkeypatch.setenv("LOG_JSON", "false")

    settings = get_settings()

    assert settings.groq_model == "llama-3.3-70b-versatile"
    assert settings.log_json is False


def test_is_only_validated_on_first_use() -> None:
    """Importing the module with no credentials present must not raise."""
    assert settings_module.Settings is not None

    with pytest.raises(MissingConfigurationError):
        get_settings()


def test_missing_credentials_report_the_underlying_validation_error() -> None:
    with pytest.raises(MissingConfigurationError) as exc_info:
        get_settings()

    assert isinstance(exc_info.value.__cause__, ValidationError)
    assert "github_token" in str(exc_info.value)
    assert ".env.example" in str(exc_info.value)


def test_out_of_range_temperature_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_credentials(monkeypatch)
    monkeypatch.setenv("GROQ_TEMPERATURE", "9.5")

    with pytest.raises(MissingConfigurationError):
        get_settings()


def test_unknown_log_level_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_credentials(monkeypatch)
    monkeypatch.setenv("LOG_LEVEL", "CHATTY")

    with pytest.raises(MissingConfigurationError):
        get_settings()


def test_secrets_are_masked_when_rendered(monkeypatch: pytest.MonkeyPatch) -> None:
    """A settings object must be safe to log or include in a traceback."""
    monkeypatch.setenv("GITHUB_TOKEN", "super-secret-token")
    monkeypatch.setenv("GROQ_API_KEY", "super-secret-key")

    rendered = f"{get_settings()!r}"

    assert "super-secret-token" not in rendered
    assert "super-secret-key" not in rendered
    assert "SecretStr" in rendered


def test_settings_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_credentials(monkeypatch)

    assert get_settings() is get_settings()
