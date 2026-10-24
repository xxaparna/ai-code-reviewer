"""In-process store of completed reviews.

Publishing and merging act on a review the user has already seen, so the
server must keep it: the merge gate trusts the stored findings and reviewed
head SHA, never values echoed back by the browser.

This is a stopgap until Phase 5 moves reviews into Postgres. It is bounded,
thread-safe (sync routes run in FastAPI's threadpool), and lost on restart.
"""

import threading
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.errors import ReviewNotFoundError
from app.models.review_request import ReviewRequest
from app.models.review_response import ReviewResponse

DEFAULT_CAPACITY = 200


@dataclass(slots=True)
class Publication:
    published_at: datetime
    html_url: str
    inline_comments: int
    suggested_changes: int
    body_findings: int
    min_severity: str


@dataclass(slots=True)
class MergeRecord:
    merged_at: datetime
    sha: str
    merge_method: str


@dataclass(slots=True)
class ReviewRecord:
    id: str
    created_at: datetime
    pr_url: str
    request: ReviewRequest
    response: ReviewResponse
    duration_ms: int
    publication: Publication | None = None
    merge: MergeRecord | None = None
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


class ReviewStore:
    def __init__(self, capacity: int = DEFAULT_CAPACITY) -> None:
        self._capacity = capacity
        self._records: OrderedDict[str, ReviewRecord] = OrderedDict()
        self._lock = threading.Lock()

    def add(
        self, pr_url: str, request: ReviewRequest, response: ReviewResponse, duration_ms: int
    ) -> ReviewRecord:
        record = ReviewRecord(
            id=uuid.uuid4().hex,
            created_at=datetime.now(UTC),
            pr_url=pr_url,
            request=request,
            response=response,
            duration_ms=duration_ms,
        )
        with self._lock:
            self._records[record.id] = record
            while len(self._records) > self._capacity:
                self._records.popitem(last=False)
        return record

    def get(self, review_id: str) -> ReviewRecord:
        with self._lock:
            record = self._records.get(review_id)
        if record is None:
            raise ReviewNotFoundError(f"Review {review_id!r} not found.")
        return record

    def list(self) -> list[ReviewRecord]:
        """Newest first."""
        with self._lock:
            return list(reversed(self._records.values()))
