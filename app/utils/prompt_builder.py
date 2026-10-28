from app.models.review_file import ReviewFile
from app.models.review_request import ReviewRequest
from app.utils.diff_validator import annotate_patch


def _render_patch(file: ReviewFile) -> str:
    if not file.patch:
        return "No patch diff available."
    return annotate_patch(file.patch)


class PromptBuilder:
    """Builds the prompt for one reviewer call: PR context plus exactly one file's diff.

    There is deliberately no whole-PR variant: sending every file to every agent
    is the bug Phase 2 removed (it overflows the context window on real PRs).
    """

    @staticmethod
    def build(request: ReviewRequest, current_file: ReviewFile) -> str:
        description = request.description or "No description provided."
        sections = [
            f"Pull Request Title: {request.title}",
            f"Description: {description}",
            f"\n--- Target File: {current_file.filename} ---\n"
            f"Language: {current_file.language}\n"
            f"Status: {current_file.status}\n"
            f"Additions: {current_file.additions}, Deletions: {current_file.deletions}\n"
            f"\nGit Diff / Code Changes:\n"
            f"```\n{_render_patch(current_file)}\n```",
            "\nEach added (+) or context line is prefixed with its line number in the new "
            'file. Use those numbers for "line" and "start_line".',
            f'\nReview ONLY {current_file.filename}. Use exactly that path for "file".',
            "\nReturn your evaluation in JSON format.",
        ]
        return "\n".join(sections)
