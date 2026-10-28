import re
from typing import Any
from urllib.parse import urlparse

from app.errors import InvalidPRURLError

GITHUB_HOSTS = frozenset({"github.com", "www.github.com"})
# GitHub's own rules: owners are alphanumerics and single hyphens (<= 39 chars),
# repository names are alphanumerics plus "._-" (<= 100 chars).
_OWNER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
_REPO_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
MAX_PR_NUMBER = 2**31 - 1


def parse_pr_url(url: str) -> dict[str, Any]:
    """
    Parse a GitHub Pull Request URL into its components.

    Accepts the canonical form and tab suffixes:
    https://github.com/owner/repo/pull/7
    https://github.com/owner/repo/pull/7/files
    https://github.com/owner/repo/pull/7/commits

    Raises:
        InvalidPRURLError (a ``ValueError``): for any other host, scheme or shape.
    """
    if not url or not isinstance(url, str):
        raise InvalidPRURLError("PR URL must be a non-empty string.")

    parsed = urlparse(url.strip())

    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").lower() not in GITHUB_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in (None, 443)
    ):
        raise InvalidPRURLError(
            f"Invalid GitHub Pull Request URL: {url!r}. Host must be 'github.com'."
        )

    parts = [p for p in parsed.path.strip("/").split("/") if p]

    if (
        len(parts) < 4
        or parts[2] != "pull"
        or not (parts[3].isascii() and parts[3].isdigit())
        or len(parts) > 5
    ):
        raise InvalidPRURLError(
            f"Invalid GitHub Pull Request URL: {url!r}. "
            "Expected https://github.com/<owner>/<repo>/pull/<number>"
        )

    owner, repo = parts[0], parts[1]
    if not _OWNER_RE.match(owner) or not _REPO_RE.match(repo) or repo in (".", ".."):
        raise InvalidPRURLError(f"Invalid repository path elements in URL: {url!r}")

    number = int(parts[3])
    if not 0 < number <= MAX_PR_NUMBER:
        raise InvalidPRURLError(f"Invalid pull request number in URL: {url!r}")

    return {"owner": owner, "repo": repo, "pull_number": number}
