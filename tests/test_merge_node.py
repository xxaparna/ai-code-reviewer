from app.graph.merge import apply_caps, dedup_findings, make_merge_node, message_similarity
from app.models.enums import Category, Severity
from app.models.review_comment import ReviewComment
from app.review_config import ReviewConfig

from tests.conftest import make_review_state


def _c(
    line: int | None, sev: Severity, msg: str, file: str = "app/main.py", **kw: object
) -> ReviewComment:
    return ReviewComment.model_validate(
        {
            "file": file,
            "line": line,
            "severity": sev,
            "category": Category.BUG,
            "message": msg,
            **kw,
        }
    )


def test_merge_node_deduplication_and_sorting() -> None:
    c1 = _c(10, Severity.LOW, "Unused import")
    c2 = _c(5, Severity.HIGH, "SQL injection risk")
    c3 = _c(10, Severity.LOW, "Unused import")

    result = make_merge_node(ReviewConfig())(make_review_state(findings=[c1, c2, c3]))
    merged = result["merged_comments"]

    assert [(m.severity, m.message) for m in merged] == [
        ("HIGH", "SQL injection risk"),
        ("LOW", "Unused import"),
    ]
    assert merged[1].dedup_collapsed_count == 2
    assert result["merge_stats"]["duplicates_collapsed"] == 1


def test_dedup_keeps_highest_severity_and_ignores_category() -> None:
    a = _c(12, Severity.MEDIUM, "Query built by string concatenation", category=Category.STYLE)
    b = _c(14, Severity.HIGH, "SQL query built by string concatenation: injection")
    unique, collapsed = dedup_findings([a, b], threshold=70, line_window=3)

    assert collapsed == 1
    assert len(unique) == 1
    assert unique[0].severity == Severity.HIGH


def test_dedup_respects_line_window_and_file() -> None:
    findings = [
        _c(10, Severity.HIGH, "Possible None dereference"),
        _c(20, Severity.HIGH, "Possible None dereference"),
        _c(10, Severity.HIGH, "Possible None dereference", file="app/other.py"),
        _c(None, Severity.HIGH, "Possible None dereference"),
    ]
    unique, collapsed = dedup_findings(findings, threshold=70, line_window=3)
    assert collapsed == 0
    assert len(unique) == 4


def test_unrelated_messages_on_the_same_line_both_survive() -> None:
    assert message_similarity("Unused import", "SQL injection via f-string") < 70
    unique, _ = dedup_findings(
        [_c(5, Severity.HIGH, "Unused import"), _c(5, Severity.HIGH, "SQL injection via f-string")],
        threshold=70,
        line_window=3,
    )
    assert len(unique) == 2


def test_dedup_keeps_a_fix_only_the_lower_ranked_duplicate_offered() -> None:
    high = _c(5, Severity.HIGH, "Unchecked None dereference")
    low = _c(5, Severity.LOW, "Unchecked None dereference here", suggested_code="if x:")
    unique, _ = dedup_findings([low, high], threshold=70, line_window=3)
    assert unique[0].severity == Severity.HIGH
    assert unique[0].suggested_code == "if x:"


def test_caps_keep_the_most_severe() -> None:
    findings = [_c(i, Severity.HIGH, f"h{i}") for i in range(1, 4)]
    findings += [_c(i, Severity.LOW, f"l{i}", file="b.py") for i in range(1, 4)]
    capped, over_file, over_review = apply_caps(findings, per_file=2, per_review=3)
    assert [c.message for c in capped] == ["h1", "h2", "l1"]
    assert (over_file, over_review) == (2, 1)
