from urllib.parse import urlparse


def parse_pr_url(url: str) -> dict:
    """
    Parse a GitHub Pull Request URL into its components.

    Example:
    https://github.com/owner/repo/pull/7
    """

    parsed = urlparse(url)

    parts = parsed.path.strip("/").split("/")

    if len(parts) != 4 or parts[2] != "pull":
        raise ValueError("Invalid GitHub Pull Request URL")

    return {
        "owner": parts[0],
        "repo": parts[1],
        "pull_number": int(parts[3]),
    }