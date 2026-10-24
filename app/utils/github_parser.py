from typing import Any
from urllib.parse import urlparse

from app.errors import InvalidPRURLError


def parse_pr_url(url: str) -> dict[str, Any]:
    """
    Parse a GitHub Pull Request URL into its components.

    Accepts the canonical form and tab suffixes:
    https://github.com/owner/repo/pull/7
    https://github.com/owner/repo/pull/7/files
    https://github.com/owner/repo/pull/7/commits
    """
    if not url or not isinstance(url, str):
        raise InvalidPRURLError("PR URL must be a non-empty string.")

    parsed = urlparse(url.strip())

    if parsed.scheme.lower() != "https" or parsed.netloc.lower() not in (
        "github.com",
        "www.github.com",
    ):
        raise InvalidPRURLError(
            f"Invalid GitHub Pull Request URL: {url!r}. Host must be 'github.com'."
        )

    parts = [p for p in parsed.path.strip("/").split("/") if p]

    if len(parts) < 4 or parts[2] != "pull" or not parts[3].isdigit():
        raise InvalidPRURLError(
            f"Invalid GitHub Pull Request URL: {url!r}. "
            "Expected https://github.com/<owner>/<repo>/pull/<number>"
        )

    owner = parts[0]
    repo = parts[1]

    if owner in (".", "..") or repo in (".", "..") or "/" in owner or "/" in repo:
        raise InvalidPRURLError(f"Invalid repository path elements in URL: {url!r}")

    return {
        "owner": owner,
        "repo": repo,
        "pull_number": int(parts[3]),
    }
