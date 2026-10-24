"""User-initiated write actions on a reviewed pull request: publish and merge."""

from datetime import UTC, datetime

import structlog

from app.errors import (
    AlreadyPublishedError,
    GitHubRejectedError,
    MergeBlockedError,
    RepoAccessDeniedError,
)
from app.models.enums import Severity
from app.services.github_service import GitHubService
from app.services.merge_gate import MergeReadiness, evaluate_merge_gate
from app.services.pr_publisher import build_review_payload
from app.services.review_store import MergeRecord, Publication, ReviewRecord, ReviewStore
from app.settings import get_settings

logger = structlog.get_logger(__name__)


class PullRequestActions:
    def __init__(self, github_service: GitHubService, store: ReviewStore) -> None:
        self.github = github_service
        self.store = store

    # --- publishing ------------------------------------------------------

    def publish(
        self, review_id: str, min_severity: Severity, *, force: bool = False
    ) -> ReviewRecord:
        """Post findings at or above ``min_severity`` as one COMMENT review.

        Never APPROVE or REQUEST_CHANGES: an LLM should not block or approve a
        merge on its own (PLAN.md §6.7).
        """
        record = self.store.get(review_id)
        with record.lock:
            if record.publication is not None and not force:
                raise AlreadyPublishedError(
                    "This review was already posted to the pull request. "
                    "Pass force=true to post it again."
                )

            req = record.request
            payload = build_review_payload(
                record.response.comments, req.files, record.response.summary, min_severity
            )
            try:
                result = self.github.create_pull_request_review(
                    req.owner,
                    req.repo,
                    req.pull_number,
                    body=payload.body,
                    event="COMMENT",
                    comments=payload.comments,
                    commit_id=req.head_sha or None,
                )
            except GitHubRejectedError as exc:
                if exc.status_code != 422 or not payload.comments:
                    raise
                # GitHub rejected an inline anchor. Rather than lose the review,
                # post every finding in the body.
                logger.warning("publish.inline_rejected_fallback", review_id=review_id)
                payload = build_review_payload(
                    record.response.comments,
                    req.files,
                    record.response.summary,
                    min_severity,
                    inline=False,
                )
                result = self.github.create_pull_request_review(
                    req.owner,
                    req.repo,
                    req.pull_number,
                    body=payload.body,
                    event="COMMENT",
                    commit_id=req.head_sha or None,
                )

            record.publication = Publication(
                published_at=datetime.now(UTC),
                html_url=str(result.get("html_url", req.html_url)),
                inline_comments=len(payload.comments),
                suggested_changes=payload.suggested_changes,
                body_findings=payload.body_findings,
                min_severity=min_severity.value,
            )
            logger.info(
                "publish.completed",
                review_id=review_id,
                inline=len(payload.comments),
                suggestions=payload.suggested_changes,
            )
            return record

    # --- merging ---------------------------------------------------------

    def readiness(self, review_id: str) -> MergeReadiness:
        return self._evaluate(self.store.get(review_id))

    def merge(self, review_id: str, *, commit_title: str | None = None) -> ReviewRecord:
        """Re-run the gate on fresh GitHub data, then merge pinned to the reviewed SHA."""
        record = self.store.get(review_id)
        with record.lock:
            readiness = self._evaluate(record)
            if not readiness.ready:
                raise MergeBlockedError(
                    "Merge blocked: " + "; ".join(c.detail for c in readiness.failing),
                    [c.as_dict() for c in readiness.checks],
                )

            settings = get_settings()
            req = record.request
            result = self.github.merge_pull_request(
                req.owner,
                req.repo,
                req.pull_number,
                sha=req.head_sha,
                merge_method=settings.merge_method,
                commit_title=commit_title,
            )
            record.merge = MergeRecord(
                merged_at=datetime.now(UTC),
                sha=str(result.get("sha", "")),
                merge_method=settings.merge_method,
            )
            logger.info("merge.completed", review_id=review_id, sha=record.merge.sha)
            return record

    def _evaluate(self, record: ReviewRecord) -> MergeReadiness:
        req = record.request
        pull_request = self.github.get_pull_request(req.owner, req.repo, req.pull_number)
        head = pull_request.get("head")
        current_sha = head.get("sha", "") if isinstance(head, dict) else ""
        try:
            check_runs = self.github.get_check_runs(req.owner, req.repo, current_sha)
            combined = self.github.get_combined_status(req.owner, req.repo, current_sha)
        except RepoAccessDeniedError:
            # Token can read the PR but not CI. Fail closed with a clear reason.
            check_runs = [
                {
                    "name": "token cannot read CI checks",
                    "status": "completed",
                    "conclusion": "unknown",
                }
            ]
            combined = {}
        return evaluate_merge_gate(
            record=record,
            pull_request=pull_request,
            check_runs=check_runs,
            combined_status=combined,
            merge_enabled=get_settings().merge_enabled,
        )
