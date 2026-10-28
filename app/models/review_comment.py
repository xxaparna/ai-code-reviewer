"""Domain model for a code review comment / finding."""

from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.models.enums import Category, Severity

MAX_MESSAGE_LENGTH = 4000
MAX_SUGGESTION_LENGTH = 2000


def normalise_path(path: str) -> str:
    """``./app/x.py``, ``/app/x.py`` and ``app\\x.py`` all mean ``app/x.py``."""
    cleaned = path.strip().replace("\\", "/")
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    return cleaned.lstrip("/")


class ReviewComment(BaseModel):
    """A single validated finding.

    Validation is strict on purpose (plan 1.5): an unknown severity or
    category, an empty message, or a missing file rejects the finding.
    """

    file: str = Field(min_length=1)
    line: int | None = None
    severity: Severity
    category: Category
    message: str = Field(min_length=1)
    suggestion: str | None = None
    # Exact replacement for new-file lines ``start_line``..``line`` (or just
    # ``line`` when ``start_line`` is None). Posted as a GitHub suggested change.
    start_line: int | None = None
    suggested_code: str | None = None
    # Which reviewer agent produced it (the highest-severity one after dedup).
    agent: str | None = None
    dedup_collapsed_count: int = 1

    @field_validator("file", mode="before")
    @classmethod
    def clean_file(cls, v: object) -> str:
        return normalise_path(str(v)) if v is not None else ""

    @field_validator("line", "start_line", mode="before")
    @classmethod
    def coerce_line(cls, v: object) -> int | None:
        """Accept ints and digit strings; anything else ("around 15") becomes None."""
        if v is None or isinstance(v, bool):
            return None
        if isinstance(v, int):
            return v if v > 0 else None
        if isinstance(v, float) and v.is_integer():
            return int(v) if v > 0 else None
        if isinstance(v, str):
            v_str = v.strip()
            if v_str.isascii() and v_str.isdigit():
                val = int(v_str)
                return val if val > 0 else None
        return None

    @field_validator("severity", mode="before")
    @classmethod
    def validate_severity(cls, v: object) -> Severity:
        if isinstance(v, Severity):
            return v
        return Severity.from_str(None if v is None else str(v))

    @field_validator("category", mode="before")
    @classmethod
    def validate_category(cls, v: object) -> Category:
        if isinstance(v, Category):
            return v
        return Category.from_str(None if v is None else str(v))

    @field_validator("message", mode="before")
    @classmethod
    def truncate_message(cls, v: object) -> str:
        msg = str(v).strip() if v is not None else ""
        if len(msg) > MAX_MESSAGE_LENGTH:
            return msg[:MAX_MESSAGE_LENGTH] + "..."
        return msg

    @field_validator("suggestion", mode="before")
    @classmethod
    def clean_suggestion(cls, v: object) -> str | None:
        if v is None:
            return None
        text = str(v).strip()
        if not text:
            return None
        return text[:MAX_SUGGESTION_LENGTH]

    @field_validator("suggested_code", mode="before")
    @classmethod
    def normalise_suggested_code(cls, v: object) -> str | None:
        if v is None:
            return None
        code = str(v).strip("\n")
        return code if code.strip() else None


class FindingList(BaseModel):
    """A list of validated findings."""

    comments: list[ReviewComment] = Field(default_factory=list)


class RawFinding(BaseModel):
    """What the model is asked to produce. Deliberately loose.

    Structured output targets this schema rather than :class:`ReviewComment`, so
    one malformed item cannot fail the whole response: each item is validated
    into a :class:`ReviewComment` separately and only the bad ones are dropped.
    """

    file: str | None = None
    line: int | str | None = None
    severity: str | None = None
    category: str | None = None
    message: str | None = None
    suggestion: str | None = None
    start_line: int | str | None = None
    suggested_code: str | None = None


class RawFindingList(BaseModel):
    """Structured-output schema for a reviewer agent."""

    comments: list[RawFinding] = Field(default_factory=list)

    def as_items(self) -> list[dict[str, Any]]:
        return [c.model_dump() for c in self.comments]
