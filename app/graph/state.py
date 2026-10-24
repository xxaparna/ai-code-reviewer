import operator
from typing import Annotated, Any, TypedDict

from app.models.review_comment import ReviewComment
from app.models.review_file import ReviewFile
from app.models.review_request import ReviewRequest


class ReviewState(TypedDict):
    request: ReviewRequest
    current_file: ReviewFile | None
    bug_comments: list[ReviewComment]
    security_comments: list[ReviewComment]
    style_comments: list[ReviewComment]
    performance_comments: list[ReviewComment]
    architecture_comments: list[ReviewComment]
    merged_comments: list[ReviewComment]

    summary: str

    # Appended to by every node; a non-empty list means the review is incomplete.
    errors: Annotated[list[str], operator.add]

    metadata: dict[str, Any]
