"""Typed exceptions for AI Code Reviewer.

Provides domain-specific exceptions to avoid leaking upstream error details
and ensure correct HTTP error mapping.
"""


class CodeReviewerError(Exception):
    """Base class for all domain exceptions in AI Code Reviewer."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class InvalidPRURLError(CodeReviewerError):
    """Raised when a pull request URL is malformed or invalid."""


class PRNotFoundError(CodeReviewerError):
    """Raised when the specified pull request is not found on GitHub."""


class RepoAccessDeniedError(CodeReviewerError):
    """Raised when GitHub access is unauthorized or forbidden."""


class UpstreamRateLimitError(CodeReviewerError):
    """Raised when an upstream service (GitHub or Groq) rate limits the request."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class LLMUnavailableError(CodeReviewerError):
    """Raised when the LLM service is unavailable or times out."""


class DiffTooLargeError(CodeReviewerError):
    """Raised when a PR diff exceeds processing limits."""


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
