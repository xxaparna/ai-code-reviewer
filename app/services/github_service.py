"""Thin synchronous GitHub REST client with retries, timeouts, and exception mapping.

Phase 4 converts this to ``httpx.AsyncClient``; the interface is kept narrow so
that change stays local.
"""

import time
from collections.abc import Callable
from types import TracebackType
from typing import Any, Self
from urllib.parse import quote

import httpx
import structlog
from tenacity import Retrying, retry_if_exception, stop_after_attempt

from app.errors import (
    CodeReviewerError,
    GitHubAuthError,
    GitHubRejectedError,
    PRNotFoundError,
    RepoAccessDeniedError,
    UpstreamError,
    UpstreamRateLimitError,
)
from app.settings import get_settings
from app.utils.retry import MAX_RETRY_AFTER_SECONDS, WaitRetryAfter, parse_retry_after

logger = structlog.get_logger(__name__)

DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=10.0)
MAX_PER_PAGE = 100
MAX_ATTEMPTS = 3
IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "PUT", "DELETE", "OPTIONS"})


def _is_rate_limited(response: httpx.Response) -> bool:
    """429, or a 403 that GitHub uses for primary/secondary rate limits."""
    if response.status_code == 429:
        return True
    if response.status_code != 403:
        return False
    headers = response.headers
    return headers.get("x-ratelimit-remaining") == "0" or "retry-after" in headers


def _retry_after_of(exc: BaseException) -> float | None:
    if isinstance(exc, httpx.HTTPStatusError):
        return parse_retry_after(exc.response.headers)
    return None


def _make_retry_predicate(method: str) -> Callable[[BaseException], bool]:
    """Classify *raw* ``httpx`` errors; domain mapping happens after the last attempt.

    POST is not idempotent (a 502 may still have created the review), so it is
    retried only when GitHub certainly did not act: rate limits and failed connects.
    """
    idempotent = method.upper() in IDEMPOTENT_METHODS

    def should_retry(exc: BaseException) -> bool:
        if isinstance(exc, httpx.HTTPStatusError):
            response = exc.response
            if _is_rate_limited(response):
                hinted = parse_retry_after(response.headers)
                return hinted is None or hinted <= MAX_RETRY_AFTER_SECONDS
            return idempotent and response.status_code >= 500
        if isinstance(exc, httpx.ConnectError | httpx.ConnectTimeout):
            return True
        return idempotent and isinstance(exc, httpx.TransportError)

    return should_retry


def _github_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"
    message = payload.get("message") if isinstance(payload, dict) else None
    return str(message)[:200] if message else f"HTTP {response.status_code}"


def _map_status_error(exc: httpx.HTTPStatusError, endpoint: str) -> CodeReviewerError:
    """Translate a final GitHub error response into a domain exception (no upstream body)."""
    response = exc.response
    status = response.status_code
    if _is_rate_limited(response):
        return UpstreamRateLimitError(
            "GitHub API rate limit exceeded", retry_after=parse_retry_after(response.headers)
        )
    if status == 404:
        # GitHub answers 404, not 403, for private repos the token can't see.
        return PRNotFoundError(
            f"GitHub returned 404 for {endpoint}. Check the URL, or, if the repository "
            "is private, that GITHUB_TOKEN belongs to an account with access to it."
        )
    if status == 401:
        return GitHubAuthError(
            "GitHub rejected the server's credentials (401). Check GITHUB_TOKEN."
        )
    if status == 403:
        return RepoAccessDeniedError(f"Access denied (403) for {endpoint}")
    if status in (405, 409, 422):
        # Safe to surface: GitHub's message here is a short reason such as
        # "Head branch was modified" or "Pull Request is not mergeable".
        return GitHubRejectedError(
            f"GitHub rejected the request: {_github_message(response)}", status
        )
    if status >= 500:
        return UpstreamError(f"GitHub is unavailable ({status}) for {endpoint}")
    return CodeReviewerError(f"GitHub API error ({status}) at {endpoint}")


