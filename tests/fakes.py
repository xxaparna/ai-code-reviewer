"""Test doubles: exercise the full pipeline with no network and no LLM spend."""

from typing import Any

from app.errors import GitHubRejectedError
from app.models.review_file import ReviewFile


class FakeLLMClient:
    """Stands in for :class:`app.services.llm_client.LLMClient`.

    Returns canned JSON chosen by the agent persona in the system prompt, and
    records every call so tests can assert on fan-out.
    """

    def __init__(self, *, fail_agents: set[str] | None = None) -> None:
        self.calls: list[dict[str, str]] = []
        self.fail_agents = fail_agents or set()

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
        for marker in self._RESPONSES:
            if marker in system_prompt:
                return marker
        return "unknown"

    def generate(self, *, system_prompt: str, user_prompt: str) -> str:
        agent = self._agent_for(system_prompt)
        self.calls.append({"agent": agent, "user_prompt": user_prompt})

        if agent in self.fail_agents:
            raise RuntimeError(f"simulated upstream failure for {agent}")

        return self._RESPONSES.get(agent, '{"comments": []}')


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
                    "patch": "@@ -1,4 +1,6 @@\n+import os\n+print(token)\n-import sys\n",
                    "additions": 2,
                    "deletions": 1,
                    "changes": 3,
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
