"""Review orchestration: fetch the PR, plan the files, run the graph, report."""

from collections import Counter, defaultdict
from collections.abc import Iterable
from typing import Any, cast

import structlog

from app.agents.registry import AgentRegistry, default_registry
from app.graph.builder import build_graph
from app.graph.reviewer import LLMFactory
from app.graph.state import AgentRun, ReviewState
from app.models.enums import Severity
from app.models.review_request import ReviewRequest
from app.models.review_response import ReviewResponse
from app.review_config import ReviewConfig
from app.services.github_service import GitHubService
from app.services.llm_client import LLMPool, ReviewLLM
from app.services.pr_publisher import build_review_payload
from app.settings import get_settings
from app.utils.file_filter import plan_review
from app.utils.github_parser import parse_pr_url
from app.utils.review_mapper import ReviewMapper

logger = structlog.get_logger(__name__)


def _agent_stats(runs: list[AgentRun]) -> dict[str, dict[str, int]]:
    stats: dict[str, dict[str, int]] = defaultdict(
        lambda: {"runs": 0, "failed": 0, "findings": 0, "dropped": 0, "duration_ms": 0}
    )
    for run in runs:
        s = stats[run["agent"]]
        s["runs"] += 1
        s["failed"] += run["status"] == "failed"
        s["findings"] += run["accepted"]
        s["dropped"] += (
            run["dropped_invalid"]
            + run["dropped_hallucinated"]
            + run["dropped_below_floor"]
            + run["dropped_over_cap"]
        )
        s["duration_ms"] = max(s["duration_ms"], run["duration_ms"])
    return dict(stats)


def _as_factory(llm: ReviewLLM | LLMFactory | None) -> LLMFactory:
    if llm is None:
        return LLMPool()
    if isinstance(llm, LLMPool):
        return llm
    generate = getattr(llm, "generate", None)
    if callable(generate):
        client = cast(ReviewLLM, llm)
        return lambda _tier: client
    return cast(LLMFactory, llm)


class ReviewService:
    def __init__(
        self,
        github_service: GitHubService | None = None,
        *,
        llm: ReviewLLM | LLMFactory | None = None,
        registry: AgentRegistry | None = None,
        config: ReviewConfig | None = None,
    ) -> None:
        """Everything is injectable; nothing here needs credentials until first use.

        ``llm`` may be a single client (used for every tier - handy in tests) or a
        ``tier -> client`` factory. Default: one lazily-built Groq client per tier.
        """
        self.registry = registry or default_registry()
        self.config = config or ReviewConfig()
        self._llm_for: LLMFactory = _as_factory(llm)
        self.workflow = build_graph(
            llm_for=self._llm_for, registry=self.registry, config=self.config
        )
        self._github_service = github_service

    @classmethod
    def from_settings(cls) -> "ReviewService":
        """Production wiring: settings-driven config and model tiers."""
        settings = get_settings()
        return cls(
            GitHubService(),
            llm=LLMPool.from_settings(),
            config=ReviewConfig.from_settings(settings),
        )

    @property
    def github_service(self) -> GitHubService:
        if self._github_service is None:
            self._github_service = GitHubService()
        return self._github_service

    def close(self) -> None:
        if self._github_service is not None:
            self._github_service.close()

    def review_pr_url(
        self,
        pr_url: str,
        publish_to_github: bool = False,
        agents: Iterable[str] | None = None,
    ) -> ReviewResponse:
        """Full workflow: PR URL -> GitHub API -> Domain Model -> LangGraph Workflow -> Response."""
        request = self.fetch_request(pr_url)
        response = self.review(request, agents=agents)

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

    def review(self, request: ReviewRequest, agents: Iterable[str] | None = None) -> ReviewResponse:
        """Run the graph over the reviewable files.

        Short-circuits before any LLM call when nothing is reviewable (plan 1.10),
        including when every file was filtered out (plan 2.5).
        """
        selected = self.registry.select(agents)
        plan = plan_review(
            request.files,
            agent_count=len(selected),
            max_patch_chars=self.config.max_patch_chars,
            max_prompt_tokens=self.config.max_review_prompt_tokens,
        )
        base_metadata: dict[str, Any] = {
            "total_files": len(request.files),
            "reviewed_files": len(plan.files),
            "skipped_files": [s.as_dict() for s in plan.skipped],
            "agents": [a.name for a in selected],
            "estimated_prompt_tokens": plan.estimated_prompt_tokens,
        }

        if not plan.files:
            return ReviewResponse(
                summary=(
                    "Nothing to review: the pull request has no changed files with "
                    "reviewable patch content."
                ),
                comments=[],
                metadata={
                    **base_metadata,
                    "total_comments": 0,
                    "severity_counts": {},
                    "category_counts": {},
                    "errors": [],
                    "complete": True,
                    "partial": False,
                    "partial_reason": None,
                    "unreviewable": True,
                    "llm_calls": 0,
                },
            )

        initial_state: ReviewState = {
            "request": request,
            "files": plan.files,
            "agents": [a.name for a in selected],
            "findings": [],
            "agent_runs": [],
            "errors": [],
        }
        final_state = self.workflow.invoke(
            initial_state, config={"max_concurrency": self.config.max_concurrency}
        )

        comments = final_state.get("merged_comments", [])
        runs: list[AgentRun] = final_state.get("agent_runs", [])
        errors: list[str] = final_state.get("errors", [])
        merge_stats: dict[str, int] = final_state.get("merge_stats", {})

        partial_reason = plan.partial_reason
        if errors and partial_reason is None:
            partial_reason = f"{len(errors)} reviewer call(s) failed."

        metadata = {
            **base_metadata,
            "total_comments": len(comments),
            "hallucinated_comments_dropped": sum(r["dropped_hallucinated"] for r in runs),
            "invalid_findings_dropped": sum(r["dropped_invalid"] for r in runs),
            "below_severity_floor_dropped": sum(r["dropped_below_floor"] for r in runs),
            "capped_findings_dropped": sum(r["dropped_over_cap"] for r in runs)
            + merge_stats.get("capped_per_file", 0)
            + merge_stats.get("capped_per_review", 0),
            "raw_findings": merge_stats.get("raw_findings", 0),
            "duplicates_collapsed": merge_stats.get("duplicates_collapsed", 0),
            "severity_counts": dict(Counter(c.severity.value for c in comments)),
            "category_counts": dict(Counter(c.category.value for c in comments)),
            "agent_stats": _agent_stats(runs),
            "structured_output_fallbacks": sum(r["used_fallback"] for r in runs),
            "llm_calls": len(runs) + 1,
            "errors": errors,
            "complete": not errors and not plan.is_partial,
            "partial": partial_reason is not None,
            "partial_reason": partial_reason,
        }
        logger.info(
            "review.completed",
            files=len(plan.files),
            agents=len(selected),
            findings=len(comments),
            errors=len(errors),
        )
        return ReviewResponse(
            summary=final_state.get("summary", "No summary generated."),
            comments=comments,
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
