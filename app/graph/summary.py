from typing import Any

import structlog

from app.graph.base_node import BaseNode
from app.graph.parser import ReviewParser
from app.graph.state import ReviewState
from app.prompts.summary_prompt import SUMMARY_PROMPT
from app.utils.summary_prompt_builder import SummaryPromptBuilder

logger = structlog.get_logger(__name__)

_node = BaseNode()


def summary_node(state: ReviewState) -> dict[str, Any]:
    """Generate the executive pull request summary from the aggregated findings."""
    prompt = SummaryPromptBuilder.build(state)

    try:
        response = _node.llm.generate(
            system_prompt=SUMMARY_PROMPT,
            user_prompt=prompt,
        )
    except Exception as exc:
        logger.exception("summary.failed", error=str(exc))
        return {
            "summary": "Summary unavailable: the summary model call failed.",
            "errors": [f"summary: {exc}"],
        }

    return {"summary": ReviewParser.parse_summary(response)}
