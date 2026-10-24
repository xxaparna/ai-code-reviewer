"""Turn findings into a GitHub pull request review with inline suggested changes.

Pure functions: no I/O, so the exact payload GitHub receives is unit-tested.
"""

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from app.models.enums import Severity
from app.models.review_comment import ReviewComment
from app.models.review_file import ReviewFile
from app.utils.diff_validator import extract_valid_lines_from_patch

SEVERITY_RANK = {Severity.HIGH: 0, Severity.MEDIUM: 1, Severity.LOW: 2}

SEVERITY_ICON = {Severity.HIGH: "🔴", Severity.MEDIUM: "🟡", Severity.LOW: "🔵"}


@dataclass(slots=True)
class ReviewPayload:
    body: str
    comments: list[dict[str, Any]] = field(default_factory=list)
    suggested_changes: int = 0
    body_findings: int = 0


def meets_threshold(comment: ReviewComment, min_severity: Severity) -> bool:
    return SEVERITY_RANK[comment.severity] <= SEVERITY_RANK[min_severity]


def _fence(code: str) -> str:
    """A backtick fence longer than any run inside ``code``, so it cannot close early."""
    longest = run = 0
    for ch in code:
        run = run + 1 if ch == "`" else 0
        longest = max(longest, run)
    return "`" * max(3, longest + 1)


def comment_body(comment: ReviewComment) -> str:
    parts = [
        f"{SEVERITY_ICON[comment.severity]} **{comment.severity.value}** · "
        f"`{comment.category.value}`",
        "",
        comment.message,
    ]
    if comment.suggestion:
        parts += ["", comment.suggestion]
    if comment.suggested_code is not None:
        fence = _fence(comment.suggested_code)
        parts += ["", f"{fence}suggestion", comment.suggested_code, fence]
    return "\n".join(parts)


def _inline_comment(comment: ReviewComment, line: int) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "path": comment.file,
        "line": line,
        "side": "RIGHT",
        "body": comment_body(comment),
    }
    if (
        comment.suggested_code is not None
        and comment.start_line is not None
        and comment.start_line < line
    ):
        payload["start_line"] = comment.start_line
        payload["start_side"] = "RIGHT"
    return payload


def build_review_payload(
    comments: list[ReviewComment],
    files: list[ReviewFile],
    summary: str,
    min_severity: Severity,
    *,
    inline: bool = True,
) -> ReviewPayload:
    """Split findings into inline comments and a summary body.

    A finding goes inline only if its line is on the new side of the diff;
    anything else (file-level findings, or ``inline=False`` after GitHub
    rejected the inline set) is listed in the review body instead of lost.
    """
    valid_lines = {f.filename: extract_valid_lines_from_patch(f.patch) for f in files}
    selected = [c for c in comments if meets_threshold(c, min_severity)]

    payload = ReviewPayload(body="")
    leftovers: list[ReviewComment] = []
    for comment in selected:
        line = comment.line
        if inline and line is not None and line in valid_lines.get(comment.file, ()):
            payload.comments.append(_inline_comment(comment, line))
            if comment.suggested_code is not None:
                payload.suggested_changes += 1
        else:
            leftovers.append(comment)

    payload.body_findings = len(leftovers)
    payload.body = _review_body(summary, selected, leftovers, payload, min_severity)
    return payload


def _review_body(
    summary: str,
    selected: list[ReviewComment],
    leftovers: list[ReviewComment],
    payload: ReviewPayload,
    min_severity: Severity,
) -> str:
    counts = Counter(c.severity for c in selected)
    tally = " · ".join(
        f"{SEVERITY_ICON[s]} {counts[s]} {s.value.lower()}" for s in Severity if counts[s]
    )
    lines = ["## 🤖 AI Code Review", "", summary.strip()]
    if selected:
        lines += [
            "",
            f"**{len(selected)} findings at {min_severity.value} or above:** {tally}",
        ]
        if payload.suggested_changes:
            lines.append(
                f"{payload.suggested_changes} include a suggested change you can apply "
                "with **Commit suggestion**."
            )
    if leftovers:
        lines += ["", "### Findings not attached to a diff line", ""]
        for c in leftovers:
            location = f"`{c.file}:{c.line}`" if c.line is not None else f"`{c.file}`"
            lines.append(
                f"- {SEVERITY_ICON[c.severity]} **{c.severity.value}** {location}: {c.message}"
            )
    lines += [
        "",
        "<sub>Posted as a comment review. This bot never approves or blocks a pull request.</sub>",
    ]
    return "\n".join(lines)
