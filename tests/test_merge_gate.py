"""The safe-merge gate: every rule, in isolation, plus the publish/merge actions."""

from collections.abc import Iterator
from typing import Any

import pytest
from app.errors import AlreadyPublishedError, MergeBlockedError
from app.models.enums import Category, Severity
from app.models.review_comment import ReviewComment
from app.models.review_file import ReviewFile
from app.models.review_response import ReviewResponse
from app.services.merge_gate import evaluate_merge_gate
from app.services.pr_actions_service import PullRequestActions
from app.services.review_store import ReviewRecord, ReviewStore
from app.settings import get_settings

from tests.conftest import make_review_request
from tests.fakes import FakeGitHubService

PATCH = "@@ -1,2 +1,3 @@\n import os\n+import sys\n \n"


def _response(*severities: Severity, complete: bool = True) -> ReviewResponse:
    comments = [
        ReviewComment(
            file="app/example.py",
            line=2,
            severity=s,
            category=Category.BUG,
            message=f"{s} issue",
            suggested_code="import sys  # fixed",
        )
        for s in severities
    ]
    return ReviewResponse(
        summary="Summary.",
        comments=comments,
        metadata={"complete": complete, "errors": [] if complete else ["bug: boom"]},
    )


def _record(store: ReviewStore, response: ReviewResponse) -> ReviewRecord:
    request = make_review_request(
        head_sha="deadbeef",
        files=[ReviewFile(filename="app/example.py", status="modified", patch=PATCH)],
    )
    return store.add("https://github.com/o/r/pull/7", request, response, duration_ms=10)


def _pr(**overrides: Any) -> dict[str, Any]:
    return {
        "state": "open",
        "draft": False,
        "merged": False,
        "mergeable": True,
        "head": {"sha": "deadbeef"},
        **overrides,
    }


def _gate(
    record: ReviewRecord, *, pr: dict[str, Any] | None = None, **kwargs: Any
) -> dict[str, bool]:
    readiness = evaluate_merge_gate(
        record=record,
        pull_request=pr or _pr(),
        check_runs=kwargs.get("check_runs", []),
        combined_status=kwargs.get("combined_status", {"statuses": []}),
        merge_enabled=kwargs.get("merge_enabled", True),
    )
    return {c.key: c.passed for c in readiness.checks}


@pytest.fixture
def store() -> ReviewStore:
    return ReviewStore()


def test_clean_review_on_current_head_is_ready(store: ReviewStore) -> None:
    gate = _gate(_record(store, _response(Severity.MEDIUM, Severity.LOW)))
    assert all(gate.values())


def test_high_finding_blocks(store: ReviewStore) -> None:
    gate = _gate(_record(store, _response(Severity.HIGH)))
    assert gate["no_high_findings"] is False


def test_new_commits_since_review_block(store: ReviewStore) -> None:
    gate = _gate(_record(store, _response()), pr=_pr(head={"sha": "cafe1234"}))
    assert gate["review_current"] is False


def test_incomplete_review_blocks(store: ReviewStore) -> None:
    gate = _gate(_record(store, _response(complete=False)))
    assert gate["review_complete"] is False


@pytest.mark.parametrize(
    "pr",
    [_pr(state="closed"), _pr(draft=True), _pr(merged=True)],
    ids=["closed", "draft", "merged"],
)
def test_pr_must_be_open_and_not_draft(store: ReviewStore, pr: dict[str, Any]) -> None:
    assert _gate(_record(store, _response()), pr=pr)["pr_open"] is False


@pytest.mark.parametrize("mergeable", [False, None], ids=["conflicts", "still-computing"])
def test_conflicts_or_unknown_mergeability_block(
    store: ReviewStore, mergeable: bool | None
) -> None:
    assert _gate(_record(store, _response()), pr=_pr(mergeable=mergeable))["no_conflicts"] is False


@pytest.mark.parametrize(
    ("check_runs", "statuses"),
    [
        ([{"name": "test", "status": "completed", "conclusion": "failure"}], []),
        ([{"name": "test", "status": "in_progress", "conclusion": None}], []),
        ([], [{"context": "ci/jenkins", "state": "error"}]),
        ([], [{"context": "ci/jenkins", "state": "pending"}]),
    ],
    ids=["failed-run", "running", "status-error", "status-pending"],
)
def test_failing_or_pending_ci_blocks(
    store: ReviewStore, check_runs: list[dict[str, Any]], statuses: list[dict[str, Any]]
) -> None:
    gate = _gate(
        _record(store, _response()),
        check_runs=check_runs,
        combined_status={"statuses": statuses},
    )
    assert gate["ci_passing"] is False


def test_skipped_and_neutral_runs_pass(store: ReviewStore) -> None:
    runs = [
        {"name": "lint", "status": "completed", "conclusion": "success"},
        {"name": "docs", "status": "completed", "conclusion": "skipped"},
        {"name": "info", "status": "completed", "conclusion": "neutral"},
    ]
    assert _gate(_record(store, _response()), check_runs=runs)["ci_passing"] is True


def test_merging_disabled_on_server_blocks(store: ReviewStore) -> None:
    assert _gate(_record(store, _response()), merge_enabled=False)["merge_enabled"] is False


# --- actions ------------------------------------------------------------------


@pytest.fixture
def merge_enabled(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.setenv("MERGE_ENABLED", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.usefixtures("merge_enabled")
def test_merge_is_pinned_to_the_reviewed_sha(store: ReviewStore) -> None:
    github = FakeGitHubService()
    record = _record(store, _response(Severity.LOW))

    PullRequestActions(github, store).merge(record.id, commit_title="Title")  # type: ignore[arg-type]

    assert github.merged == [{"sha": "deadbeef", "merge_method": "squash", "commit_title": "Title"}]
    assert record.merge is not None


@pytest.mark.usefixtures("merge_enabled")
def test_merge_rechecks_the_gate_server_side(store: ReviewStore) -> None:
    github = FakeGitHubService()
    record = _record(store, _response(Severity.HIGH))

    with pytest.raises(MergeBlockedError) as excinfo:
        PullRequestActions(github, store).merge(record.id)  # type: ignore[arg-type]

    assert github.merged == []
    failing = [c["key"] for c in excinfo.value.checks if not c["passed"]]
    assert failing == ["no_high_findings"]


@pytest.mark.usefixtures("merge_enabled")
def test_publish_posts_once_then_refuses(store: ReviewStore) -> None:
    github = FakeGitHubService()
    record = _record(store, _response(Severity.HIGH))
    actions = PullRequestActions(github, store)  # type: ignore[arg-type]

    actions.publish(record.id, Severity.MEDIUM)

    [posted] = github.published
    assert posted["event"] == "COMMENT"
    assert posted["commit_id"] == "deadbeef"
    assert len(posted["comments"]) == 1
    assert record.publication is not None
    assert record.publication.suggested_changes == 1

    with pytest.raises(AlreadyPublishedError):
        actions.publish(record.id, Severity.MEDIUM)


@pytest.mark.usefixtures("merge_enabled")
def test_publish_falls_back_to_body_when_github_rejects_inline(store: ReviewStore) -> None:
    github = FakeGitHubService(reject_inline=True)
    record = _record(store, _response(Severity.HIGH))

    PullRequestActions(github, store).publish(record.id, Severity.LOW)  # type: ignore[arg-type]

    [posted] = github.published
    assert posted["comments"] is None
    assert "HIGH issue" in posted["body"]
