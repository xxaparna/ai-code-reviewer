"""Model-output validation (plan 1.5 / 1.5a) and the tolerant parser (1.4)."""

import pytest
from app.graph.parser import ReviewParser, validate_items
from app.models.enums import Category, Severity
from app.models.review_comment import ReviewComment
from app.prompts.shared import OUTPUT_FORMAT
from pydantic import ValidationError


def _finding(**kw: object) -> ReviewComment:
    base: dict[str, object] = {
        "file": "a.py",
        "severity": "HIGH",
        "category": "BUG",
        "message": "m",
    }
    return ReviewComment.model_validate({**base, **kw})


def test_prompt_enumerates_every_category() -> None:
    for category in Category:
        assert f'"{category.value}"' in OUTPUT_FORMAT


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("high", "HIGH"), (" Medium ", "MEDIUM"), ("critical", "HIGH"), ("info", "LOW")],
)
def test_severity_accepts_case_and_known_aliases(raw: str, expected: str) -> None:
    assert _finding(severity=raw).severity == Severity(expected)


@pytest.mark.parametrize("raw", ["URGENT", "", None, "3"])
def test_unknown_severity_is_rejected(raw: object) -> None:
    with pytest.raises(ValidationError):
        _finding(severity=raw)


def test_invented_category_is_rejected_not_relabelled() -> None:
    with pytest.raises(ValidationError):
        _finding(category="VIBES")
    assert _finding(category="code smell").category == Category.STYLE
    assert _finding(category="Maintainability").category == Category.STYLE


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (15, 15),
        ("15", 15),
        (" 7 ", 7),
        ("around 15", None),
        (0, None),
        (-3, None),
        (True, None),
        ("１５", None),
        (4.0, 4),
    ],
)
def test_line_numbers_are_coerced_strictly(raw: object, expected: int | None) -> None:
    assert _finding(line=raw).line == expected


def test_message_must_be_non_empty_and_is_capped() -> None:
    with pytest.raises(ValidationError):
        _finding(message="   ")
    assert len(_finding(message="x" * 10_000).message) == 4003


def test_paths_are_normalised() -> None:
    assert _finding(file="./app/x.py").file == "app/x.py"
    assert _finding(file="/app/x.py").file == "app/x.py"


def test_validate_items_drops_only_the_bad_ones() -> None:
    items = [
        {"severity": "HIGH", "category": "BUG", "message": "ok"},
        {"severity": "NOPE", "category": "BUG", "message": "bad"},
        "not a dict",
    ]
    comments, rejected = validate_items(items, default_file="a.py", agent="bug")
    assert [(c.file, c.agent, c.message) for c in comments] == [("a.py", "bug", "ok")]
    assert rejected == 2


@pytest.mark.parametrize(
    "text",
    [
        '{"comments": [{"file": "a.py", "severity": "LOW", "category": "BUG", "message": "m"}]}',
        '```json\n{"comments": [{"file": "a.py", "severity": "LOW", "category": "BUG", "message": "m"}]}\n```',
        'Sure! Here you go:\n[{"file": "a.py", "severity": "LOW", "category": "BUG", "message": "m"}] hope it helps',
    ],
)
def test_tolerant_parser_recovers_json(text: str) -> None:
    assert [c.message for c in ReviewParser.parse_comments(text)] == ["m"]


@pytest.mark.parametrize("text", ["", "no json here", "{not json", '{"comments": "nope"}'])
def test_tolerant_parser_never_raises_on_garbage(text: str) -> None:
    assert ReviewParser.parse_comments(text) == []
