from collections import defaultdict

from app.graph.state import ReviewState


class SummaryPromptBuilder:
    """
    Builds the prompt used by the Summary Node.
    """

    @staticmethod
    def build(state: ReviewState) -> str:
        merged_comments = state.get("merged_comments", [])

        sections = [
            "You are reviewing the findings from multiple AI reviewers.\n"
            "Do NOT identify new issues.\n"
            "Only summarize the existing findings."
        ]

        if not merged_comments:
            sections.append(
                "\nSTATUS: No issues, bugs, or security vulnerabilities were identified by any reviewer.\n"
                "Provide a short, positive executive summary stating that the pull request passed all automated reviews cleanly."
            )
        else:
            grouped_comments = defaultdict(list)
            for comment in merged_comments:
                grouped_comments[comment.category].append(comment)

            for category, comments in grouped_comments.items():
                sections.append(f"\n## {category} Findings")
                for comment in comments:
                    location = (
                        f"{comment.file}:{comment.line}"
                        if comment.line is not None
                        else comment.file
                    )
                    sections.append(f"- [{comment.severity}] {location} - {comment.message}")

            sections.append(
                "\nProvide an executive summary highlighting the most critical findings."
            )

        return "\n".join(sections)
