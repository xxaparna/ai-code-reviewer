import time
from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.schemas import (
    AgentSchema,
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
    GitHubAuthError,
    GitHubRejectedError,
    InvalidPRURLError,
    LLMUnavailableError,
    MergeBlockedError,
    PRNotFoundError,
    RepoAccessDeniedError,
    ReviewNotFoundError,
    UnknownAgentError,
    UpstreamError,
    UpstreamRateLimitError,
)
from app.models.enums import Severity
from app.services.pr_actions_service import PullRequestActions
from app.services.review_service import ReviewService
from app.services.review_store import ReviewRecord, ReviewStore
from app.settings import get_settings

router = APIRouter(prefix="/api/v1", tags=["review"])


def get_review_service(request: Request) -> ReviewService:
    """The service built once by the application lifespan (plan 1.9).

    Tests replace it through ``app.dependency_overrides``.
    """
    service: ReviewService = request.app.state.review_service
    return service


def get_review_store(request: Request) -> ReviewStore:
    store: ReviewStore = request.app.state.review_store
    return store


def get_pr_actions(
    service: Annotated[ReviewService, Depends(get_review_service)],
    store: Annotated[ReviewStore, Depends(get_review_store)],
) -> PullRequestActions:
    return PullRequestActions(service.github_service, store)


def _raise_http(exc: Exception) -> NoReturn:
    """Map domain exceptions to HTTP errors without leaking upstream bodies."""
    if isinstance(exc, InvalidPRURLError | UnknownAgentError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if isinstance(exc, GitHubAuthError):
        # The server's own token is bad: not the caller's fault.
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, detail="The server's GitHub credentials were rejected."
        ) from exc
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
        # Headers on the injected Response are dropped when an HTTPException is
        # raised, so Retry-After must travel on the exception itself.
        headers = (
            {"Retry-After": str(max(1, round(exc.retry_after)))}
            if exc.retry_after is not None
            else None
        )
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded. Please try again later.",
            headers=headers,
        ) from exc
    if isinstance(exc, LLMUnavailableError):
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, detail="LLM service is currently unavailable."
        ) from exc
    if isinstance(exc, UpstreamError):
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, detail="GitHub is currently unavailable."
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
) -> ReviewPRResponse:
    try:
        review_response = service.review_pr_url(
            pr_url=payload.pr_url,
            publish_to_github=payload.publish_to_github,
            agents=payload.agents,
        )
    except Exception as exc:
        _raise_http(exc)

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
                agent=c.agent,
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
                agent=c.agent,
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
) -> ReviewDetail:
    """Run a review and keep it, so it can be published or merged later.

    Blocking for now; Phase 5 turns this into 202 + a job id.
    """
    started = time.perf_counter()
    try:
        request = service.fetch_request(payload.pr_url)
        result = service.review(request, agents=payload.agents)
    except Exception as exc:
        _raise_http(exc)
    duration_ms = int((time.perf_counter() - started) * 1000)
    return _detail(store.add(payload.pr_url, request, result, duration_ms))


@router.get("/agents", response_model=list[AgentSchema])
def list_agents(
    service: Annotated[ReviewService, Depends(get_review_service)],
) -> list[AgentSchema]:
    """The reviewer agents in the registry; pass a subset as ``agents`` on a review."""
    return [
        AgentSchema(
            name=a.name,
            category=a.category.value,
            model_tier=a.model_tier,
            enabled=a.enabled,
            severity_floor=a.severity_floor.value,
            max_findings_per_file=a.max_findings_per_file,
            prompt_version=a.prompt_version,
        )
        for a in service.registry
    ]


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
) -> ReviewDetail:
    try:
        return _detail(store.get(review_id))
    except Exception as exc:
        _raise_http(exc)


@router.post("/reviews/{review_id}/publish", response_model=ReviewDetail)
def publish_review(
    review_id: str,
    payload: PublishRequest,
    actions: Annotated[PullRequestActions, Depends(get_pr_actions)],
) -> ReviewDetail:
    """Post findings to the PR as inline comments with GitHub suggested changes."""
    try:
        record = actions.publish(review_id, Severity(payload.min_severity), force=payload.force)
    except Exception as exc:
        _raise_http(exc)
    return _detail(record)


@router.get("/reviews/{review_id}/merge-readiness", response_model=MergeReadinessSchema)
def merge_readiness(
    review_id: str,
    actions: Annotated[PullRequestActions, Depends(get_pr_actions)],
) -> MergeReadinessSchema:
    try:
        readiness = actions.readiness(review_id)
    except Exception as exc:
        _raise_http(exc)
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
) -> ReviewDetail:
    """Merge the PR, if and only if every gate check passes on fresh GitHub data."""
    try:
        record = actions.merge(review_id, commit_title=payload.commit_title)
    except Exception as exc:
        _raise_http(exc)
    return _detail(record)
