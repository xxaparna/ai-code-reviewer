"""Graph state. Every key written by parallel branches has a reducer (plan 2.2).

The reviewer node runs once per (file, agent) pair, all in the same superstep,
so ``findings``, ``agent_runs`` and ``errors`` each receive N writes per step
and must be merged with ``operator.add``; a plain key would raise
``InvalidUpdateError`` the moment two branches report.
"""

import operator
from typing import Annotated, Any, Literal, Protocol, Required, TypedDict

from app.models.review_comment import ReviewComment
from app.models.review_file import ReviewFile
from app.models.review_request import ReviewRequest


class AgentRun(TypedDict):
    """One (file, agent) reviewer call: what came back and what was kept."""

    agent: str
    file: str
    status: Literal["ok", "failed"]
    model_tier: str
    prompt_version: str
    duration_ms: int
    used_fallback: bool
    raw_findings: int
    accepted: int
    dropped_invalid: int
    dropped_hallucinated: int
    dropped_below_floor: int
    dropped_over_cap: int
    error: str | None


class ReviewTask(TypedDict):
    """The private input of one reviewer branch, delivered by ``Send``."""

    request: ReviewRequest
    file: ReviewFile
    agent: str


class ReviewState(TypedDict, total=False):
    request: Required[ReviewRequest]
    # The files that survived filtering and the budget - the fan-out domain.
    files: Required[list[ReviewFile]]
    # Names of the agents to run on every file.
    agents: Required[list[str]]

    # --- written by parallel reviewer branches: reducers required --------
    findings: Annotated[list[ReviewComment], operator.add]
    agent_runs: Annotated[list[AgentRun], operator.add]
    # A non-empty list means the review is incomplete.
    errors: Annotated[list[str], operator.add]

    # --- written once, after the fan-in -----------------------------------
    merged_comments: list[ReviewComment]
    merge_stats: dict[str, int]
    summary: str
    metadata: dict[str, Any]


class StateNode(Protocol):
    """A graph node over the shared state. LangGraph passes the state as ``state``."""

    def __call__(self, state: ReviewState) -> dict[str, Any]: ...
