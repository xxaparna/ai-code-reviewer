"""Groq LLM client: explicit timeouts, one retry layer, typed failures.

Retry policy (plan 1.2): only transient failures are retried - 429, 5xx,
timeouts and connection errors, classified by exception *type*, never by
substring. ``Retry-After`` is honoured. The Groq SDK's own retries are turned
off (``max_retries=0``) so attempts do not multiply across layers.

Failures leave this module as domain exceptions:

- :class:`UpstreamRateLimitError` - still rate limited after the last attempt
- :class:`LLMUnavailableError` - timeouts, 5xx, connection or auth problems
- :class:`StructuredOutputError` - the model answered, but not in the schema
"""

import threading
from collections.abc import Callable
from typing import Any, Protocol, TypeVar

import groq
import structlog
from langchain_groq import ChatGroq
from pydantic import BaseModel, ValidationError
from tenacity import Retrying, retry_if_exception, stop_after_attempt

from app.errors import LLMUnavailableError, StructuredOutputError, UpstreamRateLimitError
from app.settings import get_settings
from app.utils.retry import MAX_RETRY_AFTER_SECONDS, WaitRetryAfter, parse_retry_after

logger = structlog.get_logger(__name__)

T = TypeVar("T", bound=BaseModel)

DEFAULT_LLM_TIMEOUT = 45.0
DEFAULT_MAX_ATTEMPTS = 3

Messages = list[tuple[str, str]]


class ReviewLLM(Protocol):
    """What the graph needs from a model. Tests substitute a fake."""

    def generate(self, *, system_prompt: str, user_prompt: str) -> str: ...

    def generate_structured(
        self, *, system_prompt: str, user_prompt: str, schema: type[T]
    ) -> T: ...


def _retry_after_of(exc: BaseException) -> float | None:
    if isinstance(exc, groq.APIStatusError):
        return parse_retry_after(exc.response.headers)
    return None


def is_transient_llm_error(exc: BaseException) -> bool:
    if isinstance(exc, groq.RateLimitError):
        hinted = _retry_after_of(exc)
        return hinted is None or hinted <= MAX_RETRY_AFTER_SECONDS
    if isinstance(exc, groq.InternalServerError | groq.APITimeoutError | groq.APIConnectionError):
        return True
    return isinstance(exc, groq.APIStatusError) and exc.status_code >= 500


def _map_llm_error(exc: Exception, model: str) -> Exception:
    if isinstance(exc, groq.RateLimitError):
        return UpstreamRateLimitError(
            f"Groq rate limit exceeded for {model}", retry_after=_retry_after_of(exc)
        )
    if isinstance(exc, groq.APITimeoutError):
        return LLMUnavailableError(f"LLM call timed out for {model}")
    if isinstance(exc, groq.APIConnectionError):
        return LLMUnavailableError(f"Could not reach Groq for {model}")
    if isinstance(exc, groq.APIStatusError):
        if exc.status_code in (400, 422) and "json" in str(exc).lower():
            # JSON-mode / tool-call validation failed server-side: a shape problem.
            return StructuredOutputError(f"{model} did not produce valid structured output")
        return LLMUnavailableError(f"Groq returned {exc.status_code} for {model}")
    if isinstance(exc, ValidationError | ValueError | KeyError | TypeError):
        return StructuredOutputError(f"{model} returned output that does not match the schema")
    return LLMUnavailableError(f"LLM call failed for {model}: {type(exc).__name__}")


class LLMClient:
    def __init__(
        self,
        model_name: str | None = None,
        temperature: float | None = None,
        timeout: float | None = None,
        *,
        max_attempts: int | None = None,
        chat_model: Any | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self.model_name = model_name or ""
        self.timeout = timeout if timeout is not None else DEFAULT_LLM_TIMEOUT
        self.max_attempts = max_attempts if max_attempts is not None else DEFAULT_MAX_ATTEMPTS
        self._sleep = sleep

        if chat_model is not None:
            self.temperature = temperature if temperature is not None else 0.0
            self.model: Any = chat_model
            return

        settings = get_settings()
        self.model_name = model_name or settings.groq_model
        self.temperature = settings.groq_temperature if temperature is None else temperature
        if timeout is None:
            self.timeout = settings.llm_timeout_seconds
        if max_attempts is None:
            self.max_attempts = settings.llm_max_attempts
        self.model = ChatGroq(
            model=self.model_name,
            api_key=settings.groq_api_key,
            temperature=self.temperature,
            timeout=self.timeout,
            max_retries=0,  # tenacity below is the only retry layer
        )

    def _invoke(self, runnable: Any, messages: Messages) -> Any:
        kwargs: dict[str, Any] = {
            "retry": retry_if_exception(is_transient_llm_error),
            "stop": stop_after_attempt(self.max_attempts),
            "wait": WaitRetryAfter(_retry_after_of),
            "reraise": True,
            "before_sleep": lambda rs: logger.warning(
                "llm.retry",
                model=self.model_name,
                attempt=rs.attempt_number,
                error=type(rs.outcome.exception()).__name__ if rs.outcome else None,
            ),
        }
        if self._sleep is not None:
            kwargs["sleep"] = self._sleep
        try:
            return Retrying(**kwargs)(runnable.invoke, messages)
        except Exception as exc:
            mapped = _map_llm_error(exc, self.model_name)
            logger.warning(
                "llm.call_failed",
                model=self.model_name,
                error_type=type(exc).__name__,
                mapped=type(mapped).__name__,
            )
            raise mapped from exc

    def generate(self, *, system_prompt: str, user_prompt: str) -> str:
        """Invoke the LLM and return the raw response text."""
        response = self._invoke(self.model, [("system", system_prompt), ("human", user_prompt)])
        content = response.content
        return content if isinstance(content, str) else " ".join(str(item) for item in content)

    def generate_structured(self, *, system_prompt: str, user_prompt: str, schema: type[T]) -> T:
        """Invoke the LLM with output constrained to ``schema``."""
        structured_model = self.model.with_structured_output(schema)
        result = self._invoke(structured_model, [("system", system_prompt), ("human", user_prompt)])
        if isinstance(result, schema):
            return result
        if isinstance(result, dict):
            try:
                return schema.model_validate(result)
            except ValidationError as exc:
                raise StructuredOutputError(
                    f"{self.model_name} returned output that does not match the schema"
                ) from exc
        raise StructuredOutputError(f"{self.model_name} returned an unexpected structured result")


class LLMPool:
    """One client per model tier, built lazily (so building the graph needs no key)."""

    def __init__(self, models: dict[str, str] | None = None) -> None:
        self._models = models
        self._clients: dict[str, ReviewLLM] = {}
        self._lock = threading.Lock()  # reviewer branches run in a thread pool

    @classmethod
    def from_settings(cls) -> "LLMPool":
        settings = get_settings()
        return cls(
            {"strong": settings.groq_model, "fast": settings.groq_model_fast or settings.groq_model}
        )

    def __call__(self, tier: str) -> ReviewLLM:
        with self._lock:
            if self._models is None:
                self._models = LLMPool.from_settings()._models or {}
            model = self._models.get(tier) or self._models.get("strong")
            key = model or tier
            if key not in self._clients:
                self._clients[key] = LLMClient(model_name=model)
            return self._clients[key]
