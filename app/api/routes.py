import time
from functools import lru_cache
from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.api.schemas import (
    ChangedFileSchema,
    CreateReviewRequest,
    FindingSchema,
    GateCheckSchema,
    MergeReadinessSchema,
    MergeRequest,
    MergeSchema,
    PublicationSchema,
    PublishRequest,
    PullRequestSchema,
    ReviewCommentSchema,
    ReviewDetail,
    ReviewListItem,
    ReviewPRRequest,
    ReviewPRResponse,
)
from app.errors import (
    AlreadyPublishedError,
    CodeReviewerError,
    GitHubRejectedError,
    InvalidPRURLError,
    LLMUnavailableError,
    MergeBlockedError,
    PRNotFoundError,
    RepoAccessDeniedError,
    ReviewNotFoundError,
    UpstreamRateLimitError,
)
from app.models.enums import Severity
from app.services.pr_actions_service import PullRequestActions
from app.services.review_service import ReviewService
from app.services.review_store import ReviewRecord, ReviewStore
from app.settings import get_settings

router = APIRouter(prefix="/api/v1", tags=["review"])


@lru_cache(maxsize=1)
def get_review_service() -> ReviewService:
    """Build the review service on first use.

    Deferred rather than built at import time so the module stays importable
    without credentials. Phase 4 moves this onto the application lifespan.
    """
    return ReviewService()


@lru_cache(maxsize=1)
def get_review_store() -> ReviewStore:
    return ReviewStore()


def get_pr_actions(
    service: Annotated[ReviewService, Depends(get_review_service)],
    store: Annotated[ReviewStore, Depends(get_review_store)],
) -> PullRequestActions:
    return PullRequestActions(service.github_service, store)


def _raise_http(exc: Exception, response: Response) -> NoReturn:
    """Map domain exceptions to HTTP errors without leaking upstream bodies."""
    if isinstance(exc, InvalidPRURLError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if isinstance(exc, (PRNotFoundError, ReviewNotFoundError)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if isinstance(exc, RepoAccessDeniedError):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    if isinstance(exc, MergeBlockedError):
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail={"message": exc.message, "checks": exc.checks}
        ) from exc
    if isinstance(exc, (AlreadyPublishedError, GitHubRejectedError)):
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if isinstance(exc, UpstreamRateLimitError):
        if exc.retry_after is not None:
            response.headers["Retry-After"] = str(int(exc.retry_after))
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded. Please try again later.",
        ) from exc
    if isinstance(exc, LLMUnavailableError):
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, detail="LLM service is currently unavailable."
        ) from exc
    if isinstance(exc, (CodeReviewerError, ValueError)):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    raise HTTPException(
        status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to process review."
    ) from exc


@router.post("/review", response_model=ReviewPRResponse)
def review_pull_request(
    payload: ReviewPRRequest,
    service: Annotated[ReviewService, Depends(get_review_service)],
    response: Response,
) -> ReviewPRResponse:
    try:
        review_response = service.review_pr_url(
            pr_url=payload.pr_url,
            publish_to_github=payload.publish_to_github,
        )
    except Exception as exc:
        _raise_http(exc, response)

    return ReviewPRResponse(
        summary=review_response.summary,
        comments=[
            ReviewCommentSchema(
                file=c.file,
                line=c.line,
                severity=c.severity,
                category=c.category,
                message=c.message,
                suggestion=c.suggestion,
            )
            for c in review_response.comments
        ],
        metadata=review_response.metadata,
    )


# --- review sessions: the frontend's API --------------------------------------


def _pull_request_schema(record: ReviewRecord) -> PullRequestSchema:
    req = record.request
    return PullRequestSchema(
        owner=req.owner,
        repo=req.repo,
        number=req.pull_number,
        title=req.title,
        url=req.html_url or record.pr_url,
        author=req.author,
        head_ref=req.head_ref,
        base_ref=req.base_ref,
        head_sha=req.head_sha,
    )


