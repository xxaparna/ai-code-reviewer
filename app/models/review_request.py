from dataclasses import dataclass, field

from app.models.review_file import ReviewFile


@dataclass(slots=True)
class ReviewRequest:
    """
    Standard input for AI reviewer containing metadata and changed files.
    """

    title: str
    description: str
    files: list[ReviewFile] = field(default_factory=list)
    owner: str = ""
    repo: str = ""
    pull_number: int = 0
    head_sha: str = ""
    head_ref: str = ""
    base_ref: str = ""
    author: str = ""
    html_url: str = ""
