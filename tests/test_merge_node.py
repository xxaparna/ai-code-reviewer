from app.graph.merge import merge_node
from app.models.review_comment import ReviewComment

from tests.conftest import make_review_state


def test_merge_node_deduplication_and_sorting() -> None:
    c1 = ReviewComment(
        file="app/main.py",
        line=10,
        severity="LOW",
        category="STYLE",
        message="Unused import",
    )
    c2 = ReviewComment(
        file="app/main.py",
        line=5,
        severity="HIGH",
        category="SECURITY",
        message="SQL injection risk",
    )
    # Duplicate of c1
    c3 = ReviewComment(
        file="app/main.py",
        line=10,
        severity="LOW",
        category="STYLE",
        message="Unused import",
    )

    state = make_review_state(
        security_comments=[c2],
        style_comments=[c1, c3],
    )

    result = merge_node(state)
    merged = result["merged_comments"]

    assert len(merged) == 2
    assert merged[0].severity == "HIGH"
    assert merged[0].message == "SQL injection risk"
    assert merged[1].severity == "LOW"
