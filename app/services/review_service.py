from collections import Counter

from app.graph.builder import build_graph
from app.graph.state import ReviewState
from app.models.enums import Severity
from app.models.review_request import ReviewRequest
from app.models.review_response import ReviewResponse
from app.services.github_service import GitHubService
from app.services.pr_publisher import build_review_payload
from app.settings import get_settings
from app.utils.diff_validator import filter_hallucinations, is_pr_reviewable
from app.utils.github_parser import parse_pr_url
from app.utils.review_mapper import ReviewMapper


class ReviewService:
    def __init__(self, github_service: GitHubService | None = None):
        self.workflow = build_graph()
        self.github_service = github_service or GitHubService()

    def review_pr_url(self, pr_url: str, publish_to_github: bool = False) -> ReviewResponse:
        """
        Full workflow: PR URL -> GitHub API -> Domain Model -> LangGraph Workflow -> Response.
        """
        request = self.fetch_request(pr_url)
        response = self.review(request)

        if publish_to_github and response.comments:
            self._publish_review_to_github(request, response)

        return response

    def fetch_request(self, pr_url: str) -> ReviewRequest:
        """Resolve a PR URL into a :class:`ReviewRequest` with its changed files."""
        pr_info = parse_pr_url(pr_url)
        owner = pr_info["owner"]
        repo = pr_info["repo"]
        pull_number = pr_info["pull_number"]

        pull_request = self.github_service.get_pull_request(owner, repo, pull_number)
        changed_files = self.github_service.get_pull_request_files(owner, repo, pull_number)

        request = ReviewMapper.from_github(pull_request, changed_files)
        request.owner = owner
        request.repo = repo
        request.pull_number = pull_number
        return request

    def review(self, request: ReviewRequest) -> ReviewResponse:
        """
        Executes the LangGraph review graph for a given ReviewRequest.
        Short-circuits if the pull request has no reviewable patches.
        """
        if not is_pr_reviewable(request.files):
            return ReviewResponse(
                summary="Nothing to review: pull request has no changed files or reviewable patch content.",
                comments=[],
                metadata={
                    "total_files": len(request.files),
                    "total_comments": 0,
                    "severity_counts": {},
                    "category_counts": {},
                    "errors": [],
                    "complete": True,
                    "unreviewable": True,
                },
            )

        initial_state: ReviewState = {
            "request": request,
            "current_file": None,
            "bug_comments": [],
            "security_comments": [],
            "style_comments": [],
            "performance_comments": [],
            "architecture_comments": [],
            "merged_comments": [],
            "summary": "",
            "errors": [],
            "metadata": {},
        }

        final_state = self.workflow.invoke(initial_state)

        raw_merged_comments = final_state.get("merged_comments", [])
        valid_comments = filter_hallucinations(raw_merged_comments, request.files)

        severity_counts = dict(Counter(c.severity for c in valid_comments))
        category_counts = dict(Counter(c.category for c in valid_comments))

        errors = final_state.get("errors", [])

        metadata = {
            "total_files": len(request.files),
            "total_comments": len(valid_comments),
            "hallucinated_comments_dropped": len(raw_merged_comments) - len(valid_comments),
            "severity_counts": severity_counts,
            "category_counts": category_counts,
            "errors": errors,
            "complete": not errors,
        }

        return ReviewResponse(
            summary=final_state.get("summary", "No summary generated."),
            comments=valid_comments,
            metadata=metadata,
        )

    def _publish_review_to_github(self, request: ReviewRequest, response: ReviewResponse) -> None:
        """Post findings as inline comments with suggested changes (CLI ``--publish``)."""
        min_severity = Severity(get_settings().suggestion_min_severity)
        payload = build_review_payload(
            response.comments, request.files, response.summary, min_severity
        )
        self.github_service.create_pull_request_review(
            owner=request.owner,
            repo=request.repo,
            pull_number=request.pull_number,
            body=payload.body,
            event="COMMENT",
            comments=payload.comments,
            commit_id=request.head_sha or None,
        )
