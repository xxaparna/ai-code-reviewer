"""End-to-end pipeline tests: real LangGraph, fake LLM, no network."""

from typing import Any

from app.agents.registry import default_registry
from app.errors import UpstreamRateLimitError
from app.review_config import ReviewConfig
from app.services.review_service import ReviewService

from tests.fakes import FakeGitHubService, FakeLLMClient

PR_URL = "https://github.com/test-owner/test-repo/pull/7"
REVIEWER_AGENT_COUNT = len(default_registry().enabled)
SUMMARY = "Principal Software Engineer"


def _service(llm: FakeLLMClient, **github: Any) -> tuple[ReviewService, FakeGitHubService]:
    gh = FakeGitHubService(**github)
    return ReviewService(gh, llm=llm), gh  # type: ignore[arg-type]


def _file(name: str, patch: str = "@@ -1 +1,2 @@\n+x = 1\n+y = 2\n") -> dict[str, Any]:
    return {"filename": name, "status": "modified", "patch": patch}


def test_full_review_produces_merged_sorted_findings() -> None:
    llm = FakeLLMClient()
    review = _service(llm)[0].review_pr_url(PR_URL)

    # Severity first; within MEDIUM the file-level finding (no line) sorts first.
    assert [(c.severity, c.category, c.line) for c in review.comments] == [
        ("HIGH", "BUG", 12),
        ("MEDIUM", "ARCHITECTURE", None),
        ("MEDIUM", "SECURITY", 30),
    ]
    assert review.summary == "One high-severity bug and two minor issues."
    meta = review.metadata
    assert meta["complete"] is True
    assert meta["partial"] is False
    assert meta["total_files"] == meta["reviewed_files"] == 1
    assert meta["severity_counts"] == {"HIGH": 1, "MEDIUM": 2}
    assert meta["errors"] == []


def test_cross_agent_duplicates_are_collapsed() -> None:
    """Bug and security both report line 12: one finding survives, counting both."""
    review = _service(FakeLLMClient())[0].review_pr_url(PR_URL)

    line_12 = [c for c in review.comments if c.line == 12]
    assert len(line_12) == 1
    assert line_12[0].dedup_collapsed_count == 2
    assert review.metadata["duplicates_collapsed"] == 1
    assert review.metadata["raw_findings"] == 4


def test_differently_worded_duplicates_within_the_line_window_collapse() -> None:
    llm = FakeLLMClient(
        responses={
            "Senior Software Engineer": '{"comments": [{"line": 10, "severity": "HIGH", '
            '"category": "BUG", "message": "SQL query built by string concatenation"}]}',
            "Security Engineer": '{"comments": [{"line": 12, "severity": "HIGH", '
            '"category": "SECURITY", "message": "SQL query is built by string '
            'concatenation, allowing SQL injection"}]}',
            "Senior Code Reviewer": '{"comments": []}',
            "Software Architect": '{"comments": []}',
        }
    )
    review = _service(llm)[0].review_pr_url(PR_URL)
    assert len(review.comments) == 1
    assert review.comments[0].dedup_collapsed_count == 2


def test_style_agent_severity_floor_drops_low_findings() -> None:
    review = _service(FakeLLMClient())[0].review_pr_url(PR_URL)

    assert not any(c.category == "STYLE" for c in review.comments)
    assert review.metadata["below_severity_floor_dropped"] == 1
    assert review.metadata["agent_stats"]["style"]["dropped"] == 1


def test_structured_output_first_tolerant_fallback_second() -> None:
    """The style agent replies in a ```json fence, the architect in prose."""
    llm = FakeLLMClient()
    review = _service(llm)[0].review_pr_url(PR_URL)

    fallbacks = [c["agent"] for c in llm.calls if c["mode"] == "text" and c["agent"] != SUMMARY]
    assert sorted(fallbacks) == ["Senior Code Reviewer", "Software Architect"]
    assert review.metadata["structured_output_fallbacks"] == 2
    assert "ARCHITECTURE" in {c.category for c in review.comments}  # recovered from prose


def test_fallback_can_be_disabled() -> None:
    llm = FakeLLMClient()
    service = ReviewService(
        FakeGitHubService(),  # type: ignore[arg-type]
        llm=llm,
        config=ReviewConfig(tolerant_fallback=False),
    )
    review = service.review_pr_url(PR_URL)

    assert review.metadata["complete"] is False
    assert {e.split(":")[0] for e in review.metadata["errors"]} == {"style", "architecture"}


def test_one_failing_agent_degrades_instead_of_crashing() -> None:
    """A partial review must be reported as partial, never as clean."""
    llm = FakeLLMClient(fail_agents={"Security Engineer"})
    review = _service(llm)[0].review_pr_url(PR_URL)

    assert review.metadata["complete"] is False
    assert review.metadata["partial"] is True
    assert len(review.metadata["errors"]) == 1
    assert review.metadata["errors"][0].startswith("security: app/example.py:")
    assert review.metadata["agent_stats"]["security"]["failed"] == 1
    assert len(review.comments) > 0
    assert not any(c.agent == "security" for c in review.comments)


