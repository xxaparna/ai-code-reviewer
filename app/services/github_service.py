"""Thin synchronous GitHub REST client with retries, timeouts, and exception mapping.

Phase 4 converts this to ``httpx.AsyncClient``; the interface is kept narrow so
that change stays local.
"""

from types import TracebackType
from typing import Any, Self
from urllib.parse import quote

import httpx
import structlog
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from app.errors import (
    CodeReviewerError,
    GitHubRejectedError,
    PRNotFoundError,
    RepoAccessDeniedError,
    UpstreamRateLimitError,
)
from app.settings import get_settings

logger = structlog.get_logger(__name__)

DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=10.0)
MAX_PER_PAGE = 100


def _should_retry_exception(exc: BaseException) -> bool:
    """Retry on network errors, timeouts, 429s, or 5xx server errors."""
    if isinstance(exc, (httpx.TransportError, httpx.TimeoutException)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status == 429 or status >= 500
    return False


def _github_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"
    message = payload.get("message") if isinstance(payload, dict) else None
    return str(message)[:200] if message else f"HTTP {response.status_code}"


class GitHubService:
    BASE_URL = "https://api.github.com"

    def __init__(self, token: str | None = None, client: httpx.Client | None = None) -> None:
        auth_token = token or get_settings().github_token.get_secret_value()

        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Authorization": f"Bearer {auth_token}",
        }

        self._client = client or httpx.Client(
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

    @retry(
        retry=retry_if_exception(_should_retry_exception),
        stop=stop_after_attempt(3),
        wait=wait_exponential_jitter(initial=1, max=10),
        reraise=True,
    )
    def _request(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
    ) -> Any:
        try:
            response = self._client.request(method, endpoint, params=params, json=json_data)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 404:
                # GitHub answers 404, not 403, for private repos the token can't see.
                raise PRNotFoundError(
                    f"GitHub returned 404 for {endpoint}. Check the URL, or, if the repository "
                    "is private, that GITHUB_TOKEN belongs to an account with access to it."
                ) from exc
            if status in (401, 403):
                raise RepoAccessDeniedError(f"Access denied ({status}) for {endpoint}") from exc
            if status == 429:
                retry_after_hdr = exc.response.headers.get("Retry-After")
                retry_after = (
                    float(retry_after_hdr)
                    if retry_after_hdr and retry_after_hdr.isdigit()
                    else None
                )
                raise UpstreamRateLimitError(
                    "GitHub API rate limit exceeded", retry_after=retry_after
                ) from exc
            if status in (405, 409, 422):
                # Safe to surface: GitHub's message here is a short reason such as
                # "Head branch was modified" or "Pull Request is not mergeable".
                reason = _github_message(exc.response)
                raise GitHubRejectedError(f"GitHub rejected the request: {reason}", status) from exc
            raise CodeReviewerError(f"GitHub API error ({status}) at {endpoint}") from exc
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            logger.warning("github.request_network_error", endpoint=endpoint, error=str(exc))
            raise

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
