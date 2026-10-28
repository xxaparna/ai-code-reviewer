"""Retry helpers shared by the GitHub and Groq clients.

One policy, two upstreams: retry only transient failures (429, 5xx, timeouts,
connection errors), back off exponentially with jitter, honour ``Retry-After``
when the server sends one, and give up when the server asks us to wait longer
than :data:`MAX_RETRY_AFTER_SECONDS` - that wait belongs to the caller.
"""

import time
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx
from tenacity import RetryCallState
from tenacity.wait import wait_base, wait_exponential_jitter

MAX_RETRY_AFTER_SECONDS = 30.0


def parse_retry_after(headers: httpx.Headers | None, *, now: float | None = None) -> float | None:
    """Seconds to wait according to ``Retry-After`` (or GitHub's ``x-ratelimit-reset``)."""
    if headers is None:
        return None
    value = headers.get("retry-after")
    if value:
        value = value.strip()
        try:
            return max(0.0, float(value))
        except ValueError:
            pass
        try:
            when: datetime = parsedate_to_datetime(value)
        except (TypeError, ValueError, IndexError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        return max(0.0, (when - datetime.now(UTC)).total_seconds())

    if headers.get("x-ratelimit-remaining") == "0":
        reset = headers.get("x-ratelimit-reset")
        if reset and reset.isdigit():
            current = time.time() if now is None else now
            return max(0.0, float(reset) - current)
    return None


class WaitRetryAfter(wait_base):
    """Wait what the server asked for, else exponential backoff with jitter."""

    def __init__(
        self,
        retry_after_of: Callable[[BaseException], float | None],
        fallback: wait_base | None = None,
    ) -> None:
        self._retry_after_of = retry_after_of
        self._fallback = fallback or wait_exponential_jitter(initial=1, max=10)

    def __call__(self, retry_state: RetryCallState) -> float:
        outcome = retry_state.outcome
        exc = outcome.exception() if outcome is not None else None
        if exc is not None:
            hinted = self._retry_after_of(exc)
            if hinted is not None:
                return min(hinted, MAX_RETRY_AFTER_SECONDS)
        return self._fallback(retry_state)
