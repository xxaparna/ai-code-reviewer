from app.models.review_file import ReviewFile
from app.models.review_request import ReviewRequest
from app.utils.diff_validator import annotate_patch


def _render_patch(file: ReviewFile) -> str:
    if not file.patch:
        return "No patch diff available."
    return annotate_patch(file.patch)


class PromptBuilder:
    """
    Builds contextual review prompts for code reviewer nodes.
    """

    @staticmethod
    def build(request: ReviewRequest, current_file: ReviewFile | None = None) -> str:
        sections = [
            f"Pull Request Title: {request.title}",
            f"Description: {request.description if request.description else 'No description provided.'}",
        ]

        if current_file:
            sections.append(
                f"\n--- Target File: {current_file.filename} ---\n"
                f"Language: {current_file.language}\n"
                f"Status: {current_file.status}\n"
                f"Additions: {current_file.additions}, Deletions: {current_file.deletions}\n"
                f"\nGit Diff / Code Changes:\n"
                f"```\n{_render_patch(current_file)}\n```"
            )
        else:
            sections.append("\n--- Changed Files ---")
            for file in request.files:
                sections.append(
                    f"\nFile: {file.filename} ({file.language})\n"
                    f"Status: {file.status}\n"
                    f"Diff:\n```\n{_render_patch(file)}\n```"
                )

        sections.append(
            "\nEach added (+) or context line is prefixed with its line number in the new "
            'file. Use those numbers for "line" and "start_line".'
        )
        sections.append(
            "\nPlease review the above code changes carefully and return your evaluation in JSON format."
        )

        return "\n".join(sections)
