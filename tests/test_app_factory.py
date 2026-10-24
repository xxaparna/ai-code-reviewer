"""The package must import, and the app must build, without credentials."""

import asyncio
import importlib

import httpx
import pytest
from app.main import create_app
from app.settings import get_settings
from fastapi import FastAPI

CREDENTIAL_VARS = ("GITHUB_TOKEN", "GROQ_API_KEY")


@pytest.fixture
def _no_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in CREDENTIAL_VARS:
        monkeypatch.delenv(var, raising=False)
    get_settings.cache_clear()


async def _get(app: FastAPI, path: str) -> httpx.Response:
    """Drive the ASGI app in-process, running its lifespan.

    Starlette's own TestClient now wants the httpx2 package, which respx cannot
    intercept; ASGITransport keeps the whole suite on one HTTP library.
    """
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get(path)


@pytest.mark.usefixtures("_no_credentials")
@pytest.mark.parametrize(
    "module",
    [
        "app.main",
        "app.cli",
        "app.api.routes",
        "app.graph.nodes",
        "app.graph.builder",
        "app.services.review_service",
    ],
)
def test_modules_import_without_credentials(module: str) -> None:
    """Importing must never trigger configuration validation.

    This is what keeps a fresh clone, CI and the linters working with no .env.
    """
    assert importlib.import_module(module) is not None


@pytest.mark.usefixtures("_no_credentials")
def test_app_builds_without_credentials() -> None:
    """Building the app and generating its schema must need no configuration."""
    paths = create_app().openapi()["paths"]

    assert "/health" in paths
    assert "/api/v1/review" in paths
    assert "post" in paths["/api/v1/review"]


def test_healthcheck_responds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "gh-test-token")
    monkeypatch.setenv("GROQ_API_KEY", "groq-test-key")
    get_settings.cache_clear()

    response = asyncio.run(_get(create_app(), "/health"))

    assert response.status_code == 200
    assert response.json() == {"status": "healthy", "service": "ai-code-reviewer"}


def test_startup_fails_loudly_without_credentials() -> None:
    """A misconfigured process must fail at startup, not on the first request."""
    import app.settings as settings_module
    from app.settings import MissingConfigurationError

    def _raise() -> None:
        raise ValueError("field required")

    original = settings_module.Settings
    settings_module.Settings = _raise  # type: ignore[assignment, misc]
    get_settings.cache_clear()
    try:
        with pytest.raises(MissingConfigurationError):
            asyncio.run(_get(create_app(), "/health"))
    finally:
        settings_module.Settings = original  # type: ignore[misc]
        get_settings.cache_clear()
