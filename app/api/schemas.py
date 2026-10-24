from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class ReviewPRRequest(BaseModel):
    pr_url: str = Field(
        ..., description="GitHub Pull Request URL (e.g., https://github.com/owner/repo/pull/1)"
    )
    publish_to_github: bool = Field(
        False, description="Whether to publish inline comments and review to GitHub"
    )


class ReviewCommentSchema(BaseModel):
    file: str
    line: int | None = None
    severity: str
    category: str
    message: str
    suggestion: str | None = None


class ReviewPRResponse(BaseModel):
    summary: str
    comments: list[ReviewCommentSchema]
    metadata: dict[str, Any] = Field(default_factory=dict)


# --- review sessions (frontend) ----------------------------------------------

SeverityLiteral = Literal["LOW", "MEDIUM", "HIGH"]


class CreateReviewRequest(BaseModel):
    pr_url: str = Field(
        ..., description="GitHub Pull Request URL (e.g., https://github.com/owner/repo/pull/1)"
    )


class FindingSchema(BaseModel):
    id: str = Field(description="Stable within one review: the finding's index.")
    file: str
    line: int | None = None
    start_line: int | None = None
    severity: SeverityLiteral
    category: str
    message: str
    suggestion: str | None = None
    suggested_code: str | None = Field(
        None, description="Exact replacement for lines start_line..line; posted as a suggestion."
    )
    dedup_collapsed_count: int = 1


class ChangedFileSchema(BaseModel):
    filename: str
    status: str
    language: str
    additions: int
    deletions: int
    patch: str


class PullRequestSchema(BaseModel):
    owner: str
    repo: str
    number: int
    title: str
    url: str
    author: str
    head_ref: str
    base_ref: str
    head_sha: str


class PublicationSchema(BaseModel):
    published_at: datetime
    html_url: str
    inline_comments: int
    suggested_changes: int
    body_findings: int
    min_severity: SeverityLiteral


class MergeSchema(BaseModel):
    merged_at: datetime
    sha: str
    merge_method: str


class ReviewDetail(BaseModel):
    id: str
    created_at: datetime
    duration_ms: int
    pull_request: PullRequestSchema
    summary: str
    findings: list[FindingSchema]
    files: list[ChangedFileSchema]
    metadata: dict[str, Any] = Field(default_factory=dict)
    publication: PublicationSchema | None = None
    merge: MergeSchema | None = None


class ReviewListItem(BaseModel):
    id: str
    created_at: datetime
    duration_ms: int
    pull_request: PullRequestSchema
    severity_counts: dict[str, int]
    complete: bool
    published: bool
    merged: bool


class PublishRequest(BaseModel):
    min_severity: SeverityLiteral = Field(
        "MEDIUM", description="Lowest severity to post. Lower findings are not posted at all."
    )
    force: bool = Field(False, description="Post again even if already published.")


class GateCheckSchema(BaseModel):
    key: str
    label: str
    passed: bool
    detail: str


class MergeReadinessSchema(BaseModel):
    ready: bool
    reviewed_sha: str
    current_sha: str
    merge_method: str
    checks: list[GateCheckSchema]


class MergeRequest(BaseModel):
    commit_title: str | None = Field(None, max_length=256)