class GitHubService:
    BASE_URL = "https://api.github.com"

    def __init__(
        self,
        token: str | None = None,
        client: httpx.Client | None = None,
        *,
        max_attempts: int = MAX_ATTEMPTS,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._max_attempts = max_attempts
        self._sleep = sleep
        if client is not None:
            self._client = client
            return

        auth_token = token or get_settings().github_token.get_secret_value()
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Authorization": f"Bearer {auth_token}",
        }
        self._client = httpx.Client(
            base_url=self.BASE_URL,
            headers=headers,
            timeout=DEFAULT_TIMEOUT,
            follow_redirects=True,
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def _send(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None,
        json_data: dict[str, Any] | None,
    ) -> httpx.Response:
        response = self._client.request(method, endpoint, params=params, json=json_data)
        response.raise_for_status()
        return response

    def _request(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
    ) -> Any:
        """Send a request; retry transient failures, then map errors to domain exceptions.

        Retrying happens on raw ``httpx`` errors and the mapping happens outside the
        retry loop - mapping inside it would hide the error from the retry predicate.
        """
        retrying = Retrying(
            retry=retry_if_exception(_make_retry_predicate(method)),
            stop=stop_after_attempt(self._max_attempts),
            wait=WaitRetryAfter(_retry_after_of),
            sleep=self._sleep,
            before_sleep=lambda rs: logger.warning(
                "github.request_retry",
                endpoint=endpoint,
                attempt=rs.attempt_number,
                error=type(rs.outcome.exception()).__name__ if rs.outcome else None,
            ),
            reraise=True,
        )
        try:
            response = retrying(self._send, method, endpoint, params, json_data)
        except httpx.HTTPStatusError as exc:
            raise _map_status_error(exc, endpoint) from exc
        except httpx.TransportError as exc:
            logger.warning("github.request_network_error", endpoint=endpoint, error=str(exc))
            raise UpstreamError(f"GitHub is unreachable ({type(exc).__name__})") from exc
        return response.json()

    def get_pull_request(self, owner: str, repo: str, pull_number: int) -> dict[str, Any]:
        safe_owner = quote(owner, safe="")
        safe_repo = quote(repo, safe="")
        endpoint = f"/repos/{safe_owner}/{safe_repo}/pulls/{pull_number}"
        result: dict[str, Any] = self._request("GET", endpoint)
        return result

    def get_pull_request_files(
        self, owner: str, repo: str, pull_number: int
    ) -> list[dict[str, Any]]:
        safe_owner = quote(owner, safe="")
        safe_repo = quote(repo, safe="")
        endpoint = f"/repos/{safe_owner}/{safe_repo}/pulls/{pull_number}/files"
        files: list[dict[str, Any]] = []
        page = 1

        while True:
            batch: list[dict[str, Any]] = self._request(
                "GET", endpoint, params={"per_page": MAX_PER_PAGE, "page": page}
            )
            if not batch:
                break
            files.extend(batch)
            if len(batch) < MAX_PER_PAGE:
                break
            page += 1

        return files

    def create_pull_request_review(
        self,
        owner: str,
        repo: str,
        pull_number: int,
        body: str,
        event: str = "COMMENT",
        comments: list[dict[str, Any]] | None = None,
        commit_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a pull request review, optionally with inline comments.

        ``commit_id`` anchors line comments to the commit that was reviewed, so
        they stay correct even if the branch moves on before publishing.
        """
        safe_owner = quote(owner, safe="")
        safe_repo = quote(repo, safe="")
        endpoint = f"/repos/{safe_owner}/{safe_repo}/pulls/{pull_number}/reviews"
        payload: dict[str, Any] = {"body": body, "event": event}
        if comments:
            payload["comments"] = comments
        if commit_id:
            payload["commit_id"] = commit_id

        result: dict[str, Any] = self._request("POST", endpoint, json_data=payload)
        return result

    def get_check_runs(self, owner: str, repo: str, ref: str) -> list[dict[str, Any]]:
        """Check runs (GitHub Actions and other apps) for a commit."""
        safe_owner = quote(owner, safe="")
        safe_repo = quote(repo, safe="")
        endpoint = f"/repos/{safe_owner}/{safe_repo}/commits/{quote(ref, safe='')}/check-runs"
        runs: list[dict[str, Any]] = []
        page = 1
        while True:
            result: dict[str, Any] = self._request(
                "GET", endpoint, params={"per_page": MAX_PER_PAGE, "page": page}
            )
            batch: list[dict[str, Any]] = result.get("check_runs", [])
            runs.extend(batch)
            if len(batch) < MAX_PER_PAGE:
                break
            page += 1
        return runs

    def get_combined_status(self, owner: str, repo: str, ref: str) -> dict[str, Any]:
        """Legacy commit statuses (external CI that does not use the Checks API)."""
        safe_owner = quote(owner, safe="")
        safe_repo = quote(repo, safe="")
        endpoint = f"/repos/{safe_owner}/{safe_repo}/commits/{quote(ref, safe='')}/status"
        result: dict[str, Any] = self._request("GET", endpoint)
        return result

    def merge_pull_request(
        self,
        owner: str,
        repo: str,
        pull_number: int,
        *,
        sha: str,
        merge_method: str,
        commit_title: str | None = None,
    ) -> dict[str, Any]:
        """Merge a pull request, but only if its head is still exactly ``sha``.

        GitHub enforces the ``sha`` precondition atomically and answers 409 if
        the branch moved, which closes the gap between our checks and the merge.
        """
        safe_owner = quote(owner, safe="")
        safe_repo = quote(repo, safe="")
        endpoint = f"/repos/{safe_owner}/{safe_repo}/pulls/{pull_number}/merge"
        payload: dict[str, Any] = {"sha": sha, "merge_method": merge_method}
        if commit_title:
            payload["commit_title"] = commit_title
        result: dict[str, Any] = self._request("PUT", endpoint, json_data=payload)
        return result
