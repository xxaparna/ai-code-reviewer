from typing import Any

import structlog

from app.graph.parser import ReviewParser
from app.graph.state import ReviewState
from app.models.enums import Severity
from app.models.review_comment import FindingList, ReviewComment
from app.services.llm_client import LLMClient
from app.utils.prompt_builder import PromptBuilder

logger = structlog.get_logger(__name__)

SEVERITY_ORDER = {
    Severity.HIGH: 0,
    Severity.MEDIUM: 1,
    Severity.LOW: 2,
}


class BaseNode:
    """Shared behaviour for the specialised LLM reviewer nodes."""

    def __init__(
        self,
        llm: LLMClient | None = None,
        severity_floor: Severity = Severity.LOW,
        enabled: bool = True,
    ) -> None:
        self._llm = llm
        self.severity_floor = severity_floor
        self.enabled = enabled

    @property
    def llm(self) -> LLMClient:
        """Build the client on first use, so constructing a node needs no credentials."""
        if self._llm is None:
            self._llm = LLMClient()
        return self._llm

    def execute(
        self,
        state: ReviewState,
        *,
        system_prompt: str,
        output_key: str,
    ) -> dict[str, Any]:
        if not self.enabled:
            logger.info("agent.disabled", agent=output_key)
            return {output_key: []}

        request = state.get("request")
        current_file = state.get("current_file")

        prompt = PromptBuilder.build(request, current_file)

        try:
            try:
                finding_list: FindingList = self.llm.generate_structured(
                    system_prompt=system_prompt,
                    user_prompt=prompt,
                    schema=FindingList,
                )
                comments = finding_list.comments
            except Exception as exc:
                logger.info("agent.structured_output_fallback", agent=output_key, error=str(exc))
                raw_response = self.llm.generate(
                    system_prompt=system_prompt,
                    user_prompt=prompt,
                )
                comments = ReviewParser.parse_comments(raw_response)
        except Exception as exc:
            logger.exception("agent.failed", agent=output_key, error=str(exc))
            return {output_key: [], "errors": [f"{output_key}: {exc}"]}

        floor_weight = SEVERITY_ORDER.get(self.severity_floor, 2)
        filtered_comments: list[ReviewComment] = [
            c for c in comments if SEVERITY_ORDER.get(c.severity, 2) <= floor_weight
        ]

        logger.info(
            "agent.completed",
            agent=output_key,
            total_findings=len(comments),
            accepted_findings=len(filtered_comments),
        )
        return {output_key: filtered_comments}
