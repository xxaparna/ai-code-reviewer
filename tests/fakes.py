"""Test doubles: exercise the full pipeline with no network and no LLM spend."""

import json
import threading
from typing import Any, TypeVar

from app.errors import GitHubRejectedError, StructuredOutputError
from app.models.review_file import ReviewFile
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class FakeLLMClient:
    """Stands in for :class:`app.services.llm_client.LLMClient`.

    Returns canned JSON chosen by the agent persona in the system prompt, and
    records every call so tests can assert on fan-out.
    """

    def __init__(
        self,
        *,
        fail_agents: set[str] | None = None,
        responses: dict[str, str] | None = None,
        fail_with: Exception | None = None,
    ) -> None:
        self.calls: list[dict[str, str]] = []
        self.fail_agents = fail_agents or set()
        self.fail_with = fail_with
        self._responses = {**self._RESPONSES, **(responses or {})}
        self._lock = threading.Lock()

    # Keyed by a distinctive phrase in each agent's system prompt.
    _RESPONSES: dict[str, str] = {
        "Senior Software Engineer": """
        {"comments": [{"file": "app/example.py", "line": 12, "severity": "HIGH",
          "category": "BUG", "message": "Unchecked None dereference."}]}
        """,
        "Security Engineer": """
        {"comments": [{"file": "app/example.py", "line": 12, "severity": "HIGH",
          "category": "SECURITY", "message": "Unchecked None dereference."},
         {"file": "app/example.py", "line": 30, "severity": "MEDIUM",
          "category": "SECURITY", "message": "Token logged in plaintext."}]}
        """,
        "Senior Code Reviewer": """
        ```json
        {"comments": [{"file": "app/example.py", "line": 5, "severity": "LOW",
          "category": "STYLE", "message": "Unused import."}]}
        ```
        """,
        "Performance Engineer": '{"comments": []}',
        "Software Architect": """
        Here is my review:
        [{"file": "app/example.py", "line": null, "severity": "MEDIUM",
          "category": "ARCHITECTURE", "message": "Service reaches into the data layer."}]
        """,
        "Principal Software Engineer": '{"summary": "One high-severity bug and two minor issues."}',
    }

    def _agent_for(self, system_prompt: str) -> str:
        for marker in self._responses:
            if marker in system_prompt:
                return marker
        return "unknown"

    def _respond(self, system_prompt: str, user_prompt: str, mode: str) -> str:
        agent = self._agent_for(system_prompt)
        with self._lock:
            self.calls.append({"agent": agent, "user_prompt": user_prompt, "mode": mode})
        if agent in self.fail_agents:
            raise self.fail_with or RuntimeError(f"simulated upstream failure for {agent}")
        return self._responses.get(agent, '{"comments": []}')

    def generate(self, *, system_prompt: str, user_prompt: str) -> str:
        return self._respond(system_prompt, user_prompt, "text")

    def generate_structured(self, *, system_prompt: str, user_prompt: str, schema: type[T]) -> T:
        """Like JSON mode: only a bare JSON object is accepted, anything else fails."""
        text = self._respond(system_prompt, user_prompt, "structured")
        try:
            return schema.model_validate(json.loads(text))
        except ValueError as exc:
            raise StructuredOutputError("not valid structured output") from exc


# One hunk: new-file lines 1-40 are all added, so findings on 5, 12 and 30 are grounded.
DEFAULT_PATCH = "@@ -1,1 +1,40 @@\n-import sys\n" + "\n".join(
    f"+line_{i} = {i}" for i in range(1, 41)
)


class FakeGitHubService:
    """Serves a fixed pull request payload; records anything published."""

    def __init__(
        self,
        *,
        files: list[dict[str, Any]] | None = None,
        pr_overrides: dict[str, Any] | None = None,
        check_runs: list[dict[str, Any]] | None = None,
        combined_status: dict[str, Any] | None = None,
        reject_inline: bool = False,
    ) -> None:
        self.published: list[dict[str, Any]] = []
        self.merged: list[dict[str, Any]] = []
        self.pr_overrides = pr_overrides or {}
        self.check_runs = check_runs or []
        self.combined_status = combined_status or {"state": "pending", "statuses": []}
        self.reject_inline = reject_inline
        self.files = (
            files
            if files is not None
            else [
                {
                    "filename": "app/example.py",
                    "status": "modified",
                    "patch": DEFAULT_PATCH,
                    "additions": 40,
                    "deletions": 1,
                    "changes": 41,
                }
            ]
        )

    def get_pull_request(self, owner: str, repo: str, pull_number: int) -> dict[str, Any]:
        return {
            "title": "Add token handling",
            "body": "Adds token handling to the example module.",
            "number": pull_number,
            "state": "open",
            "draft": False,
            "merged": False,
            "mergeable": True,
            "html_url": f"https://github.com/{owner}/{repo}/pull/{pull_number}",
            "user": {"login": "octocat"},
            "head": {"sha": "deadbeef", "ref": "feature"},
            "base": {"ref": "main", "repo": {"name": repo, "owner": {"login": owner}}},
            **self.pr_overrides,
        }

    def get_pull_request_files(
        self, owner: str, repo: str, pull_number: int
    ) -> list[dict[str, Any]]:
        return self.files

    def create_pull_request_review(
        self,
        owner: str,
        repo: str,
        pull_number: int,
        body: str,
        event: str = "COMMENT",
        comments: list[dict[str, Any]] | None = None,
        commit_id: str | None = None,
    ) -> dict[str, Any]:
        if self.reject_inline and comments:
            raise GitHubRejectedError(
                "GitHub rejected the request: line must be part of the diff", 422
            )
        self.published.append(
            {
                "owner": owner,
                "repo": repo,
                "pull_number": pull_number,
                "body": body,
                "event": event,
                "comments": comments,
                "commit_id": commit_id,
            }
        )
        return {"id": 1, "state": "COMMENTED", "html_url": "https://github.com/r/pull/7#review-1"}

    def get_check_runs(self, owner: str, repo: str, ref: str) -> list[dict[str, Any]]:
        return self.check_runs

    def get_combined_status(self, owner: str, repo: str, ref: str) -> dict[str, Any]:
        return self.combined_status

    def merge_pull_request(
        self,
        owner: str,
        repo: str,
        pull_number: int,
        *,
        sha: str,
        merge_method: str,
        commit_title: str | None = None,
    ) -> dict[str, Any]:
        self.merged.append({"sha": sha, "merge_method": merge_method, "commit_title": commit_title})
        return {"sha": "mergedsha", "merged": True}


def make_file(filename: str = "app/example.py", **overrides: Any) -> ReviewFile:
    defaults: dict[str, Any] = {
        "filename": filename,
        "status": "modified",
        "patch": "@@ -1,2 +1,3 @@\n+import os\n",
        "language": "Python",
        "additions": 1,
        "deletions": 0,
        "changes": 1,
    }
    return ReviewFile(**{**defaults, **overrides})
