"""GitHub client resilience (plan 1.2 / 1.7 / 1.14): retries fire, errors are typed."""

import httpx
import pytest
import respx
from app.errors import (
    CodeReviewerError,
    GitHubAuthError,
    GitHubRejectedError,
    PRNotFoundError,
    RepoAccessDeniedError,
    UpstreamError,
    UpstreamRateLimitError,
)
from app.services.github_service import GitHubService

BASE = "https://api.github.com"
PR = f"{BASE}/repos/o/r/pulls/7"


@pytest.fixture
def sleeps() -> list[float]:
    return []


@pytest.fixture
def github(sleeps: list[float]) -> GitHubService:
    client = httpx.Client(base_url=BASE)
    return GitHubService(client=client, sleep=sleeps.append)


@respx.mock
def test_5xx_is_retried_then_succeeds(github: GitHubService, sleeps: list[float]) -> None:
    route = respx.get(PR).mock(
        side_effect=[httpx.Response(502), httpx.Response(503), httpx.Response(200, json={"n": 7})]
    )
    assert github.get_pull_request("o", "r", 7) == {"n": 7}
    assert route.call_count == 3
    assert len(sleeps) == 2


@respx.mock
def test_retry_after_header_is_honoured(github: GitHubService, sleeps: list[float]) -> None:
    respx.get(PR).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "7"}),
            httpx.Response(200, json={}),
        ]
    )
    github.get_pull_request("o", "r", 7)
    assert sleeps == [7.0]


@respx.mock
def test_persistent_429_becomes_typed_rate_limit_error(github: GitHubService) -> None:
    route = respx.get(PR).mock(return_value=httpx.Response(429, headers={"Retry-After": "2"}))
    with pytest.raises(UpstreamRateLimitError) as info:
        github.get_pull_request("o", "r", 7)
    assert info.value.retry_after == 2.0
    assert route.call_count == 3


@respx.mock
def test_long_retry_after_is_not_waited_on(github: GitHubService, sleeps: list[float]) -> None:
    route = respx.get(PR).mock(return_value=httpx.Response(429, headers={"Retry-After": "600"}))
    with pytest.raises(UpstreamRateLimitError) as info:
        github.get_pull_request("o", "r", 7)
    assert route.call_count == 1
    assert sleeps == []
    assert info.value.retry_after == 600.0


@respx.mock
def test_primary_rate_limit_403_is_a_rate_limit(github: GitHubService) -> None:
    respx.get(PR).mock(
        return_value=httpx.Response(
            403, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "9999999999"}
        )
    )
    with pytest.raises(UpstreamRateLimitError):
        github.get_pull_request("o", "r", 7)


@pytest.mark.parametrize(
    ("status", "exc_type"),
    [
        (404, PRNotFoundError),
        (401, GitHubAuthError),
        (403, RepoAccessDeniedError),
        (422, GitHubRejectedError),
        (418, CodeReviewerError),
    ],
)
@respx.mock
def test_4xx_is_never_retried(
    github: GitHubService, status: int, exc_type: type[Exception]
) -> None:
    route = respx.get(PR).mock(return_value=httpx.Response(status, json={"message": "secret body"}))
    with pytest.raises(exc_type) as info:
        github.get_pull_request("o", "r", 7)
    assert route.call_count == 1
    if status != 422:
        assert "secret body" not in str(info.value)


@respx.mock
def test_timeouts_are_retried_then_typed(github: GitHubService) -> None:
    route = respx.get(PR).mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(UpstreamError):
        github.get_pull_request("o", "r", 7)
    assert route.call_count == 3


@respx.mock
def test_exhausted_5xx_is_upstream_error(github: GitHubService) -> None:
    respx.get(PR).mock(return_value=httpx.Response(500))
    with pytest.raises(UpstreamError):
        github.get_pull_request("o", "r", 7)


@respx.mock
def test_post_is_not_retried_on_5xx(github: GitHubService) -> None:
    """A 502 on POST may still have created the review: never post it twice."""
    route = respx.post(f"{PR}/reviews").mock(return_value=httpx.Response(502))
    with pytest.raises(UpstreamError):
        github.create_pull_request_review("o", "r", 7, body="x")
    assert route.call_count == 1


@respx.mock
def test_post_is_retried_on_429(github: GitHubService) -> None:
    route = respx.post(f"{PR}/reviews").mock(
        side_effect=[httpx.Response(429), httpx.Response(200, json={"id": 1})]
    )
    assert github.create_pull_request_review("o", "r", 7, body="x") == {"id": 1}
    assert route.call_count == 2


@respx.mock
def test_path_segments_are_url_encoded(github: GitHubService) -> None:
    route = respx.route(host="api.github.com").mock(return_value=httpx.Response(200, json={}))
    github.get_pull_request("o/../x", "r", 7)
    assert route.calls.last.request.url.raw_path == b"/repos/o%2F..%2Fx/r/pulls/7"
