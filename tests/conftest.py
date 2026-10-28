"""Shared test fixtures and factories."""

from typing import Any

from app.graph.state import ReviewState
from app.models.review_file import ReviewFile
from app.models.review_request import ReviewRequest


def make_review_request(**overrides: Any) -> ReviewRequest:
    """Build a ReviewRequest with sensible defaults."""
    defaults: dict[str, Any] = {
        "title": "Test pull request",
        "description": "A test pull request.",
        "files": [
            ReviewFile(
                filename="app/example.py",
                status="modified",
                patch="@@ -1,2 +1,3 @@\n+import os\n",
                language="Python",
                additions=1,
                deletions=0,
                changes=1,
            )
        ],
        "owner": "test-owner",
        "repo": "test-repo",
        "pull_number": 1,
        "head_sha": "abc1234",
    }
    return ReviewRequest(**{**defaults, **overrides})


def make_review_state(**overrides: Any) -> ReviewState:
    """Build a complete ReviewState so nodes can be called in isolation."""
    request = make_review_request()
    state: ReviewState = {
        "request": request,
        "files": list(request.files),
        "agents": [],
        "findings": [],
        "agent_runs": [],
        "errors": [],
        "merged_comments": [],
        "summary": "",
        "metadata": {},
    }
    state.update(overrides)  # type: ignore[typeddict-item]  # test-only partial override
    return state