def _detail(record: ReviewRecord) -> ReviewDetail:
    pub = record.publication
    merge = record.merge
    return ReviewDetail(
        id=record.id,
        created_at=record.created_at,
        duration_ms=record.duration_ms,
        pull_request=_pull_request_schema(record),
        summary=record.response.summary,
        findings=[
            FindingSchema(
                id=str(i),
                file=c.file,
                line=c.line,
                start_line=c.start_line,
                severity=c.severity.value,
                category=c.category.value,
                message=c.message,
                suggestion=c.suggestion,
                suggested_code=c.suggested_code,
                dedup_collapsed_count=c.dedup_collapsed_count,
            )
            for i, c in enumerate(record.response.comments)
        ],
        files=[
            ChangedFileSchema(
                filename=f.filename,
                status=f.status,
                language=f.language,
                additions=f.additions,
                deletions=f.deletions,
                patch=f.patch,
            )
            for f in record.request.files
        ],
        metadata=record.response.metadata,
        publication=PublicationSchema(
            published_at=pub.published_at,
            html_url=pub.html_url,
            inline_comments=pub.inline_comments,
            suggested_changes=pub.suggested_changes,
            body_findings=pub.body_findings,
            min_severity=pub.min_severity,  # type: ignore[arg-type]  # validated Severity value
        )
        if pub
        else None,
        merge=MergeSchema(merged_at=merge.merged_at, sha=merge.sha, merge_method=merge.merge_method)
        if merge
        else None,
    )


@router.post("/reviews", response_model=ReviewDetail, status_code=status.HTTP_201_CREATED)
def create_review(
    payload: CreateReviewRequest,
    service: Annotated[ReviewService, Depends(get_review_service)],
    store: Annotated[ReviewStore, Depends(get_review_store)],
    response: Response,
) -> ReviewDetail:
    """Run a review and keep it, so it can be published or merged later.

    Blocking for now; Phase 5 turns this into 202 + a job id.
    """
    started = time.perf_counter()
    try:
        request = service.fetch_request(payload.pr_url)
        result = service.review(request)
    except Exception as exc:
        _raise_http(exc, response)
    duration_ms = int((time.perf_counter() - started) * 1000)
    return _detail(store.add(payload.pr_url, request, result, duration_ms))


@router.get("/reviews", response_model=list[ReviewListItem])
def list_reviews(store: Annotated[ReviewStore, Depends(get_review_store)]) -> list[ReviewListItem]:
    return [
        ReviewListItem(
            id=r.id,
            created_at=r.created_at,
            duration_ms=r.duration_ms,
            pull_request=_pull_request_schema(r),
            severity_counts={
                str(k): int(v) for k, v in r.response.metadata.get("severity_counts", {}).items()
            },
            complete=bool(r.response.metadata.get("complete", False)),
            published=r.publication is not None,
            merged=r.merge is not None,
        )
        for r in store.list()
    ]


@router.get("/reviews/{review_id}", response_model=ReviewDetail)
def get_review(
    review_id: str,
    store: Annotated[ReviewStore, Depends(get_review_store)],
    response: Response,
) -> ReviewDetail:
    try:
        return _detail(store.get(review_id))
    except Exception as exc:
        _raise_http(exc, response)


@router.post("/reviews/{review_id}/publish", response_model=ReviewDetail)
def publish_review(
    review_id: str,
    payload: PublishRequest,
    actions: Annotated[PullRequestActions, Depends(get_pr_actions)],
    response: Response,
) -> ReviewDetail:
    """Post findings to the PR as inline comments with GitHub suggested changes."""
    try:
        record = actions.publish(review_id, Severity(payload.min_severity), force=payload.force)
    except Exception as exc:
        _raise_http(exc, response)
    return _detail(record)


@router.get("/reviews/{review_id}/merge-readiness", response_model=MergeReadinessSchema)
def merge_readiness(
    review_id: str,
    actions: Annotated[PullRequestActions, Depends(get_pr_actions)],
    response: Response,
) -> MergeReadinessSchema:
    try:
        readiness = actions.readiness(review_id)
    except Exception as exc:
        _raise_http(exc, response)
    return MergeReadinessSchema(
        ready=readiness.ready,
        reviewed_sha=readiness.reviewed_sha,
        current_sha=readiness.current_sha,
        merge_method=get_settings().merge_method,
        checks=[GateCheckSchema(**c.as_dict()) for c in readiness.checks],  # type: ignore[arg-type]
    )


@router.post("/reviews/{review_id}/merge", response_model=ReviewDetail)
def merge_pull_request(
    review_id: str,
    payload: MergeRequest,
    actions: Annotated[PullRequestActions, Depends(get_pr_actions)],
    response: Response,
) -> ReviewDetail:
    """Merge the PR, if and only if every gate check passes on fresh GitHub data."""
    try:
        record = actions.merge(review_id, commit_title=payload.commit_title)
    except Exception as exc:
        _raise_http(exc, response)
    return _detail(record)
