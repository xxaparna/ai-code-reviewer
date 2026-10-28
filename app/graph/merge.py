"""Fan-in: cross-agent fuzzy dedup, then caps (plan 1.11 / 2.7 / 2.8).

The §4b baseline measured a 2.2x duplication factor: one SQL injection was
reported four times by four agents, in four wordings and categories. Two
findings are the same issue when they are in the same file, within
``line_window`` lines of each other, and their messages are similar
(``rapidfuzz.fuzz.token_set_ratio`` >= ``threshold``). Category is ignored on
purpose - mislabelled categories are exactly what defeated the old exact key.
The highest-severity copy survives and counts how many it absorbed.
"""

from collections import defaultdict
from typing import Any

from rapidfuzz import fuzz
from rapidfuzz.utils import default_process

from app.graph.state import ReviewState, StateNode
from app.models.review_comment import ReviewComment
from app.review_config import ReviewConfig


def message_similarity(a: str, b: str) -> float:
    """0-100. Order-insensitive, tolerant of one message elaborating on the other."""
    if not a or not b:
        return 0.0
    return float(fuzz.token_set_ratio(a, b, processor=default_process))


def _lines_close(a: int | None, b: int | None, window: int) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= window


def _sort_key(c: ReviewComment) -> tuple[int, str, int, int]:
    return (c.severity.rank, c.file, c.line if c.line is not None else -1, -len(c.message))


def dedup_findings(
    findings: list[ReviewComment], *, threshold: float, line_window: int
) -> tuple[list[ReviewComment], int]:
    """Collapse duplicates. Returns (unique findings, number collapsed)."""
    by_file: dict[str, list[ReviewComment]] = defaultdict(list)
    collapsed = 0
    for comment in sorted(findings, key=_sort_key):
        kept = by_file[comment.file]
        match = next(
            (
                existing
                for existing in kept
                if _lines_close(existing.line, comment.line, line_window)
                and message_similarity(existing.message, comment.message) >= threshold
            ),
            None,
        )
        if match is None:
            kept.append(comment.model_copy(deep=True))
            continue
        collapsed += 1
        match.dedup_collapsed_count += comment.dedup_collapsed_count
        # Keep the higher-severity wording, but not at the cost of a concrete fix
        # that only a lower-ranked duplicate proposed.
        if match.suggested_code is None and comment.suggested_code is not None:
            match.suggested_code = comment.suggested_code
            match.start_line = comment.start_line
            match.line = comment.line
    unique = [c for group in by_file.values() for c in group]
    return sorted(unique, key=_sort_key), collapsed


def apply_caps(
    findings: list[ReviewComment], *, per_file: int, per_review: int
) -> tuple[list[ReviewComment], int, int]:
    """Keep the most severe findings. Input must be severity-sorted."""
    seen: dict[str, int] = defaultdict(int)
    within_file: list[ReviewComment] = []
    for comment in findings:
        if seen[comment.file] < per_file:
            within_file.append(comment)
        seen[comment.file] += 1
    capped = within_file[:per_review]
    return capped, len(findings) - len(within_file), len(within_file) - len(capped)


def make_merge_node(config: ReviewConfig) -> StateNode:
    def merge_node(state: ReviewState) -> dict[str, Any]:
        raw = list(state.get("findings", []))
        unique, collapsed = dedup_findings(
            raw,
            threshold=config.dedup_similarity_threshold,
            line_window=config.dedup_line_window,
        )
        capped, over_file, over_review = apply_caps(
            unique,
            per_file=config.max_findings_per_file,
            per_review=config.max_findings_per_review,
        )
        return {
            "merged_comments": capped,
            "merge_stats": {
                "raw_findings": len(raw),
                "duplicates_collapsed": collapsed,
                "capped_per_file": over_file,
                "capped_per_review": over_review,
            },
        }

    return merge_node


merge_node = make_merge_node(ReviewConfig())
