from difflib import SequenceMatcher
from typing import Any

from app.graph.state import ReviewState
from app.models.enums import Severity
from app.models.review_comment import ReviewComment

SEVERITY_ORDER = {
    Severity.HIGH: 0,
    Severity.MEDIUM: 1,
    Severity.LOW: 2,
}


def is_similar_message(msg1: str, msg2: str, threshold: float = 0.65) -> bool:
    """Return True if two finding messages are fuzzy-matched as similar."""
    if not msg1 or not msg2:
        return False
    clean1 = msg1.strip().lower()
    clean2 = msg2.strip().lower()
    if clean1 == clean2:
        return True
    return SequenceMatcher(None, clean1, clean2).ratio() >= threshold


def merge_node(state: ReviewState) -> dict[str, Any]:
    """
    Merges, deduplicates, and sorts findings from all specialized reviewer nodes.
    Applies fuzzy line-window matching (line +/- 3) and string similarity.
    """
    raw_comments: list[ReviewComment] = []

    raw_comments.extend(state.get("bug_comments", []))
    raw_comments.extend(state.get("security_comments", []))
    raw_comments.extend(state.get("style_comments", []))
    raw_comments.extend(state.get("performance_comments", []))
    raw_comments.extend(state.get("architecture_comments", []))

    # Sort high severity first, then by file, line, and message length
    raw_comments.sort(
        key=lambda c: (
            SEVERITY_ORDER.get(c.severity, 99),
            c.file,
            c.line if c.line is not None else -1,
            -len(c.message),
        )
    )

    unique_comments: list[ReviewComment] = []

    for comment in raw_comments:
        matched = False
        for existing in unique_comments:
            if existing.file != comment.file:
                continue

            # Line distance check (both None or line delta <= 3)
            line_match = False
            if existing.line is None and comment.line is None:
                line_match = True
            elif existing.line is not None and comment.line is not None:
                if abs(existing.line - comment.line) <= 3:
                    line_match = True

            if line_match and is_similar_message(existing.message, comment.message):
                existing.dedup_collapsed_count += comment.dedup_collapsed_count
                # Keep the higher-severity wording, but don't lose a concrete fix
                # that only a lower-ranked duplicate proposed.
                if existing.suggested_code is None and comment.suggested_code is not None:
                    existing.suggested_code = comment.suggested_code
                    existing.start_line = comment.start_line
                    existing.line = comment.line
                matched = True
                break

        if not matched:
            unique_comments.append(comment.model_copy(deep=True))

    return {"merged_comments": unique_comments}
