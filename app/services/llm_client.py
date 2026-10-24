"""Groq LLM client wrapper with timeouts, retries, and structured output support."""

from typing import TypeVar

import structlog
from langchain_groq import ChatGroq
from pydantic import BaseModel
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from app.errors import LLMUnavailableError, UpstreamRateLimitError
from app.settings import get_settings

logger = structlog.get_logger(__name__)

T = TypeVar("T", bound=BaseModel)

DEFAULT_LLM_TIMEOUT = 45.0


def _should_retry_llm_exception(exc: BaseException) -> bool:
    """Retry LLM calls on 429 rate limit or 5xx server errors / timeouts."""
    exc_str = str(exc).lower()
    if "429" in exc_str or "rate limit" in exc_str or "timeout" in exc_str or "500" in exc_str or "503" in exc_str:
        return True
    return False


class LLMClient:
    def __init__(
        self,
        model_name: str | None = None,
        temperature: float | None = None,
        timeout: float = DEFAULT_LLM_TIMEOUT,
    ) -> None:
        settings = get_settings()
        self.model_name = model_name or settings.groq_model
        self.temperature = settings.groq_temperature if temperature is None else temperature
        self.timeout = timeout

        self.model = ChatGroq(
            model=self.model_name,
            api_key=settings.groq_api_key,
            temperature=self.temperature,
            request_timeout=self.timeout,
        )

    @retry(
        retry=retry_if_exception(_should_retry_llm_exception),
        stop=stop_after_attempt(3),
        wait=wait_exponential_jitter(initial=1, max=10),
        reraise=True,
    )
    def generate(self, *, system_prompt: str, user_prompt: str) -> str:
        """Invoke the LLM and return raw response text."""
        try:
            response = self.model.invoke(
                [
                    ("system", system_prompt),
                    ("human", user_prompt),
                ]
            )
            content = response.content
            return content if isinstance(content, str) else " ".join(str(item) for item in content)
        except Exception as exc:
            exc_str = str(exc).lower()
            logger.warning("llm.generate_failed", error=str(exc), model=self.model_name)
            if "429" in exc_str or "rate limit" in exc_str:
                raise UpstreamRateLimitError(f"Groq rate limit exceeded for {self.model_name}") from exc
            raise LLMUnavailableError(f"LLM call failed for {self.model_name}: {exc}") from exc

    @retry(
        retry=retry_if_exception(_should_retry_llm_exception),
        stop=stop_after_attempt(3),
        wait=wait_exponential_jitter(initial=1, max=10),
        reraise=True,
    )
    def generate_structured(self, *, system_prompt: str, user_prompt: str, schema: type[T]) -> T:
        """Invoke the LLM with structured output enforced by a Pydantic schema."""
        try:
            structured_model = self.model.with_structured_output(schema)
            result = structured_model.invoke(
                [
                    ("system", system_prompt),
                    ("human", user_prompt),
                ]
            )
            if isinstance(result, schema):
                return result
            raise LLMUnavailableError("LLM returned unexpected response format for structured output.")
        except Exception as exc:
            exc_str = str(exc).lower()
            logger.warning("llm.generate_structured_failed", error=str(exc), model=self.model_name)
            if "429" in exc_str or "rate limit" in exc_str:
                raise UpstreamRateLimitError(f"Groq rate limit exceeded for {self.model_name}") from exc
            raise LLMUnavailableError(f"Structured LLM call failed for {self.model_name}: {exc}") from exc
