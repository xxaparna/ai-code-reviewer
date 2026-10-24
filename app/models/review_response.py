from dataclasses import dataclass, field
from typing import Any

from app.models.review_comment import ReviewComment


@dataclass(slots=True)
class ReviewResponse:
    """
    Standard output from every reviewer.
    """

    summary: str
    comments: list[ReviewComment] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
