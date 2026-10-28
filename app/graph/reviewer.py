"""The one generic reviewer node (plan 2.3), run once per (file, agent).

Behaviour comes from the agent's registry entry: prompt, model tier, severity
floor and per-file cap. The node sees exactly one file's diff, and keeps only
findings that point inside that file's diff hunks.
"""

import time
from collections.abc import Callable
from typing import Any

import structlog

from app.agents.registry import AgentConfig, AgentRegistry
from app.errors import LLMUnavailableError, StructuredOutputError, UpstreamRateLimitError
from app.graph.parser import ReviewParser, validate_items
from app.graph.state import AgentRun, ReviewTask
from app.models.review_comment import RawFindingList, ReviewComment
from app.review_config import ReviewConfig
from app.services.llm_client import ReviewLLM
from app.utils.diff_validator import filter_hallucinations
from app.utils.prompt_builder import PromptBuilder

logger = structlog.get_logger(__name__)

LLMFactory = Callable[[str], ReviewLLM]
MAX_ERROR_CHARS = 200


def _call_model(
    llm: ReviewLLM, agent: AgentConfig, prompt: str, config: ReviewConfig
) -> tuple[list[Any], bool]:
    """Structured output first; the tolerant text parser only behind the flag.

    Transient failures (rate limits, timeouts) are *not* retried as text: the
    client already retried them, and a second request would only add load.
    """
    if config.structured_output:
        try:
            result = llm.generate_structured(
                system_prompt=agent.system_prompt, user_prompt=prompt, schema=RawFindingList
            )
            return result.as_items(), False
        except (UpstreamRateLimitError, LLMUnavailableError):
            raise
        except StructuredOutputError as exc:
            if not config.tolerant_fallback:
                raise
            logger.info("agent.structured_output_fallback", agent=agent.name, error=str(exc))

    raw = llm.generate(system_prompt=agent.system_prompt, user_prompt=prompt)
    return ReviewParser.parse_items(raw), config.structured_output


class ReviewerNode:
    def __init__(self, registry: AgentRegistry, llm_for: LLMFactory, config: ReviewConfig) -> None:
        self._registry = registry
        self._llm_for = llm_for
        self._config = config

    def __call__(self, task: ReviewTask) -> dict[str, Any]:
        agent = self._registry.get(task["agent"])
        file = task["file"]
        started = time.perf_counter()
        run: AgentRun = {
            "agent": agent.name,
            "file": file.filename,
            "status": "ok",
            "model_tier": agent.model_tier,
            "prompt_version": agent.prompt_version,
            "duration_ms": 0,
            "used_fallback": False,
            "raw_findings": 0,
            "accepted": 0,
            "dropped_invalid": 0,
            "dropped_hallucinated": 0,
            "dropped_below_floor": 0,
            "dropped_over_cap": 0,
            "error": None,
        }
        log = logger.bind(agent=agent.name, file=file.filename)

        try:
            prompt = PromptBuilder.build(task["request"], file)
            items, run["used_fallback"] = _call_model(
                self._llm_for(agent.model_tier), agent, prompt, self._config
            )
        except Exception as exc:
            message = str(exc)[:MAX_ERROR_CHARS] or type(exc).__name__
            run["status"] = "failed"
            run["error"] = message
            run["duration_ms"] = int((time.perf_counter() - started) * 1000)
            log.warning("agent.failed", error_type=type(exc).__name__)
            return {
                "findings": [],
                "agent_runs": [run],
                "errors": [f"{agent.name}: {file.filename}: {message}"],
            }

        findings = self._accept(items, agent, file.filename, task, run)
        run["accepted"] = len(findings)
        run["duration_ms"] = int((time.perf_counter() - started) * 1000)
        log.info("agent.completed", **{k: run[k] for k in ("raw_findings", "accepted")})  # type: ignore[literal-required]
        return {"findings": findings, "agent_runs": [run]}

    def _accept(
        self,
        items: list[Any],
        agent: AgentConfig,
        filename: str,
        task: ReviewTask,
        run: AgentRun,
    ) -> list[ReviewComment]:
        run["raw_findings"] = len(items)
        # A missing category means "this agent's category"; a wrong one is rejected.
        prepared = [
            {**item, "category": item.get("category") or agent.category.value}
            if isinstance(item, dict)
            else item
            for item in items
        ]
        comments, run["dropped_invalid"] = validate_items(
            prepared, default_file=filename, agent=agent.name
        )

        # The agent saw one file: any other path, or a line outside its hunks, is invented.
        in_file = [c for c in comments if c.file == filename]
        grounded = filter_hallucinations(in_file, [task["file"]])
        run["dropped_hallucinated"] = len(comments) - len(grounded)

        kept = [c for c in grounded if agent.accepts(c.severity)]
        run["dropped_below_floor"] = len(grounded) - len(kept)

        kept.sort(key=lambda c: c.severity.rank)
        capped = kept[: agent.max_findings_per_file]
        run["dropped_over_cap"] = len(kept) - len(capped)
        return capped
