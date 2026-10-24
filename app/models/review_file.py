from dataclasses import dataclass


@dataclass(slots=True)
class ReviewFile:
    filename: str
    status: str
    patch: str = ""
    language: str = "Unknown"
    additions: int = 0
    deletions: int = 0
    changes: int = 0
    old_path: str | None = None
