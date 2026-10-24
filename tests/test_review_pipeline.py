"""End-to-end pipeline tests: real LangGraph, fake LLM, no network.

This is the first test that actually executes the compiled graph. It is the
safety net Phase 1 and Phase 2 refactors need.
"""

import app.graph.nodes as nodes_module
import app.graph.summary as summary_module
import pytest
from app.services.review_service import ReviewService

from tests.fakes import FakeGitHubService, FakeLLMClient

PR_URL = "https://github.com/test-owner/test-repo/pull/7"
REVIEWER_AGENT_COUNT = 5


@pytest.fixture
def fake_llm(monkeypatch: pytest.MonkeyPatch) -> FakeLLMClient:
    """Inject a fake LLM into the module-level reviewer and summary nodes.

    The nodes hold a shared BaseNode singleton; Phase 2 replaces that with an
    injected agent registry, at which point this fixture gets simpler.
    """
    client = FakeLLMClient()
    monkeypatch.setattr(nodes_module._node, "_llm", client)
    monkeypatch.setattr(summary_module._node, "_llm", client)
    return client


@pytest.fixture
def failing_llm(monkeypatch: pytest.MonkeyPatch) -> FakeLLMClient:
    client = FakeLLMClient(fail_agents={"Security Engineer"})
    monkeypatch.setattr(nodes_module._node, "_llm", client)
    monkeypatch.setattr(summary_module._node, "_llm", client)
    return client


def test_full_review_produces_merged_sorted_findings(fake_llm: FakeLLMClient) -> None:
    github = FakeGitHubService()
    service = ReviewService(github_service=github)  # type: ignore[arg-type]

    review = service.review_pr_url(PR_URL)

    # Every reviewer agent ran, plus the summary node.
    assert len(fake_llm.calls) == REVIEWER_AGENT_COUNT + 1

    assert len(review.comments) == 5

    # Sorted by severity, then file, then line (line-less findings first).
    assert [c.severity for c in review.comments] == ["HIGH", "HIGH", "MEDIUM", "MEDIUM", "LOW"]
    assert [c.category for c in review.comments] == [
        "BUG",
        "SECURITY",
        "ARCHITECTURE",
        "SECURITY",
        "STYLE",
    ]

    assert review.summary == "One high-severity bug and two minor issues."
    assert review.metadata["complete"] is True
    assert review.metadata["total_files"] == 1
    assert review.metadata["total_comments"] == 5
    assert review.metadata["severity_counts"] == {"HIGH": 2, "MEDIUM": 2, "LOW": 1}
    assert review.metadata["errors"] == []


def test_cross_agent_duplicates_survive_dedup(fake_llm: FakeLLMClient) -> None:
    """Documents the Phase 2.7 gap: dedup is keyed on category as well.

    The bug agent and the security agent both report "Unchecked None
    dereference." on app/example.py:12. Because merge.py keys on
    (file, line, category, message), the identical finding is reported twice.
    Two agents describing the same issue in *different words* would also both
    survive. When fuzzy dedup lands, this becomes one finding and this test
    should be rewritten to assert the collapse.
    """
    service = ReviewService(github_service=FakeGitHubService())  # type: ignore[arg-type]

    review = service.review_pr_url(PR_URL)
    line_12 = [c for c in review.comments if c.line == 12]

    assert len(line_12) == 2
    assert {c.message for c in line_12} == {"Unchecked None dereference."}
    assert {c.category for c in line_12} == {"BUG", "SECURITY"}


def test_fenced_and_prose_wrapped_json_are_both_parsed(fake_llm: FakeLLMClient) -> None:
    """The style agent replies in a ```json fence, the architect in prose."""
    service = ReviewService(github_service=FakeGitHubService())  # type: ignore[arg-type]

    review = service.review_pr_url(PR_URL)
    categories = {c.category for c in review.comments}

    assert "STYLE" in categories  # came from a fenced block
    assert "ARCHITECTURE" in categories  # came from a bare array inside prose


def test_one_failing_agent_degrades_instead_of_crashing(failing_llm: FakeLLMClient) -> None:
    """A partial review must be reported as partial, never as clean."""
    service = ReviewService(github_service=FakeGitHubService())  # type: ignore[arg-type]

    review = service.review_pr_url(PR_URL)

    assert review.metadata["complete"] is False
    assert len(review.metadata["errors"]) == 1
    assert "security_comments" in review.metadata["errors"][0]

    # The surviving agents still reported.
    assert len(review.comments) > 0
    assert not any(c.category == "SECURITY" for c in review.comments)


def test_publishing_is_opt_in(fake_llm: FakeLLMClient) -> None:
    github = FakeGitHubService()
    service = ReviewService(github_service=github)  # type: ignore[arg-type]

    service.review_pr_url(PR_URL, publish_to_github=False)
    assert github.published == []

    service.review_pr_url(PR_URL, publish_to_github=True)
    assert len(github.published) == 1
    assert github.published[0]["event"] == "COMMENT"
    assert "AI Code Review" in github.published[0]["body"]
    assert github.published[0]["commit_id"] == "deadbeef"


def test_file_level_findings_are_published_in_the_review_body(fake_llm: FakeLLMClient) -> None:
    """A finding with no line cannot be anchored inline, so it goes in the body."""
    github = FakeGitHubService()
    service = ReviewService(github_service=github)  # type: ignore[arg-type]

    service.review_pr_url(PR_URL, publish_to_github=True)

    published = github.published[0]
    assert published["comments"] == []
    assert "Service reaches into the data layer." in published["body"]


def test_every_agent_receives_the_whole_diff(fake_llm: FakeLLMClient) -> None:
    """Documents the Phase 2 bug: no per-file fan-out.

    Each agent is handed every file in one prompt. Rewrite this test when the
    Send-based fan-out lands.
    """
    github = FakeGitHubService(
        files=[
            {
                "filename": f"app/mod_{i}.py",
                "status": "modified",
                "patch": f"@@ -1 +1,2 @@\n+x = {i}\n",
                "additions": 1,
                "deletions": 0,
                "changes": 1,
            }
            for i in range(3)
        ]
    )
    service = ReviewService(github_service=github)  # type: ignore[arg-type]

    service.review_pr_url(PR_URL)

    reviewer_calls = [c for c in fake_llm.calls if c["agent"] != "Principal Software Engineer"]
    assert len(reviewer_calls) == REVIEWER_AGENT_COUNT  # not 5 x 3 files

    for call in reviewer_calls:
        for i in range(3):
            assert f"app/mod_{i}.py" in call["user_prompt"]


def test_empty_pull_request_still_calls_every_agent(fake_llm: FakeLLMClient) -> None:
    """Documents a missing guard, found against psf/requests#7587 (0 files).

    A pull request with no reviewable files should short-circuit before any LLM
    call. Today it does not: all six calls are made against a prompt whose
    "Changed Files" section is empty, and the result still claims to be a
    complete review. Rewrite this test when the guard lands (Phase 1.10).
    """
    service = ReviewService(github_service=FakeGitHubService(files=[]))  # type: ignore[arg-type]

    review = service.review_pr_url(PR_URL)

    assert len(fake_llm.calls) == REVIEWER_AGENT_COUNT + 1  # should be 0
    assert review.metadata["total_files"] == 0
    assert review.metadata["complete"] is True  # should be False, or not a review at all

    # No file content reached the model, so any finding it returns is invented.
    for call in fake_llm.calls:
        assert "Diff:" not in call["user_prompt"]
