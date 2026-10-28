"""Frontend API: create, read, publish, merge-readiness and merge, over ASGI."""

import asyncio
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from app.agents.registry import default_registry
from app.api.routes import get_review_service, get_review_store
from app.errors import (
    GitHubAuthError,
    LLMUnavailableError,
    PRNotFoundError,
    UpstreamError,
    UpstreamRateLimitError,
)
from app.main import create_app
from app.models.enums import Category, Severity
from app.models.review_comment import ReviewComment
from app.models.review_file import ReviewFile
from app.models.review_request import ReviewRequest
from app.models.review_response import ReviewResponse
from app.services.review_store import ReviewStore
from app.settings import get_settings
from fastapi import FastAPI

from tests.conftest import make_review_request
from tests.fakes import FakeGitHubService

PATCH = "@@ -1,2 +1,3 @@\n import os\n+import sys\n \n"


class StubReviewService:
    """No graph, no LLM: returns one HIGH finding with a suggested change."""

    def __init__(self) -> None:
        self.github_service = FakeGitHubService()
        self.registry = default_registry()
        self.fail_with: Exception | None = None
        self.requested_agents: list[str] | None = None

    def fetch_request(self, pr_url: str) -> ReviewRequest:
        if self.fail_with is not None:
            raise self.fail_with
        return make_review_request(
            head_sha="deadbeef",
            files=[ReviewFile(filename="app/example.py", status="modified", patch=PATCH)],
        )

    def review(self, request: ReviewRequest, agents: list[str] | None = None) -> ReviewResponse:
        if agents is not None:
            self.registry.select(agents)
        self.requested_agents = agents
        return ReviewResponse(
            summary="One issue.",
            comments=[
                ReviewComment(
                    file="app/example.py",
                    line=2,
                    severity=Severity.HIGH,
                    category=Category.SECURITY,
                    message="Bad import.",
                    suggested_code="import os.path",
                )
            ],
            metadata={"complete": True, "errors": [], "severity_counts": {"HIGH": 1}},
        )


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> Iterator[FastAPI]:
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.setenv("MERGE_ENABLED", "true")
    get_settings.cache_clear()
    application = create_app()
    stub = StubReviewService()
    application.state.stub = stub
    store = ReviewStore()
    application.dependency_overrides[get_review_service] = lambda: stub
    application.dependency_overrides[get_review_store] = lambda: store
    yield application
    get_settings.cache_clear()


def _call(
    app: FastAPI, calls: list[tuple[str, str, dict[str, Any] | None]]
) -> list[httpx.Response]:
    async def run() -> list[httpx.Response]:
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                out: list[httpx.Response] = []
                for method, path, body in calls:
                    path = path.format(id=out[0].json()["id"] if out else "")
                    out.append(await client.request(method, path, json=body))
                return out

    return asyncio.run(run())


def test_review_lifecycle(app: FastAPI) -> None:
    created, fetched, listed, readiness, merge, published = _call(
        app,
        [
            ("POST", "/api/v1/reviews", {"pr_url": "https://github.com/o/r/pull/7"}),
            ("GET", "/api/v1/reviews/{id}", None),
            ("GET", "/api/v1/reviews", None),
            ("GET", "/api/v1/reviews/{id}/merge-readiness", None),
            ("POST", "/api/v1/reviews/{id}/merge", {}),
            ("POST", "/api/v1/reviews/{id}/publish", {"min_severity": "HIGH"}),
        ],
    )

    assert created.status_code == 201
    body = created.json()
    assert body["pull_request"]["head_sha"] == "deadbeef"
    assert body["findings"][0]["suggested_code"] == "import os.path"
    assert body["files"][0]["patch"] == PATCH

    assert fetched.json()["id"] == body["id"]
    assert [r["id"] for r in listed.json()] == [body["id"]]

    assert readiness.status_code == 200
    assert readiness.json()["ready"] is False
    failing = [c["key"] for c in readiness.json()["checks"] if not c["passed"]]
    assert failing == ["no_high_findings"]

    # The UI can't bypass the gate: the server refuses with the failing checks.
    assert merge.status_code == 409
    assert "checks" in merge.json()["detail"]

    assert published.status_code == 200
    assert published.json()["publication"]["suggested_changes"] == 1


def test_unknown_review_is_404(app: FastAPI) -> None:
    [response] = _call(app, [("GET", "/api/v1/reviews/nope", None)])
    assert response.status_code == 404


def test_agents_endpoint_and_subset(app: FastAPI) -> None:
    [agents] = _call(app, [("GET", "/api/v1/agents", None)])
    subset, unknown = _call(
        app,
        [
            (
                "POST",
                "/api/v1/reviews",
                {"pr_url": "https://github.com/o/r/pull/7", "agents": ["bug"]},
            ),
            (
                "POST",
                "/api/v1/reviews",
                {"pr_url": "https://github.com/o/r/pull/7", "agents": ["x"]},
            ),
        ],
    )
    assert [a["name"] for a in agents.json()][:2] == ["bug", "security"]
    assert subset.status_code == 201
    assert app.state.stub.requested_agents == ["bug"]
    assert unknown.status_code == 400


@pytest.mark.parametrize(
    ("exc", "status", "retry_after"),
    [
        (PRNotFoundError("missing"), 404, None),
        (GitHubAuthError("bad token"), 502, None),
        (UpstreamRateLimitError("slow down", retry_after=17), 429, "17"),
        (UpstreamError("GitHub 503 body: internal secrets"), 502, None),
        (LLMUnavailableError("groq said: internal secrets"), 502, None),
        (RuntimeError("boom with internal secrets"), 500, None),
    ],
)
def test_errors_map_to_status_codes_without_leaking(
    app: FastAPI, exc: Exception, status: int, retry_after: str | None
) -> None:
    app.state.stub.fail_with = exc
    [response] = _call(
        app, [("POST", "/api/v1/reviews", {"pr_url": "https://github.com/o/r/pull/7"})]
    )
    assert response.status_code == status
    assert "internal secrets" not in response.text
    assert response.headers.get("Retry-After") == retry_after
