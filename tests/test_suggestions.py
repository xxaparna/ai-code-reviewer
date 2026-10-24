"""Suggested changes: hunk-aware validation and the exact GitHub review payload."""

from app.models.enums import Category, Severity
from app.models.review_comment import ReviewComment
from app.services.pr_publisher import build_review_payload, comment_body
from app.utils.diff_validator import (
    annotate_patch,
    extract_hunk_lines,
    filter_hallucinations,
    suggestion_is_anchorable,
)

from tests.fakes import make_file

# Two hunks: new-file lines 1-3 and 20-22.
TWO_HUNKS = (
    "@@ -1,2 +1,3 @@\n"
    " import os\n"
    "+import sys\n"
    " \n"
    "@@ -18,2 +20,3 @@\n"
    " def f():\n"
    "-    return 1\n"
    "+    x = 2\n"
    "+    return x\n"
)


def _finding(**overrides: object) -> ReviewComment:
    defaults: dict[str, object] = {
        "file": "app/example.py",
        "line": 2,
        "severity": Severity.HIGH,
        "category": Category.BUG,
        "message": "Problem.",
    }
    return ReviewComment.model_validate({**defaults, **overrides})


def test_hunks_cover_added_and_context_lines_but_not_removed_ones() -> None:
    assert extract_hunk_lines(TWO_HUNKS) == [{1, 2, 3}, {20, 21, 22}]


def test_annotate_patch_numbers_new_side_lines_only() -> None:
    lines = annotate_patch(TWO_HUNKS).splitlines()

    assert lines[0] == "@@ -1,2 +1,3 @@"
    assert lines[2] == "    2 +import sys"
    assert lines[6] == "      -    return 1"  # removed: blank gutter
    assert lines[7] == "   21 +    x = 2"


def test_suggestion_range_must_sit_inside_one_hunk() -> None:
    hunks = extract_hunk_lines(TWO_HUNKS)

    assert suggestion_is_anchorable(_finding(line=21, start_line=20), hunks)
    assert suggestion_is_anchorable(_finding(line=2), hunks)
    # Spans the gap between hunks: GitHub would reject it.
    assert not suggestion_is_anchorable(_finding(line=21, start_line=3), hunks)
    assert not suggestion_is_anchorable(_finding(line=None), hunks)


def test_unanchorable_fix_is_dropped_but_the_finding_is_kept() -> None:
    file = make_file(patch=TWO_HUNKS)
    finding = _finding(line=21, start_line=3, suggested_code="pass")

    [kept] = filter_hallucinations([finding], [file])

    assert kept.message == "Problem."
    assert kept.suggested_code is None
    assert kept.start_line is None


def test_inverted_start_line_is_coerced_and_rejected() -> None:
    hunks = extract_hunk_lines(TWO_HUNKS)
    assert not suggestion_is_anchorable(_finding(line=20, start_line=22), hunks)


def test_payload_posts_multi_line_suggestion_on_the_right_side() -> None:
    file = make_file(patch=TWO_HUNKS)
    finding = _finding(line=22, start_line=21, suggested_code="    return 2")

    payload = build_review_payload([finding], [file], "Summary.", Severity.LOW)

    [comment] = payload.comments
    assert comment["path"] == "app/example.py"
    assert comment["line"] == 22
    assert comment["start_line"] == 21
    assert comment["side"] == comment["start_side"] == "RIGHT"
    assert "```suggestion\n    return 2\n```" in comment["body"]
    assert payload.suggested_changes == 1


def test_single_line_suggestion_omits_start_line() -> None:
    file = make_file(patch=TWO_HUNKS)
    payload = build_review_payload(
        [_finding(line=2, suggested_code="import sys  # noqa")], [file], "S", Severity.LOW
    )

    assert "start_line" not in payload.comments[0]


def test_threshold_excludes_lower_severities_entirely() -> None:
    file = make_file(patch=TWO_HUNKS)
    findings = [
        _finding(line=2, severity=Severity.HIGH),
        _finding(line=3, severity=Severity.MEDIUM),
        _finding(line=21, severity=Severity.LOW),
    ]

    high_only = build_review_payload(findings, [file], "S", Severity.HIGH)
    medium_up = build_review_payload(findings, [file], "S", Severity.MEDIUM)

    assert [c["line"] for c in high_only.comments] == [2]
    assert [c["line"] for c in medium_up.comments] == [2, 3]


def test_unanchored_findings_go_to_the_body_instead_of_being_lost() -> None:
    file = make_file(patch=TWO_HUNKS)
    findings = [_finding(line=None, message="Whole-file concern.")]

    payload = build_review_payload(findings, [file], "S", Severity.LOW)

    assert payload.comments == []
    assert payload.body_findings == 1
    assert "Whole-file concern." in payload.body


def test_inline_false_moves_everything_to_the_body() -> None:
    file = make_file(patch=TWO_HUNKS)
    payload = build_review_payload([_finding(line=2)], [file], "S", Severity.LOW, inline=False)

    assert payload.comments == []
    assert payload.body_findings == 1


def test_fence_is_longer_than_backticks_inside_the_code() -> None:
    body = comment_body(_finding(suggested_code='doc = """```py"""'))

    assert "````suggestion\n" in body
    assert body.endswith("\n````")
