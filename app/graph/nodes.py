from typing import Any

from app.graph.base_node import BaseNode
from app.graph.merge import merge_node
from app.graph.state import ReviewState
from app.graph.summary import summary_node
from app.prompts.architecture_prompt import ARCHITECTURE_PROMPT
from app.prompts.bug_prompt import BUG_PROMPT
from app.prompts.performance_prompt import PERFORMANCE_PROMPT
from app.prompts.security_prompt import SECURITY_PROMPT
from app.prompts.style_prompt import STYLE_PROMPT

__all__ = [
    "architecture_node",
    "bug_node",
    "merge_node",
    "performance_node",
    "security_node",
    "style_node",
    "summary_node",
]

# Phase 2 replaces these five near-identical functions with one node
# parameterised by an entry in the agent registry.
_node = BaseNode()


def bug_node(state: ReviewState) -> dict[str, Any]:
    return _node.execute(state, system_prompt=BUG_PROMPT, output_key="bug_comments")


def security_node(state: ReviewState) -> dict[str, Any]:
    return _node.execute(state, system_prompt=SECURITY_PROMPT, output_key="security_comments")


def style_node(state: ReviewState) -> dict[str, Any]:
    return _node.execute(state, system_prompt=STYLE_PROMPT, output_key="style_comments")


def performance_node(state: ReviewState) -> dict[str, Any]:
    return _node.execute(state, system_prompt=PERFORMANCE_PROMPT, output_key="performance_comments")


def architecture_node(state: ReviewState) -> dict[str, Any]:
    return _node.execute(
        state, system_prompt=ARCHITECTURE_PROMPT, output_key="architecture_comments"
    )
