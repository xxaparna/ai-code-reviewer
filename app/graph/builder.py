from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.graph.nodes import (
    architecture_node,
    bug_node,
    merge_node,
    performance_node,
    security_node,
    style_node,
    summary_node,
)
from app.graph.state import ReviewState


def build_graph() -> CompiledStateGraph[ReviewState, None, Any, Any]:
    """
    Builds and compiles the multi-agent LangGraph workflow.
    Executes review nodes in parallel from START, fanning in to merge -> summary -> END.
    """
    builder = StateGraph(ReviewState)

    # Add Reviewer Nodes
    builder.add_node("bug", bug_node)
    builder.add_node("security", security_node)
    builder.add_node("style", style_node)
    builder.add_node("performance", performance_node)
    builder.add_node("architecture", architecture_node)

    # Add Merging & Summary Nodes
    builder.add_node("merge", merge_node)
    builder.add_node("summary", summary_node)

    # Parallel Fan-Out from START to reviewer nodes
    builder.add_edge(START, "bug")
    builder.add_edge(START, "security")
    builder.add_edge(START, "style")
    builder.add_edge(START, "performance")
    builder.add_edge(START, "architecture")

    # Fan-In from reviewer nodes to merge
    builder.add_edge("bug", "merge")
    builder.add_edge("security", "merge")
    builder.add_edge("style", "merge")
    builder.add_edge("performance", "merge")
    builder.add_edge("architecture", "merge")

    # Final Summary and Execution Termination
    builder.add_edge("merge", "summary")
    builder.add_edge("summary", END)

    return builder.compile()
