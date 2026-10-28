"""Phase 2 definition of done: per-file map-reduce with Send.

A 40-file PR produces one LLM call per (file x enabled agent), each prompt
containing exactly one file's diff, and the reducers merge every branch.
"""

import json
import re

from app.agents.registry import AgentRegistry, default_registry
from app.graph.builder import fan_out
from app.models.review_file import ReviewFile
from app.review_config import ReviewConfig
from app.services.review_service import ReviewService

from tests.conftest import make_review_request
from tests.fakes import FakeLLMClient

FILES = 40
SUMMARY = "Principal Software Engineer"
FILE_HEADER = re.compile(r"--- Target File: (\S+) ---")


def _request(n: int = FILES):  # type: ignore[no-untyped-def]
    return make_review_request(
        files=[
            ReviewFile(
                filename=f"pkg/mod_{i}.py",
                status="modified",
                patch=f"@@ -1 +1,2 @@\n+value_{i} = {i}\n+other_{i} = {i}\n",
                language="Python",
            )
            for i in range(n)
        ]
    )


class EchoLLM(FakeLLMClient):
    """Reports one finding per call, on the file named in the prompt."""

    def _respond(self, system_prompt: str, user_prompt: str, mode: str) -> str:
        super()._respond(system_prompt, user_prompt, mode)
        if SUMMARY in system_prompt:
            return '{"summary": "ok"}'
        match = FILE_HEADER.search(user_prompt)
        agent = self._agent_for(system_prompt)
        filename = match.group(1) if match else "?"
        finding = {
            "file": filename,
            "line": 1,
            "severity": "HIGH",
            "message": f"{agent} in {filename}",
        }
        return json.dumps({"comments": [finding]})


def test_fan_out_width_is_files_times_agents() -> None:
    request = _request()
    sends = fan_out({"request": request, "files": request.files, "agents": ["bug", "security"]})
    assert len(sends) == FILES * 2
    assert {(s.arg["file"].filename, s.arg["agent"]) for s in sends} == {
        (f.filename, a) for f in request.files for a in ("bug", "security")
    }


def test_forty_file_pr_one_call_per_file_and_agent() -> None:
    agents = len(default_registry().enabled)
    llm = EchoLLM()
    service = ReviewService(llm=llm, config=ReviewConfig(max_findings_per_review=10_000))

    review = service.review(_request())

    reviewer_calls = [c for c in llm.calls if c["agent"] != SUMMARY]
    assert len(reviewer_calls) == FILES * agents
    assert len([c for c in llm.calls if c["agent"] == SUMMARY]) == 1

    # Each prompt carries exactly one file's diff.
    for call in reviewer_calls:
        assert len(FILE_HEADER.findall(call["user_prompt"])) == 1
        (name,) = FILE_HEADER.findall(call["user_prompt"])
        assert call["user_prompt"].count("+value_") == 1
        assert f"value_{name.split('_')[1].split('.')[0]}" in call["user_prompt"]

    # Every (file, agent) pair was reviewed exactly once.
    pairs = {(c["agent"], FILE_HEADER.findall(c["user_prompt"])[0]) for c in reviewer_calls}
    assert len(pairs) == FILES * agents

    # Reducers merged every branch: one agent_run per pair, findings from all files.
    meta = review.metadata
    assert sum(s["runs"] for s in meta["agent_stats"].values()) == FILES * agents
    assert meta["raw_findings"] == FILES * agents
    assert {c.file for c in review.comments} == {f"pkg/mod_{i}.py" for i in range(FILES)}
    assert meta["complete"] is True
    assert meta["llm_calls"] == FILES * agents + 1


def test_finding_caps_apply_after_fan_in() -> None:
    llm = EchoLLM()
    service = ReviewService(
        llm=llm, config=ReviewConfig(max_findings_per_file=1, max_findings_per_review=7)
    )
    review = service.review(_request(10))
    assert len(review.comments) == 7
    assert review.metadata["capped_findings_dropped"] > 0


def test_disabled_agent_never_runs() -> None:
    base = default_registry()
    registry = AgentRegistry(
        tuple(a.model_copy(update={"enabled": a.name != "style"}) for a in base)
    )
    llm = EchoLLM()
    ReviewService(llm=llm, registry=registry).review(_request(3))
    assert "Senior Code Reviewer" not in {c["agent"] for c in llm.calls}
    assert len([c for c in llm.calls if c["agent"] != SUMMARY]) == 3 * (len(base) - 1)
