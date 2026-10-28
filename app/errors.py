"""Typed exceptions for AI Code Reviewer.

Provides domain-specific exceptions to avoid leaking upstream error details
and ensure correct HTTP error mapping.
"""


class CodeReviewerError(Exception):
    """Base class for all domain exceptions in AI Code Reviewer."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class InvalidPRURLError(CodeReviewerError, ValueError):
    """Raised when a pull request URL is malformed or invalid."""


class PRNotFoundError(CodeReviewerError):
    """Raised when the specified pull request is not found on GitHub."""


class RepoAccessDeniedError(CodeReviewerError):
    """Raised when GitHub access is unauthorized or forbidden."""


class GitHubAuthError(RepoAccessDeniedError):
    """Raised when GitHub rejects the server's own credentials (401).

    That is a server misconfiguration, not the caller's fault, so it maps to 502.
    """


class UpstreamError(CodeReviewerError):
    """Raised when an upstream service keeps failing (5xx, network) after retries."""


class UpstreamRateLimitError(CodeReviewerError):
    """Raised when an upstream service (GitHub or Groq) rate limits the request."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class LLMUnavailableError(CodeReviewerError):
    """Raised when the LLM service is unavailable or times out."""


class DiffTooLargeError(CodeReviewerError):
    """Raised when a PR diff exceeds processing limits."""


class StructuredOutputError(CodeReviewerError):
    """Raised when the model answered, but not in the requested structured shape.

    Not transient: retrying the same call is pointless, so the caller falls back
    to the tolerant text parser instead (when enabled).
    """


class UnknownAgentError(CodeReviewerError, ValueError):
    """Raised when a review asks for an agent that is not registered or is disabled."""


class BudgetExceededError(CodeReviewerError):
    """Raised when a review exceeds token or cost limits."""


class UnreviewablePRError(CodeReviewerError):
    """Raised when a PR has 0 changed files or no reviewable patch content."""


class GitHubRejectedError(CodeReviewerError):
    """Raised when GitHub refuses a write (405/409/422), e.g. a merge or an invalid comment."""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class ReviewNotFoundError(CodeReviewerError):
    """Raised when a review id is unknown (or has been evicted from the store)."""


class AlreadyPublishedError(CodeReviewerError):
    """Raised when a review's comments have already been posted to the pull request."""


class MergeBlockedError(CodeReviewerError):
    """Raised when the merge gate refuses a merge. Carries the failing checks."""

    def __init__(self, message: str, checks: list[dict[str, object]]) -> None:
        super().__init__(message)
        self.checks = checks
