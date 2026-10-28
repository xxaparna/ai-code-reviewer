"""Closed vocabularies for findings.

Model output is validated against these enums (plan 1.5/1.5a). A short alias
table absorbs common synonyms; anything else is *rejected*, never silently
relabelled - an invented category used to pass straight through.
"""

from enum import StrEnum


class Severity(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"

    @property
    def rank(self) -> int:
        """0 is most severe: sorts HIGH, MEDIUM, LOW."""
        return _SEVERITY_RANK[self]

    @classmethod
    def from_str(cls, value: str | None) -> "Severity":
        """Parse a model-supplied severity. Raises ``ValueError`` for unknown values."""
        if value is None or not str(value).strip():
            raise ValueError("severity is required")
        key = str(value).strip().upper()
        try:
            return cls(key)
        except ValueError:
            if key in _SEVERITY_ALIASES:
                return _SEVERITY_ALIASES[key]
            raise ValueError(f"unknown severity {value!r}") from None


_SEVERITY_RANK = {Severity.HIGH: 0, Severity.MEDIUM: 1, Severity.LOW: 2}
_SEVERITY_ALIASES = {
    "CRITICAL": Severity.HIGH,
    "BLOCKER": Severity.HIGH,
    "MAJOR": Severity.HIGH,
    "MODERATE": Severity.MEDIUM,
    "MINOR": Severity.LOW,
    "INFO": Severity.LOW,
    "TRIVIAL": Severity.LOW,
}


class Category(StrEnum):
    BUG = "BUG"
    SECURITY = "SECURITY"
    PERFORMANCE = "PERFORMANCE"
    ARCHITECTURE = "ARCHITECTURE"
    STYLE = "STYLE"

    @classmethod
    def from_str(cls, value: str | None) -> "Category":
        """Parse a model-supplied category. Raises ``ValueError`` for unknown values."""
        if value is None or not str(value).strip():
            raise ValueError("category is required")
        key = str(value).strip().upper().replace("-", "_").replace(" ", "_")
        try:
            return cls(key)
        except ValueError:
            if key in _CATEGORY_ALIASES:
                return _CATEGORY_ALIASES[key]
            raise ValueError(f"unknown category {value!r}") from None


_CATEGORY_ALIASES = {
    "CORRECTNESS": Category.BUG,
    "LOGIC": Category.BUG,
    "VULNERABILITY": Category.SECURITY,
    "PERF": Category.PERFORMANCE,
    "DESIGN": Category.ARCHITECTURE,
    "MAINTAINABILITY": Category.STYLE,
    "READABILITY": Category.STYLE,
    "CODE_SMELL": Category.STYLE,
}


class AgentName(StrEnum):
    BUG = "bug"
    SECURITY = "security"
    STYLE = "style"
    PERFORMANCE = "performance"
    ARCHITECTURE = "architecture"
    SUMMARY = "summary"
