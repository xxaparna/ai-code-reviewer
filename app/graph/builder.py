"""The review graph: per-file, per-agent map-reduce (plan 2.1).

    START --Send(file, agent) x N--> review --> merge --> summary --> END

``fan_out`` runs at START and emits one ``Send`` per (reviewable file x
selected agent), so the width is known only at runtime. All branches land in
the same superstep, so ``merge`` runs exactly once, after every branch has
written through the ``operator.add`` reducers. Concurrency is bounded by the
``max_concurrency`` run config (see ``ReviewService``), not by the fan-out.
"""

from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Send

from app.agents.registry import AgentRegistry, default_registry
from app.graph.merge import make_merge_node
from app.graph.reviewer import LLMFactory, ReviewerNode
from app.graph.state import ReviewState, ReviewTask
from app.graph.summary import make_summary_node
from app.review_config import ReviewConfig
from app.services.llm_client import LLMPool

REVIEW_NODE = "review"


def fan_out(state: ReviewState) -> list[Send]:
    """One branch per (file, agent). Each branch sees exactly one file."""
    return [
        Send(REVIEW_NODE, ReviewTask(request=state["request"], file=file, agent=agent))
        for file in state["files"]
        for agent in state["agents"]
    ]


def build_graph(
    *,
    llm_for: LLMFactory | None = None,
    registry: AgentRegistry | None = None,
    config: ReviewConfig | None = None,
) -> CompiledStateGraph[ReviewState, None, Any, Any]:
    """Compile the graph. Building needs no credentials: model clients are lazy."""
    registry = registry or default_registry()
    config = config or ReviewConfig()
    llm_for = llm_for or LLMPool()
    reviewer = ReviewerNode(registry, llm_for, config)

    def review(state: ReviewTask) -> dict[str, Any]:
        return reviewer(state)

    builder = StateGraph(ReviewState)
    builder.add_node(REVIEW_NODE, review, input_schema=ReviewTask)
    builder.add_node("merge", make_merge_node(config))
    builder.add_node("summary", make_summary_node(llm_for))

    builder.add_conditional_edges(START, fan_out, [REVIEW_NODE])
    builder.add_edge(REVIEW_NODE, "merge")
    builder.add_edge("merge", "summary")
    builder.add_edge("summary", END)
    return builder.compile()
