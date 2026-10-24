from enum import StrEnum


class Severity(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"

    @classmethod
    def from_str(cls, value: str | None) -> "Severity":
        if not value:
            return cls.LOW
        val_upper = str(value).strip().upper()
        try:
            return cls(val_upper)
        except ValueError:
            return cls.LOW


class Category(StrEnum):
    BUG = "BUG"
    SECURITY = "SECURITY"
    PERFORMANCE = "PERFORMANCE"
    ARCHITECTURE = "ARCHITECTURE"
    STYLE = "STYLE"

    @classmethod
    def from_str(cls, value: str | None) -> "Category":
        if not value:
            return cls.BUG
        val_upper = str(value).strip().upper()
        try:
            return cls(val_upper)
        except ValueError:
            # Tolerant fallback: map unknown categories reasonably
            if "SEC" in val_upper:
                return cls.SECURITY
            if "PERF" in val_upper:
                return cls.PERFORMANCE
            if "ARCH" in val_upper:
                return cls.ARCHITECTURE
            if "STYLE" in val_upper or "FORMAT" in val_upper or "LINT" in val_upper:
                return cls.STYLE
            return cls.BUG


class AgentName(StrEnum):
    BUG = "bug"
    SECURITY = "security"
    STYLE = "style"
    PERFORMANCE = "performance"
    ARCHITECTURE = "architecture"
    SUMMARY = "summary"
