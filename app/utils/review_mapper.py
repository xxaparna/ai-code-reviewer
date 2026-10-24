from typing import Any

from app.models.review_file import ReviewFile
from app.models.review_request import ReviewRequest
from app.utils.file_analyzer import FileAnalyzer


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


class ReviewMapper:
    @staticmethod
    def from_github(
        pull_request: dict[str, Any],
        changed_files: list[dict[str, Any]],
    ) -> ReviewRequest:

        files: list[ReviewFile] = []

        for file_data in changed_files:
            filename = file_data["filename"]
            patch = file_data.get("patch", "")
            stats = FileAnalyzer.parse_patch_stats(patch)
            language = FileAnalyzer.detect_language(filename)

            additions = file_data.get("additions", stats["additions"])
            deletions = file_data.get("deletions", stats["deletions"])
            changes = file_data.get("changes", stats["changes"])

            files.append(
                ReviewFile(
                    filename=filename,
                    status=file_data["status"],
                    patch=patch,
                    language=language,
                    additions=additions,
                    deletions=deletions,
                    changes=changes,
                    old_path=file_data.get("previous_filename"),
                )
            )

        head = _as_dict(pull_request.get("head"))
        base = _as_dict(pull_request.get("base"))
        user = _as_dict(pull_request.get("user"))
        head_sha = head.get("sha", "")

        owner = ""
        repo = ""
        if isinstance(pull_request.get("base"), dict) and isinstance(
            pull_request["base"].get("repo"), dict
        ):
            owner = pull_request["base"]["repo"].get("owner", {}).get("login", "")
            repo = pull_request["base"]["repo"].get("name", "")

        return ReviewRequest(
            title=pull_request.get("title", "Untitled PR"),
            description=pull_request.get("body") or "",
            files=files,
            owner=owner,
            repo=repo,
            pull_number=pull_request.get("number", 0),
            head_sha=head_sha,
            head_ref=head.get("ref", ""),
            base_ref=base.get("ref", ""),
            author=user.get("login", ""),
            html_url=pull_request.get("html_url", ""),
        )
