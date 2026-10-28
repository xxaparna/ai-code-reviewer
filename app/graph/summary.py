from collections.abc import Callable
from typing import Any

import structlog

from app.graph.parser import ReviewParser
from app.graph.state import ReviewState, StateNode
from app.prompts.summary_prompt import SUMMARY_PROMPT
from app.services.llm_client import ReviewLLM
from app.utils.summary_prompt_builder import SummaryPromptBuilder

logger = structlog.get_logger(__name__)


def make_summary_node(llm_for: Callable[[str], ReviewLLM]) -> StateNode:
    """Executive summary of the merged findings: one call, on the strong tier."""

    def summary_node(state: ReviewState) -> dict[str, Any]:
        prompt = SummaryPromptBuilder.build(state)
        try:
            response = llm_for("strong").generate(system_prompt=SUMMARY_PROMPT, user_prompt=prompt)
        except Exception as exc:
            logger.warning("summary.failed", error_type=type(exc).__name__)
            return {
                "summary": "Summary unavailable: the summary model call failed.",
                "errors": [f"summary: {str(exc)[:200]}"],
            }
        return {"summary": ReviewParser.parse_summary(response)}

    return summary_node
