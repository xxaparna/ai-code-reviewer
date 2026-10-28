import pytest
from app.errors import InvalidPRURLError
from app.utils.github_parser import parse_pr_url


def test_parse_valid_pr_url() -> None:
    url = "https://github.com/owner/repo/pull/42"
    result = parse_pr_url(url)
    assert result == {
        "owner": "owner",
        "repo": "repo",
        "pull_number": 42,
    }


def test_parse_pr_url_with_trailing_slash() -> None:
    url = "https://github.com/fastapi/fastapi/pull/1000/"
    result = parse_pr_url(url)
    assert result["owner"] == "fastapi"
    assert result["repo"] == "fastapi"
    assert result["pull_number"] == 1000


def test_parse_invalid_pr_url() -> None:
    with pytest.raises(ValueError):
        parse_pr_url("https://github.com/owner/repo/issues/42")


def test_parse_pr_url_with_tab_suffix() -> None:
    for suffix in ("files", "commits", "changes"):
        result = parse_pr_url(f"https://github.com/owner/repo/pull/7/{suffix}")
        assert result == {"owner": "owner", "repo": "repo", "pull_number": 7}


def test_parse_pr_url_non_numeric_number() -> None:
    with pytest.raises(ValueError):
        parse_pr_url("https://github.com/owner/repo/pull/abc")


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.com/a/b/pull/1",
        "http://github.com/a/b/pull/1",
        "https://github.com.evil.com/a/b/pull/1",
        "https://user@github.com/a/b/pull/1",
        "https://github.com:8443/a/b/pull/1",
        "https://github.com/../b/pull/1",
        "https://github.com/a/../pull/1",
        "https://github.com/a/b%2F..%2Fc/pull/1",
        "https://github.com/a/b/pull/0",
        "https://github.com/a/b/pull/\u0661",
        "https://github.com/a/b/pull/1/files/extra/more",
        "",
    ],
)
def test_hostile_or_malformed_urls_are_rejected(url: str) -> None:
    with pytest.raises(InvalidPRURLError):
        parse_pr_url(url)
