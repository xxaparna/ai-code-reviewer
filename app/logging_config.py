"""Structured logging configuration.

Call :func:`configure_logging` once during process startup (the FastAPI
lifespan or the CLI entrypoint). Modules themselves should only ever call
``structlog.get_logger(__name__)``.
"""

import logging
import sys

import structlog


def configure_logging(*, level: str = "INFO", json_output: bool = True) -> None:
    """Configure structlog and route stdlib logging through it."""
    shared_processors: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    renderer: structlog.typing.Processor = (
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    )

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelNamesMapping()[level]),
        # stdlib factory (not WriteLoggerFactory): add_logger_name needs a
        # logger with a .name, and it puts uvicorn/httpx on the same handler.
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Hand stdlib loggers (uvicorn, httpx, langchain) to the same output.
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stderr,
        level=logging.getLevelNamesMapping()[level],
        force=True,
    )