def test_rate_limited_agent_is_not_retried_as_text() -> None:
    llm = FakeLLMClient(
        fail_agents={"Security Engineer"}, fail_with=UpstreamRateLimitError("429", retry_after=5)
    )
    review = _service(llm)[0].review_pr_url(PR_URL)

    security_calls = [c for c in llm.calls if c["agent"] == "Security Engineer"]
    assert [c["mode"] for c in security_calls] == ["structured"]
    assert review.metadata["complete"] is False


def test_hallucinated_file_and_line_are_dropped() -> None:
    llm = FakeLLMClient(
        responses={
            "Senior Software Engineer": '{"comments": ['
            '{"file": "app/other.py", "line": 1, "severity": "HIGH", "category": "BUG", "message": "a"},'
            '{"file": "app/example.py", "line": 999, "severity": "HIGH", "category": "BUG", "message": "b"},'
            '{"file": "./app/example.py", "line": "3", "severity": "HIGH", "category": "BUG", "message": "c"}'
            "]}",
            "Security Engineer": '{"comments": []}',
            "Senior Code Reviewer": '{"comments": []}',
            "Software Architect": '{"comments": []}',
        }
    )
    review = _service(llm)[0].review_pr_url(PR_URL)

    assert [(c.file, c.line, c.message) for c in review.comments] == [("app/example.py", 3, "c")]
    assert review.metadata["hallucinated_comments_dropped"] == 2


def test_invalid_findings_are_rejected_individually() -> None:
    llm = FakeLLMClient(
        responses={
            "Senior Software Engineer": '{"comments": ['
            '{"line": 1, "severity": "URGENT", "category": "BUG", "message": "bad severity"},'
            '{"line": 2, "severity": "HIGH", "category": "VIBES", "message": "bad category"},'
            '{"line": 3, "severity": "HIGH", "category": "BUG", "message": "   "},'
            '{"line": 4, "severity": "critical", "category": "code smell", "message": "aliases ok"}'
            "]}",
            "Security Engineer": '{"comments": []}',
            "Senior Code Reviewer": '{"comments": []}',
            "Software Architect": '{"comments": []}',
        }
    )
    review = _service(llm)[0].review_pr_url(PR_URL)

    assert [(c.severity, c.category, c.message) for c in review.comments] == [
        ("HIGH", "STYLE", "aliases ok")
    ]
    assert review.metadata["invalid_findings_dropped"] == 3


def test_publishing_is_opt_in() -> None:
    service, github = _service(FakeLLMClient())

    service.review_pr_url(PR_URL, publish_to_github=False)
    assert github.published == []

    service.review_pr_url(PR_URL, publish_to_github=True)
    assert len(github.published) == 1
    assert github.published[0]["event"] == "COMMENT"
    assert "AI Code Review" in github.published[0]["body"]
    assert github.published[0]["commit_id"] == "deadbeef"


def test_file_level_findings_are_published_in_the_review_body() -> None:
    service, github = _service(FakeLLMClient())

    service.review_pr_url(PR_URL, publish_to_github=True)

    published = github.published[0]
    assert "Service reaches into the data layer." in published["body"]
    assert all(c["line"] in (12, 30) for c in published["comments"])


def test_empty_pull_request_makes_no_llm_calls() -> None:
    """psf/requests#7587 (0 files): short-circuit before any LLM call (plan 1.10)."""
    llm = FakeLLMClient()
    review = _service(llm, files=[])[0].review_pr_url(PR_URL)

    assert llm.calls == []
    assert review.comments == []
    assert review.metadata["unreviewable"] is True
    assert review.metadata["total_files"] == 0


def test_pull_request_of_only_filtered_files_makes_no_llm_calls() -> None:
    llm = FakeLLMClient()
    files = [
        _file("package-lock.json"),
        _file("vendor/lib.go"),
        _file("static/app.min.js"),
        {"filename": "logo.png", "status": "added"},
    ]
    review = _service(llm, files=files)[0].review_pr_url(PR_URL)

    assert llm.calls == []
    assert review.metadata["unreviewable"] is True
    assert {s["file"] for s in review.metadata["skipped_files"]} == {f["filename"] for f in files}


def test_client_can_request_a_subset_of_agents() -> None:
    llm = FakeLLMClient()
    review = _service(llm)[0].review_pr_url(PR_URL, agents=["security"])

    assert {c["agent"] for c in llm.calls} == {"Security Engineer", SUMMARY}
    assert review.metadata["agents"] == ["security"]
    assert {c.agent for c in review.comments} == {"security"}
