"""Utilities for patch hunk parsing, hallucination filtering, and PR reviewability check."""

import re

import structlog

from app.models.review_comment import ReviewComment
from app.models.review_file import ReviewFile

logger = structlog.get_logger(__name__)

HUNK_HEADER_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")

# Longest range a single suggested change may replace.
MAX_SUGGESTION_LINES = 30


def extract_hunk_lines(patch: str) -> list[set[int]]:
    """Return, per hunk, the new-file line numbers it covers (added or context lines)."""
    if not patch:
        return []

    hunks: list[set[int]] = []
    current_line = 0

    for line in patch.splitlines():
        match = HUNK_HEADER_RE.match(line)
        if match:
            current_line = int(match.group(1))
            hunks.append(set())
            continue

        if not hunks:
            continue

        if line.startswith("+") or line.startswith(" "):
            hunks[-1].add(current_line)
            current_line += 1

    return hunks


def extract_valid_lines_from_patch(patch: str) -> set[int]:
    """Extract all valid new-file line numbers covered by patch hunks."""
    valid_lines: set[int] = set()
    for hunk in extract_hunk_lines(patch):
        valid_lines |= hunk
    return valid_lines


def annotate_patch(patch: str) -> str:
    """Prefix every added/context line with its new-file line number.

    Models are poor at counting lines from ``@@`` headers. Suggested changes
    replace exact line ranges, so the prompt states each number explicitly.
    Removed lines get a blank gutter: they cannot be commented on.
    """
    out: list[str] = []
    current_line = 0
    in_hunk = False
    for line in patch.splitlines():
        match = HUNK_HEADER_RE.match(line)
        if match:
            current_line = int(match.group(1))
            in_hunk = True
            out.append(line)
        elif in_hunk and (line.startswith("+") or line.startswith(" ")):
            out.append(f"{current_line:>5} {line}")
            current_line += 1
        else:
            out.append(f"{'':>5} {line}")
    return "\n".join(out)


def suggestion_is_anchorable(comment: ReviewComment, hunks: list[set[int]]) -> bool:
    """True when a suggested change can be posted as a GitHub multi-line suggestion.

    GitHub requires the whole ``start_line``..``line`` range to sit on the new
    side of a single hunk.
    """
    if comment.line is None:
        return False
    start = comment.start_line if comment.start_line is not None else comment.line
    if start > comment.line or comment.line - start + 1 > MAX_SUGGESTION_LINES:
        return False
    wanted = set(range(start, comment.line + 1))
    return any(wanted <= hunk for hunk in hunks)


def filter_hallucinations(
    comments: list[ReviewComment], files: list[ReviewFile]
) -> list[ReviewComment]:
    """Drop any finding referencing a file not in changed files or a line outside diff hunks."""
    file_map: dict[str, ReviewFile] = {f.filename: f for f in files}
    hunks_map: dict[str, list[set[int]]] = {f.filename: extract_hunk_lines(f.patch) for f in files}
    valid_lines_map: dict[str, set[int]] = {
        name: set[int]().union(*hunks) for name, hunks in hunks_map.items()
    }

    valid_comments: list[ReviewComment] = []

    for comment in comments:
        if comment.file not in file_map:
            logger.info("finding.dropped_unknown_file", file=comment.file, line=comment.line)
            continue

        if comment.line is not None:
            allowed_lines = valid_lines_map[comment.file]
            if allowed_lines and comment.line not in allowed_lines:
                logger.info(
                    "finding.dropped_out_of_hunk_line",
                    file=comment.file,
                    line=comment.line,
                )
                continue

        if comment.suggested_code is not None and not suggestion_is_anchorable(
            comment, hunks_map[comment.file]
        ):
            # Keep the finding, drop only the fix: a suggestion that spans
            # lines outside one hunk would be rejected by GitHub.
            logger.info(
                "finding.suggestion_dropped_unanchorable",
                file=comment.file,
                start_line=comment.start_line,
                line=comment.line,
            )
            comment = comment.model_copy(update={"suggested_code": None, "start_line": None})

        valid_comments.append(comment)

    return valid_comments
