"""ASGI application factory.

Only wiring lives here. The command line entrypoint is in :mod:`app.cli`.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from app.api.routes import router
from app.logging_config import configure_logging
from app.services.review_service import ReviewService
from app.services.review_store import ReviewStore
from app.settings import get_settings

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Validate configuration, then build the long-lived services once (plan 1.9)."""
    settings = get_settings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    service = ReviewService.from_settings()
    app.state.review_service = service
    app.state.review_store = ReviewStore()
    logger.info("application.startup", model=settings.groq_model)
    try:
        yield
    finally:
        service.close()
        logger.info("application.shutdown")


def create_app() -> FastAPI:
    app = FastAPI(
        title="AI Code Reviewer API",
        description="Multi-agent LangGraph AI code reviewer for GitHub pull requests",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.include_router(router)

    @app.get("/health", tags=["meta"])
    def healthcheck() -> dict[str, str]:
        return {"status": "healthy", "service": "ai-code-reviewer"}

    return app


app = create_app()
